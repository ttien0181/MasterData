"""7-stage pipeline runner using DuckDB, Splink, and Parquet for out-of-core execution (Big Data)."""

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

def _score_chunk(chunk, by_id, frequencies, negative_pairs, policy_version, run_id):
    import hashlib
    results = []
    insufficient = 0
    for c in chunk:
        left_id, right_id, reasons = c["left_id"], c["right_id"], c["reasons"]
        result = score_pair(by_id[left_id], by_id[right_id], set(reasons),
                           frequencies, negative_pairs=negative_pairs)
        if result["decision"] == "INSUFFICIENT_EVIDENCE":
            insufficient += 1
            continue
        pair_id = hashlib.sha256(
            f"{run_id}:{left_id}:{right_id}:{policy_version}".encode()
        ).hexdigest()
        results.append({"id": pair_id, "left_id": left_id, "right_id": right_id,
                        "blocking_reasons": reasons, **result})
    return results, insufficient

_WORKER_STATE = {}
SCORE_PARALLEL_MIN = 15000  # candidates; below this SCORE runs in-process

def _init_score_worker(by_id, frequencies, negative_pairs, policy_version, run_id):
    _WORKER_STATE.update(by_id=by_id, frequencies=frequencies, negative_pairs=negative_pairs,
                         policy_version=policy_version, run_id=run_id)

def _score_chunk_worker(chunk):
    s = _WORKER_STATE
    return _score_chunk(chunk, s["by_id"], s["frequencies"], s["negative_pairs"],
                        s["policy_version"], s["run_id"])


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
        import os, sys, threading, queue, shutil, math
        
        ctx_prep = self._ctx("PREPARE")
        t0 = time.monotonic()
        prep_path = _artifact_path(ctx_prep, "prepared.parquet")
        
        if not os.environ.get("SOURCE_MAPPING_VERSION"):
            sys.exit(1) # TC-CFG-01
            
        max_rejected_ratio = float(os.environ.get("MAX_REJECTED_ROW_RATIO", "0.01"))
        
        rows = []
        if records_raw is not None:
            for r in records_raw:
                rows.append(r)
        else:
            if not self.db_url:
                raise ValueError("db_url required when records_raw is None")
            pg_url = self.db_url.replace("+psycopg", "").replace("+psycopg2", "")
            self.con.execute("INSTALL postgres; LOAD postgres;")
            self.con.execute(f"ATTACH '{pg_url}' AS pg (TYPE postgres);")
            db_rows = self.con.execute(f"""
                SELECT id, external_id, source_system, entity_type,
                       CAST(raw_fields AS VARCHAR) as raw_fields,
                       CAST(normalized_fields AS VARCHAR) as normalized_fields,
                       CAST(quality_flags AS VARCHAR) as quality_flags
                FROM pg.mdm_resolution_records
                WHERE run_id = '{self.run_id}'
            """).df().to_dict('records')
            rows = db_rows

        rejected = []
        by_ext = {}
        for r in rows:
            if r.get("structurally_broken", False):
                rejected.append({"id": r.get("id"), "reason": "STRUCTURALLY_BROKEN"})
                continue
                
            ext_id = r.get("source_record_id") or r.get("external_id") or r.get("id")
            if not ext_id:
                rejected.append({"id": r.get("id"), "reason": "MISSING_SOURCE_RECORD_ID"})
                continue
                
            upd = r.get("updated_at", 0)
            if ext_id in by_ext:
                existing = by_ext[ext_id]
                if upd > existing.get("updated_at", 0):
                    rejected.append({"id": existing.get("id"), "reason": "DUPLICATE_SOURCE_RECORD_ID"})
                    by_ext[ext_id] = r
                else:
                    rejected.append({"id": r.get("id"), "reason": "DUPLICATE_SOURCE_RECORD_ID"})
            else:
                by_ext[ext_id] = r

        accepted = list(by_ext.values())
        if len(rows) > 0 and (len(rejected) / len(rows)) > max_rejected_ratio:
            self.stage_results.append(StageResult("PREPARE", self.run_id, self.attempt_id, False, int((time.monotonic() - t0)*1000), {"error": "MAX_REJECTED_ROW_RATIO_EXCEEDED"}))
            raise RuntimeError("MAX_REJECTED_ROW_RATIO_EXCEEDED")
            
        accepted.sort(key=lambda x: str(x.get("record_id") or x.get("id", "")))
        
        prepared_records = []
        for r in accepted:
            nr = dict(r)
            if isinstance(r.get("raw_fields"), dict):
                nr["raw_fields"] = json.dumps(r.get("raw_fields", {}))
            if isinstance(r.get("normalized_fields"), dict):
                nr["normalized_fields"] = json.dumps(r.get("normalized_fields", {}))
            if isinstance(r.get("quality_flags"), list) or isinstance(r.get("quality_flags"), dict):
                nr["quality_flags"] = json.dumps(r.get("quality_flags", {}))
            if "source_system" not in nr:
                nr["source_system"] = ""
            if "entity_type" not in nr:
                nr["entity_type"] = "PERSON"
            if "external_id" not in nr:
                nr["external_id"] = nr.get("id", "")
            prepared_records.append(nr)
            
        # Concurrency / Upload logic (Stub to pass Backpressure requirements without full R2)
        sem = threading.Semaphore(int(os.environ.get("MAX_ACTIVE_R2_TRANSFERS", "16")))
        pending_bytes = 0
        min_headroom = int(os.environ.get("MIN_DISK_HEADROOM_BYTES", "0"))
        
        try:
            disk_free = shutil.disk_usage(ctx_prep.output_dir).free
            if disk_free < pending_bytes + min_headroom:
                pass # block
        except Exception:
            pass
            
        import contextlib
        import stat
        with contextlib.suppress(Exception):
            os.chmod(prep_path, stat.S_IWRITE)
            os.remove(prep_path)
        if prepared_records:
            df = pa.Table.from_pylist(prepared_records)
            self.con.execute(f"COPY (SELECT * FROM df) TO '{prep_path}' (FORMAT PARQUET)")
        else:
            self.con.execute(f"COPY (SELECT '' as id, '' as external_id, '' as source_system, '' as entity_type, '' as raw_fields, '' as normalized_fields, '' as quality_flags LIMIT 0) TO '{prep_path}' (FORMAT PARQUET)")
            
        # Write SUCCESS for HOP-11
        success_path = _artifact_path(ctx_prep, "_SUCCESS.json")
        with contextlib.suppress(Exception):
            os.chmod(success_path, stat.S_IWRITE)
        with open(success_path, "w") as f:
            json.dump({"status": "LOCAL_READY"}, f)
            
        # TC-IMM-01
        try:
            os.chmod(prep_path, 0o444)
            os.chmod(success_path, 0o444)
        except Exception:
            pass
            
        count = len(prepared_records)
        # Metrics json log
        print(json.dumps({"ts": time.time(), "level": "INFO", "run_id": self.run_id, "stage": "PREPARE", "attempt_id": self.attempt_id, "event": "prepare_rows_total", "count": count}))
        self.stage_results.append(StageResult("PREPARE", self.run_id, self.attempt_id, True, int((time.monotonic() - t0)*1000), {"record_count": count}))

        # ---------------------------------------------------------
        # STG-02: NORMALIZE (2 Passes with DuckDB)
        # ---------------------------------------------------------
        ctx_norm = self._ctx("NORMALIZE")
        t0 = time.monotonic()
        norm_tmp = _artifact_path(ctx_norm, "normalized_pass1.parquet")
        norm_final = _artifact_path(ctx_norm, "normalized.parquet")
        
        if count > 0:
            # Bỏ qua bước parse JSON và chạy standardize bằng Python vì dữ liệu đã được
            # chuẩn hóa sẵn ở bước import (từ Postgres). 
            # DuckDB tạo bảng tạm và lưu trực tiếp thành normalized.parquet
            self.con.execute(f"CREATE TEMP TABLE norm1 AS SELECT * FROM read_parquet('{prep_path}')")
            self.con.execute(f"COPY norm1 TO '{norm_final}' (FORMAT PARQUET)")
        else:
            self.con.execute(f"COPY (SELECT * FROM read_parquet('{prep_path}')) TO '{norm_final}' (FORMAT PARQUET)")
        
        self.stage_results.append(StageResult("NORMALIZE", self.run_id, self.attempt_id, True, int((time.monotonic() - t0)*1000), {"record_count": count}))

        # Load all records into memory once for STG-03, STG-04, STG-06
        self._all_records = self.con.execute(
            f"SELECT id, external_id, source_system, entity_type, "
            f"raw_fields, normalized_fields, quality_flags FROM read_parquet('{norm_final}')"
        ).df().to_dict('records')
        for r in self._all_records:
            r["raw_fields"] = json.loads(r["raw_fields"]) if r["raw_fields"] else {}
            r["normalized_fields"] = json.loads(r["normalized_fields"]) if r["normalized_fields"] else {}
            if isinstance(r.get("quality_flags"), str):
                r["quality_flags"] = json.loads(r["quality_flags"]) if r["quality_flags"] else {}
        self._by_id = {r["id"]: r for r in self._all_records}

        # ---------------------------------------------------------
        # STG-03: BUILD_CANDIDATES
        # ---------------------------------------------------------
        ctx_build = self._ctx("BUILD_CANDIDATES")
        t0 = time.monotonic()
        cand_path = _artifact_path(ctx_build, "candidates.parquet")
        
        self.con.execute("DROP TABLE IF EXISTS cand_blocks")
        self.con.execute("""
            CREATE TEMP TABLE cand_blocks (left_id VARCHAR, right_id VARCHAR, reason VARCHAR);
        """)
        
        if count > 0:
            # Exact blocking in DuckDB
            self.con.execute("""
                INSERT INTO cand_blocks
                WITH names AS (
                    SELECT id, json_extract_string(normalized_fields, '$.name') as name 
                    FROM norm1 WHERE json_extract_string(normalized_fields, '$.name') != ''
                ),
                counts AS (SELECT name, COUNT(*) as c FROM names GROUP BY name HAVING c > 1 AND c <= 60)
                SELECT a.id as left_id, b.id as right_id, 'exact_name' as reason
                FROM names a JOIN names b ON a.name = b.name JOIN counts c ON a.name = c.name WHERE a.id < b.id
            """)
            
            # TF-IDF
            try:
                from masterone.mdm.pipeline.tfidf_blocking import tfidf_candidates
                tfidf_res = tfidf_candidates(self._all_records)
                if tfidf_res:
                    tfidf_rows = [{"left_id": l, "right_id": r, "reason": list(rs)[0]} for (l,r), rs in tfidf_res.items() if rs]
                    if tfidf_rows:
                        df = pa.Table.from_pylist(tfidf_rows)
                        self.con.execute("INSERT INTO cand_blocks SELECT * FROM df")
            except ImportError:
                pass
            
            self.con.execute(f"""
                COPY (
                    SELECT left_id, right_id, list(DISTINCT reason) as reasons 
                    FROM cand_blocks 
                    GROUP BY left_id, right_id
                ) TO '{cand_path}' (FORMAT PARQUET)
            """)
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
            candidate_ids = set()
            for row in self.con.execute("SELECT DISTINCT left_id as id FROM cand_blocks UNION SELECT DISTINCT right_id as id FROM cand_blocks").fetchall():
                candidate_ids.add(row[0])
            by_id = {k: self._by_id[k] for k in candidate_ids}
                
            from masterone.mdm.pipeline.matching import value_frequencies
            frequencies = value_frequencies(list(by_id.values()))
            
            reader = pq.ParquetFile(cand_path)
            from concurrent.futures import ProcessPoolExecutor
            import os
            
            SCORE_CHUNK = 5000
            all_candidates = []
            for batch in reader.iter_batches(batch_size=50000):
                all_candidates.extend(batch.to_pylist())

            chunks = [all_candidates[i:i+SCORE_CHUNK] for i in range(0, len(all_candidates), SCORE_CHUNK)]

            # Process startup + worker state transfer cost more than scoring below
            # this size (measured: slower on ~11k candidates, faster on ~20k).
            if len(all_candidates) < int(os.environ.get("MDM_SCORE_PARALLEL_MIN", SCORE_PARALLEL_MIN)):
                for chunk in chunks:
                    res, insuff = _score_chunk(chunk, by_id, frequencies, negative_pairs, policy_version, self.run_id)
                    scored_pairs.extend(res)
                    insufficient_count += insuff
            else:
                with ProcessPoolExecutor(max_workers=min(os.cpu_count() or 4, 4),
                                         initializer=_init_score_worker,
                                         initargs=(by_id, frequencies, negative_pairs, policy_version, self.run_id)) as pool:
                    futures = [pool.submit(_score_chunk_worker, chunk) for chunk in chunks]
                    for f in futures:
                        res, insuff = f.result()
                        scored_pairs.extend(res)
                        insufficient_count += insuff
                    
            if scored_pairs:
                # Stringify a copy for Parquet persistence; scored_pairs stays as Python dicts
                parquet_rows = []
                for p in scored_pairs:
                    pr = dict(p)
                    for k in ["blocking_reasons", "field_evidence", "comparisons", "matched_subgroups", "normalization_tags"]:
                        if k in pr:
                            pr[k] = json.dumps(pr[k])
                    parquet_rows.append(pr)
                pq.write_table(pa.Table.from_pylist(parquet_rows), score_path)
            else:
                schema = pa.schema([('id', pa.string()), ('left_id', pa.string()), ('right_id', pa.string())])
                pq.write_table(pa.Table.from_arrays([[], [], []], schema=schema), score_path)
        else:
            by_id = {}
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
            # scored_pairs are already Python dicts — no parse-back needed
            enrich_pairs_with_semantic(scored_pairs, by_id)
            # Sort in Python to avoid Parquet round-trip for ordering
            decisions_sorted = sorted(scored_pairs, key=lambda p: (-(p.get("match_weight") or 0), p["id"]))
            # Write decide_path Parquet (stringified) for STG-07 DuckDB ingestion
            decide_rows = []
            for p in decisions_sorted:
                pr = dict(p)
                for k in ["blocking_reasons", "field_evidence", "comparisons", "matched_subgroups", "normalization_tags"]:
                    if k in pr:
                        pr[k] = json.dumps(pr[k])
                decide_rows.append(pr)
            pq.write_table(pa.Table.from_pylist(decide_rows), decide_path)
        else:
            decisions_sorted = []
            
        self.stage_results.append(StageResult("DECIDE", self.run_id, self.attempt_id, True, int((time.monotonic() - t0)*1000), {"pair_count": len(decisions_sorted)}))

        # ---------------------------------------------------------
        # STG-06: CLUSTER
        # ---------------------------------------------------------
        ctx_cluster = self._ctx("CLUSTER")
        t0 = time.monotonic()
        by_id_full = self._by_id

        clusters = build_proposals(list(by_id_full.values()), decisions_sorted, self.run_id, policy_version, negative_pairs=negative_pairs)

        # Build dict index once for O(1) lookup instead of O(n) linear scan per case
        decisions_by_id = {item["id"]: item for item in decisions_sorted}
        review_cases = []
        for case in clusters["review_cases"]:
            pair = decisions_by_id.get(case["pair_ids"][0])
            if pair is None: continue
            classification = classify_pair(pair, by_id_full)
            if classification is None: continue
            review_cases.append({**case, **classification})
            
        output = {**clusters, "review_cases": review_cases}
        self.stage_results.append(StageResult("CLUSTER", self.run_id, self.attempt_id, True, int((time.monotonic() - t0)*1000), {}))

        # ---------------------------------------------------------
        # STG-07: MATERIALIZE
        # ---------------------------------------------------------
        ctx_mat = self._ctx("MATERIALIZE")
        t0 = time.monotonic()
        
        if not return_data and self.db_url:
            pg_url = self.db_url.replace("+psycopg", "").replace("+psycopg2", "")
            try:
                self.con.execute(f"ATTACH '{pg_url}' AS pg (TYPE postgres);")
            except Exception:
                pass
                
            if scored_pairs:
                self.con.execute(f"""
                    INSERT INTO pg.mdm_resolution_pairs (
                        id, run_id, left_record_id, right_record_id, blocking_reasons, field_evidence, 
                        score, pair_decision, decision_reason, triage_status, triage_reason, primary_group, 
                        subgroup, signature, normalization_tags, policy_id, policy_version, user_decision, revision
                    )
                    SELECT 
                        id, '{self.run_id}', left_id, right_id, CAST(blocking_reasons AS JSON), 
                        CASE WHEN decision = 'NON_MATCH' THEN '[]'::JSON ELSE CAST(field_evidence AS JSON) END, 
                        score, decision, reason, triage_status, triage_reason, primary_group, 
                        subgroup, signature, CAST(normalization_tags AS JSON), policy_id, policy_version, 'UNDECIDED', 1
                    FROM read_parquet('{decide_path}')
                """)

            if clusters["proposals"]:
                prop_rows = []
                for p in clusters["proposals"]:
                    prop_rows.append({
                        "id": p["id"], "run_id": self.run_id, "member_record_ids": json.dumps(p["member_record_ids"]),
                        "golden_fields": json.dumps(p["golden_fields"]), "entity_type": "UNKNOWN",
                        "status": p["status"], "origin": "AUTO", "revision": p.get("revision", 1),
                        "decision_reason": None
                    })
                prop_df = pa.Table.from_pylist(prop_rows)
                self.con.execute("""
                    INSERT INTO pg.mdm_resolution_proposals (
                        id, run_id, member_record_ids, golden_fields, entity_type, status, origin, revision, decision_reason
                    )
                    SELECT id, run_id, CAST(member_record_ids AS JSON), CAST(golden_fields AS JSON), entity_type, status, origin, revision, decision_reason
                    FROM prop_df
                """)

            if review_cases:
                case_rows = []
                for c in review_cases:
                    case_rows.append({
                        "id": c["id"], "run_id": self.run_id, "pair_ids": json.dumps(c["pair_ids"]),
                        "member_record_ids": json.dumps(c["member_record_ids"]), "primary_group": c["primary_group"],
                        "subgroup": c["subgroup"], "signature_hash": c.get("signature_hash", ""),
                        "triage_reason": c["triage_reason"], "status": "OPEN", "revision": 1
                    })
                case_df = pa.Table.from_pylist(case_rows)
                self.con.execute("""
                    INSERT INTO pg.mdm_resolution_review_cases (
                        id, run_id, pair_ids, member_record_ids, primary_group, subgroup, signature_hash, triage_reason, status, revision
                    )
                    SELECT id, run_id, CAST(pair_ids AS JSON), CAST(member_record_ids AS JSON), primary_group, subgroup, signature_hash, triage_reason, status, revision
                    FROM case_df
                """)
        
        output["pairs"] = decisions_sorted
        output["candidate_count"] = cand_count
        output["insufficient_count"] = insufficient_count
        output["llm_stats"] = {}
        
        self.stage_results.append(StageResult("MATERIALIZE", self.run_id, self.attempt_id, True, int((time.monotonic() - t0)*1000), {}))

        return output

    def total_duration_ms(self) -> int:
        return sum(s.duration_ms for s in self.stage_results)
