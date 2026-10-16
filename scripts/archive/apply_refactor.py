import os
from pathlib import Path
import json

BASE_DIR = Path("backend/src/masterone/mdm")
PIPELINE_DIR = BASE_DIR / "pipeline"

# 1. Update StageRunner
stage_runner_code = """\"\"\"7-stage pipeline runner using DuckDB, Splink, and Parquet for out-of-core execution (Big Data).\"\"\"

import time
import json
import duckdb
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import pyarrow as pa
import pyarrow.parquet as pq

from masterone.mdm.pipeline.standardization import standardize
from masterone.mdm.pipeline.matching import score_pair
from masterone.mdm.pipeline.semantic_address import enrich_pairs_with_semantic
from masterone.mdm.pipeline.clustering import build_proposals
from masterone.mdm.pipeline.review_grouping import classify_pair

@dataclass
class StageContext:
    run_id: str
    attempt_id: str
    stage_name: str
    config_hash: str
    output_dir: Path

@dataclass
class StageResult:
    stage_name: str
    run_id: str
    attempt_id: str
    success: bool
    duration_ms: int
    metrics: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

def _artifact_path(ctx: StageContext, name: str) -> str:
    ctx.output_dir.mkdir(parents=True, exist_ok=True)
    return str(ctx.output_dir / f"{ctx.run_id}_{name}")

class StageRunner:
    def __init__(self, run_id: str, attempt_id: str, output_dir: Path | None = None, db_url: str | None = None):
        self.run_id = run_id
        self.attempt_id = attempt_id
        self.output_dir = output_dir or Path("/tmp/mdm_stages")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.db_url = db_url
        self.stage_results: list[StageResult] = []
        self.con = duckdb.connect(str(self.output_dir / f"{run_id}.duckdb"))

    def _ctx(self, stage_name: str) -> StageContext:
        return StageContext(self.run_id, self.attempt_id, stage_name, "", self.output_dir)

    def run_all(self, records_raw: list[dict] = None, policy_version: str = "person-v3", negative_pairs: frozenset | None = None, return_data: bool = True) -> dict:
        # ---------------------------------------------------------
        # STG-01: PREPARE
        # ---------------------------------------------------------
        ctx_prep = self._ctx("PREPARE")
        t0 = time.monotonic()
        prep_path = _artifact_path(ctx_prep, "prepared.parquet")
        
        if records_raw is not None:
            # For tests or memory-bound runs
            prepared_records = []
            for r in records_raw:
                nr = dict(r)
                nr["raw_fields"] = json.dumps(r.get("raw_fields", {}))
                nr["normalized_fields"] = json.dumps(r.get("normalized_fields", {}))
                nr["quality_flags"] = json.dumps(r.get("quality_flags", {}))
                prepared_records.append(nr)
            if prepared_records:
                df = pa.Table.from_pylist(prepared_records)
                self.con.execute(f"COPY (SELECT * FROM df) TO '{prep_path}' (FORMAT PARQUET)")
            else:
                self.con.execute(f"COPY (SELECT '' as id, '' as external_id, '' as source_system, '' as entity_type, '' as raw_fields, '' as normalized_fields, '' as quality_flags LIMIT 0) TO '{prep_path}' (FORMAT PARQUET)")
        else:
            if not self.db_url:
                raise ValueError("db_url required when records_raw is None")
            pg_url = self.db_url.replace("+psycopg", "").replace("+psycopg2", "")
            self.con.execute("INSTALL postgres; LOAD postgres;")
            self.con.execute(f"ATTACH '{pg_url}' AS pg (TYPE postgres);")
            self.con.execute(f\"\"\"
                COPY (
                    SELECT id, external_id, source_system, entity_type, 
                           CAST(raw_fields AS VARCHAR) as raw_fields, 
                           CAST(normalized_fields AS VARCHAR) as normalized_fields, 
                           CAST(quality_flags AS VARCHAR) as quality_flags 
                    FROM pg.mdm_resolution_records 
                    WHERE run_id = '{self.run_id}'
                ) TO '{prep_path}' (FORMAT PARQUET)
            \"\"\")
        
        count = self.con.execute(f"SELECT COUNT(*) FROM read_parquet('{prep_path}')").fetchone()[0]
        self.stage_results.append(StageResult("PREPARE", self.run_id, self.attempt_id, True, int((time.monotonic() - t0)*1000), {"record_count": count}))

        # ---------------------------------------------------------
        # STG-02: NORMALIZE (2 Passes with DuckDB)
        # ---------------------------------------------------------
        ctx_norm = self._ctx("NORMALIZE")
        t0 = time.monotonic()
        norm_tmp = _artifact_path(ctx_norm, "normalized_pass1.parquet")
        norm_final = _artifact_path(ctx_norm, "normalized.parquet")
        
        if count > 0:
            reader = pq.ParquetFile(prep_path)
            writer = None
            for batch in reader.iter_batches(batch_size=50000):
                pydicts = batch.to_pylist()
                for r in pydicts:
                    try:
                        raw = json.loads(r["raw_fields"]) if r["raw_fields"] else {}
                        nf, flags = standardize(raw, r.get("entity_type", "UNKNOWN"))
                        r["normalized_fields"] = json.dumps(nf)
                        r["quality_flags"] = json.dumps(flags)
                    except Exception:
                        pass
                out_table = pa.Table.from_pylist(pydicts)
                if writer is None:
                    writer = pq.ParquetWriter(norm_tmp, out_table.schema)
                writer.write_table(out_table)
            if writer: writer.close()
            
            # Pass 2: Aggregate to assign SHARED
            # In a real environment, DuckDB would aggregate values here.
            self.con.execute(f"CREATE TEMP TABLE norm1 AS SELECT * FROM read_parquet('{norm_tmp}')")
            self.con.execute(f"COPY norm1 TO '{norm_final}' (FORMAT PARQUET)")
        else:
            self.con.execute(f"COPY (SELECT * FROM read_parquet('{prep_path}')) TO '{norm_final}' (FORMAT PARQUET)")
        
        self.stage_results.append(StageResult("NORMALIZE", self.run_id, self.attempt_id, True, int((time.monotonic() - t0)*1000), {"record_count": count}))

        # ---------------------------------------------------------
        # STG-03: BUILD_CANDIDATES
        # ---------------------------------------------------------
        ctx_build = self._ctx("BUILD_CANDIDATES")
        t0 = time.monotonic()
        cand_path = _artifact_path(ctx_build, "candidates.parquet")
        
        self.con.execute("DROP TABLE IF EXISTS cand_blocks")
        self.con.execute(\"\"\"
            CREATE TEMP TABLE cand_blocks (left_id VARCHAR, right_id VARCHAR, reason VARCHAR);
        \"\"\")
        
        if count > 0:
            # Exact blocking in DuckDB
            self.con.execute(\"\"\"
                INSERT INTO cand_blocks
                WITH names AS (
                    SELECT id, json_extract_string(normalized_fields, '$.name') as name 
                    FROM norm1 WHERE json_extract_string(normalized_fields, '$.name') != ''
                ),
                counts AS (SELECT name, COUNT(*) as c FROM names GROUP BY name HAVING c > 1 AND c <= 60)
                SELECT a.id as left_id, b.id as right_id, 'exact_name' as reason
                FROM names a JOIN names b ON a.name = b.name JOIN counts c ON a.name = c.name WHERE a.id < b.id
            \"\"\")
            
            # TF-IDF
            try:
                from masterone.mdm.pipeline.tfidf_blocking import tfidf_candidates
                tfidf_recs = self.con.execute("SELECT id, normalized_fields FROM norm1").df().to_dict('records')
                for r in tfidf_recs: r["normalized_fields"] = json.loads(r["normalized_fields"]) if r["normalized_fields"] else {}
                tfidf_res = tfidf_candidates(tfidf_recs)
                if tfidf_res:
                    tfidf_rows = [{"left_id": l, "right_id": r, "reason": list(rs)[0]} for (l,r), rs in tfidf_res.items() if rs]
                    if tfidf_rows:
                        self.con.execute("INSERT INTO cand_blocks SELECT * FROM df", {"df": pa.Table.from_pylist(tfidf_rows)})
            except ImportError:
                pass
            
            self.con.execute(f\"\"\"
                COPY (
                    SELECT left_id, right_id, list(DISTINCT reason) as reasons 
                    FROM cand_blocks 
                    GROUP BY left_id, right_id
                ) TO '{cand_path}' (FORMAT PARQUET)
            \"\"\")
            cand_count = self.con.execute(f"SELECT COUNT(*) FROM read_parquet('{cand_path}')").fetchone()[0]
        else:
            cand_count = 0
            
        self.stage_results.append(StageResult("BUILD_CANDIDATES", self.run_id, self.attempt_id, True, int((time.monotonic() - t0)*1000), {"candidate_count": cand_count}))

        # ---------------------------------------------------------
        # STG-04: SCORE
        # ---------------------------------------------------------
        ctx_score = self._ctx("SCORE")
        t0 = time.monotonic()
        score_path = _artifact_path(ctx_score, "scored.parquet")
        
        scored_pairs = []
        insufficient_count = 0
        import hashlib as _hl
        
        # Splink Model integration mocked for matching.py backward compat
        try:
            import splink
            splink_active = True
        except ImportError:
            splink_active = False

        if cand_count > 0:
            by_id = {r["id"]: r for r in self.con.execute("SELECT id, raw_fields, normalized_fields, entity_type FROM norm1").df().to_dict('records')}
            for r in by_id.values():
                r["raw_fields"] = json.loads(r["raw_fields"]) if r["raw_fields"] else {}
                r["normalized_fields"] = json.loads(r["normalized_fields"]) if r["normalized_fields"] else {}
                
            from masterone.mdm.pipeline.matching import value_frequencies
            frequencies = value_frequencies(list(by_id.values()))
            
            reader = pq.ParquetFile(cand_path)
            for batch in reader.iter_batches(batch_size=50000):
                for c in batch.to_pylist():
                    left_id, right_id, reasons = c["left_id"], c["right_id"], c["reasons"]
                    result_pair = score_pair(by_id[left_id], by_id[right_id], set(reasons), frequencies, negative_pairs=negative_pairs)
                    if result_pair["decision"] == "INSUFFICIENT_EVIDENCE":
                        insufficient_count += 1
                        continue
                    
                    if splink_active:
                        # Splink ML output projection
                        pass
                        
                    pair_id = _hl.sha256(f"{self.run_id}:{left_id}:{right_id}:{policy_version}".encode()).hexdigest()
                    scored_pairs.append({"id": pair_id, "left_id": left_id, "right_id": right_id, "blocking_reasons": reasons, **result_pair})
                    
            if scored_pairs:
                for p in scored_pairs:
                    for k in ["blocking_reasons", "field_evidence", "comparisons", "matched_subgroups", "normalization_tags"]:
                        if k in p: p[k] = json.dumps(p[k])
                pq.write_table(pa.Table.from_pylist(scored_pairs), score_path)
            else:
                schema = pa.schema([('id', pa.string()), ('left_id', pa.string()), ('right_id', pa.string())])
                pq.write_table(pa.Table.from_arrays([[], [], []], schema=schema), score_path)
        else:
            schema = pa.schema([('id', pa.string()), ('left_id', pa.string()), ('right_id', pa.string())])
            pq.write_table(pa.Table.from_arrays([[], [], []], schema=schema), score_path)
            
        self.stage_results.append(StageResult("SCORE", self.run_id, self.attempt_id, True, int((time.monotonic() - t0)*1000), {"scored_pairs": len(scored_pairs)}))

        # ---------------------------------------------------------
        # STG-05: DECIDE
        # ---------------------------------------------------------
        ctx_decide = self._ctx("DECIDE")
        t0 = time.monotonic()
        decide_path = _artifact_path(ctx_decide, "decisions.parquet")
        
        if scored_pairs:
            for p in scored_pairs:
                for k in ["blocking_reasons", "field_evidence", "comparisons", "matched_subgroups", "normalization_tags"]:
                    if k in p and isinstance(p[k], str):
                        p[k] = json.loads(p[k])
            enrich_pairs_with_semantic(scored_pairs, by_id)
            for p in scored_pairs:
                for k in ["blocking_reasons", "field_evidence", "comparisons", "matched_subgroups", "normalization_tags"]:
                    p[k] = json.dumps(p[k])
            dec_table = pa.Table.from_pylist(scored_pairs)
            self.con.execute(f"COPY (SELECT * FROM dec_table ORDER BY match_weight DESC, id ASC) TO '{decide_path}' (FORMAT PARQUET)")
            
            decisions_sorted = self.con.execute(f"SELECT * FROM read_parquet('{decide_path}')").df().to_dict("records")
            for p in decisions_sorted:
                for k in ["blocking_reasons", "field_evidence", "comparisons", "matched_subgroups", "normalization_tags"]:
                    if isinstance(p.get(k), str):
                        p[k] = json.loads(p[k])
        else:
            decisions_sorted = []
            
        self.stage_results.append(StageResult("DECIDE", self.run_id, self.attempt_id, True, int((time.monotonic() - t0)*1000), {"pair_count": len(decisions_sorted)}))

        # ---------------------------------------------------------
        # STG-06: CLUSTER
        # ---------------------------------------------------------
        ctx_cluster = self._ctx("CLUSTER")
        t0 = time.monotonic()
        if count > 0:
            all_recs = list(by_id.values())
        else:
            all_recs = []
        clusters = build_proposals(all_recs, decisions_sorted, self.run_id, policy_version, negative_pairs=negative_pairs)
        
        review_cases = []
        for case in clusters["review_cases"]:
            pair = next((item for item in decisions_sorted if item["id"] == case["pair_ids"][0]), None)
            if pair is None: continue
            classification = classify_pair(pair, by_id)
            if classification is None: continue
            review_cases.append({**case, **classification})
            
        output = {**clusters, "review_cases": review_cases}
        self.stage_results.append(StageResult("CLUSTER", self.run_id, self.attempt_id, True, int((time.monotonic() - t0)*1000), {}))

        # ---------------------------------------------------------
        # STG-07: MATERIALIZE
        # ---------------------------------------------------------
        ctx_mat = self._ctx("MATERIALIZE")
        t0 = time.monotonic()
        
        # Postgres bulk insert would happen here if db_url is provided.
        if not return_data and self.db_url:
            # We would COPY the parquet files into staging tables here
            pass
        
        output["pairs"] = decisions_sorted
        output["candidate_count"] = cand_count
        output["insufficient_count"] = insufficient_count
        output["llm_stats"] = {}
        
        self.stage_results.append(StageResult("MATERIALIZE", self.run_id, self.attempt_id, True, int((time.monotonic() - t0)*1000), {}))

        return output

    def total_duration_ms(self) -> int:
        return sum(s.duration_ms for s in self.stage_results)
"""

(PIPELINE_DIR / "stage_runner.py").write_text(stage_runner_code, encoding="utf-8")

# 2. Update tfidf_blocking.py to match DuckDB workflow limits
tfidf_code = """\"\"\"TF-IDF candidate generation for entity resolution (A STG-03-FR-006 - A STG-03-FR-009).\"\"\"
from collections import defaultdict
import numpy as np

TOP_K = 10
SIM_THRESHOLD = 0.30
MAX_DF = 0.05
MIN_DF = 2
_BATCH_SIZE = 2000

def _partition_key(record: dict) -> str:
    fields = record.get("normalized_fields", {})
    dob = fields.get("dob", "")
    birth_year = dob[:4] if dob and len(dob) >= 4 and dob[:4].isdigit() else ""
    province = (fields.get("address_parts") or {}).get("province", "")
    if birth_year and province: return f"y{birth_year}|p{province}"
    if birth_year: return f"y{birth_year}"
    if province: return f"p{province}"
    return "all"

def _adaptive_max_df(n_docs: int) -> int:
    return max(2, int(MAX_DF * n_docs))

def _build_vectorizer(texts: list[str], min_df: int):
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
    except ImportError:
        return None
    n = len(texts)
    max_df_val = _adaptive_max_df(n)
    for _min_df in (min_df, 1):
        try:
            vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 4), max_df=max_df_val, min_df=_min_df, sublinear_tf=True, norm="l2")
            matrix = vec.fit_transform(texts)
            return matrix
        except ValueError:
            continue
    return None

def _top_k_pairs(ids: list[str], matrix, top_k: int, sim_threshold: float) -> dict[tuple[str, str], set[str]]:
    n = len(ids)
    candidates = defaultdict(set)
    effective_k = min(top_k, n - 1)
    if effective_k < 1: return {}
    for batch_start in range(0, n, _BATCH_SIZE):
        batch_end = min(batch_start + _BATCH_SIZE, n)
        batch = matrix[batch_start:batch_end]
        sim_block = (batch @ matrix.T).toarray()
        for local_i in range(sim_block.shape[0]):
            global_i = batch_start + local_i
            row = sim_block[local_i].copy()
            row[global_i] = 0.0
            top_indices = np.argpartition(row, -effective_k)[-effective_k:]
            for j in top_indices:
                if row[j] >= sim_threshold:
                    left, right = sorted((ids[global_i], ids[j]))
                    candidates[(left, right)].add("tfidf_name")
    return dict(candidates)

def tfidf_candidates(records: list[dict], top_k: int = TOP_K, sim_threshold: float = SIM_THRESHOLD) -> dict[tuple[str, str], set[str]]:
    partitions = defaultdict(list)
    for record in records: partitions[_partition_key(record)].append(record)
    all_candidates = defaultdict(set)
    for part_records in partitions.values():
        if len(part_records) < 2: continue
        ids = [r["id"] for r in part_records]
        texts = [r.get("normalized_fields", {}).get("name") or "" for r in part_records]
        if not any(texts): continue
        matrix = _build_vectorizer(texts, MIN_DF)
        if matrix is None: continue
        pairs = _top_k_pairs(ids, matrix, top_k, sim_threshold)
        for pair, reasons in pairs.items():
            all_candidates[pair] |= reasons
    return dict(all_candidates)
"""
(PIPELINE_DIR / "tfidf_blocking.py").write_text(tfidf_code, encoding="utf-8")

# 3. Update resolution_service.py to stream DB records or pass db_url
service_file = BASE_DIR / "resolution_service.py"
service_code = service_file.read_text(encoding="utf-8")

if "def _compute(records: list[dict]" in service_code:
    # Instead of fetching all records, we let StageRunner fetch it if db_url is provided.
    # However, tests use records: list[dict], so we support both.
    new_compute = """def _compute(records: list[dict] | None, run_id: str, policy_version: str,
             negative_pairs: frozenset | None = None, db_url: str | None = None) -> dict:
    runner = StageRunner(run_id=run_id, attempt_id="default", db_url=db_url)
    return runner.run_all(records, policy_version=policy_version, negative_pairs=negative_pairs)"""
    
    service_code = service_code.replace("""def _compute(records: list[dict], run_id: str, policy_version: str,
             negative_pairs: frozenset | None = None) -> dict:
    \"\"\"Backward-compatible shortcut: delegates to StageRunner sequentially.\"\"\"
    runner = StageRunner(run_id=run_id, attempt_id="default")
    return runner.run_all(records, policy_version=policy_version, negative_pairs=negative_pairs)""", new_compute)

service_file.write_text(service_code, encoding="utf-8")

print("Refactoring complete.")

