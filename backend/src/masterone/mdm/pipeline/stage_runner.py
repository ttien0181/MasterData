"""7-stage pipeline with batched SCORE artifacts; CLUSTER still uses memory."""

import json
import os
import time
from dataclasses import dataclass, field
from itertools import islice
from hashlib import sha256
from pathlib import Path
from tempfile import mkdtemp
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from masterone.mdm.pipeline.candidate_engine import CandidateConfig, CandidateEngine, CandidateResult
from masterone.mdm.pipeline.clustering import build_proposals
from masterone.mdm.pipeline.review_grouping import classify_pair
from masterone.mdm.pipeline.scoring_engine import (
    CLUSTER_CACHE_RECORDS,
    ParquetSnapshot,
    RecordView,
    ScoringConfig,
    ScoringEngine,
    iter_score_batches,
)
from masterone.mdm.pipeline.semantic_address import enrich_pairs_with_semantic
from masterone.mdm.pipeline.standardization import standardize


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
        self.output_dir = output_dir if output_dir is not None else Path(mkdtemp(prefix="masterone-stages-"))
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.db_url = db_url
        self.stage_results: list[StageResult] = []
        self.con = duckdb.connect(str(self.output_dir / f"{run_id}.duckdb"))

    def _ctx(self, stage_name: str) -> StageContext:
        return StageContext(self.run_id, self.attempt_id, stage_name, "", self.output_dir)

    def _copy_snapshot_from_postgres(self, prep_path: str) -> None:
        import psycopg
        from sqlalchemy.engine import make_url

        url = make_url(self.db_url)
        conninfo = {"host": url.host, "port": url.port, "dbname": url.database,
                    "user": url.username, "password": url.password, **url.query}
        conninfo = {key: value for key, value in conninfo.items() if value is not None}
        columns = ("id", "external_id", "source_system", "entity_type", "raw_fields",
                   "normalized_fields", "quality_flags", "source_updated_at")
        schema = pa.schema([(name, pa.string()) for name in columns])
        sql = """
            SELECT id, external_id, source_system, entity_type,
                   raw_fields::text AS raw_fields,
                   normalized_fields::text AS normalized_fields,
                   quality_flags::text AS quality_flags,
                   COALESCE(normalized_fields->>'_source_updated_at', original_fields->>'source_updated_at',
                            extra_fields->>'source_updated_at') AS source_updated_at
            FROM mdm_resolution_records
            WHERE run_id = %s
        """
        with psycopg.connect(**conninfo) as conn, conn.cursor(name="prepare_snapshot") as cursor, \
                pq.ParquetWriter(prep_path, schema) as writer:
            cursor.itersize = 2048
            cursor.execute(sql, (self.run_id,))
            while rows := cursor.fetchmany(2048):
                arrays = [pa.array(values, type=pa.string()) for values in zip(*rows)]
                writer.write_table(pa.Table.from_arrays(arrays, schema=schema))

    def build_candidates(self, snapshot_path: str, candidate_path: str,
                         config: CandidateConfig | None = None,
                         batch_size: int = 50000) -> CandidateResult:
        """Read batches into a global index; batch boundaries never limit recall."""
        engine = CandidateEngine(config)
        for batch in pq.ParquetFile(snapshot_path).iter_batches(batch_size=batch_size):
            records = batch.to_pylist()
            for record in records:
                if isinstance(record["normalized_fields"], str):
                    record["normalized_fields"] = json.loads(record["normalized_fields"] or "{}")
            engine.add_records(records)
        result = engine.finalize()
        schema = pa.schema([("left_id", pa.string()), ("right_id", pa.string()),
                            ("reasons", pa.list_(pa.string()))])
        # One canonical row per pair; every source survives into SCORE. Written in chunks:
        # converting every pair to a dict at once is the largest allocation of the stage.
        with pq.ParquetWriter(candidate_path, schema) as writer:
            pairs = iter(result.pairs.items())
            while chunk := list(islice(pairs, 500_000)):
                writer.write_table(pa.Table.from_pylist(
                    [{"left_id": left, "right_id": right, "reasons": sorted(sources)}
                     for (left, right), sources in chunk], schema=schema))
        # DuckDB streams the Parquet file and spills to its own file, so this never holds every pair in memory.
        self.con.execute("DROP TABLE IF EXISTS cand_blocks")
        self.con.execute("CREATE TEMP TABLE cand_blocks (left_id VARCHAR, right_id VARCHAR, reason VARCHAR)")
        self.con.execute(f"INSERT INTO cand_blocks SELECT left_id, right_id, unnest(reasons) "
                         f"FROM read_parquet('{candidate_path}')")
        Path(candidate_path).with_suffix(".metrics.json").write_text(
            json.dumps(result.metrics, sort_keys=True), encoding="utf-8")
        with Path(candidate_path).with_suffix(".blocks.json").open("w", encoding="utf-8") as handle:
            json.dump(result.block_log, handle, sort_keys=True)
        return result

    def score_candidates(self, snapshot_path: str, candidate_path: str, score_path: str,
                         config: ScoringConfig | None = None, negative_pairs=None) -> dict:
        self.scoring_engine = ScoringEngine(ParquetSnapshot(snapshot_path, self.con), config,
                                            negative_pairs=negative_pairs)
        reader = pq.ParquetFile(candidate_path)
        def candidates():
            for batch in reader.iter_batches(batch_size=self.scoring_engine.config.batch_size):
                yield from batch.to_pylist()
        return self.scoring_engine.write_scores(candidates(), score_path, expected_count=reader.metadata.num_rows)

    def run_all(self, records_raw: list[dict] = None, policy_version: str = "person-v3", negative_pairs: frozenset | None = None, return_data: bool = True, candidate_config: CandidateConfig | None = None, scoring_config: ScoringConfig | None = None, execution_blocked_pairs: frozenset = frozenset()) -> dict:
        if not return_data and self.db_url:
            from sqlalchemy import create_engine
            from sqlalchemy.orm import sessionmaker

            from masterone.mdm.engine_adapter import engine_context, select_engine
            engine = create_engine(self.db_url)
            try:
                profile = select_engine(sessionmaker(engine, expire_on_commit=False), self.run_id)
                with engine_context(profile):
                    return self._run_with_profile(records_raw, policy_version, negative_pairs, return_data,
                        candidate_config, scoring_config, execution_blocked_pairs)
            finally:
                engine.dispose()
        return self._run_with_profile(records_raw, policy_version, negative_pairs, return_data,
            candidate_config, scoring_config, execution_blocked_pairs)

    def _run_with_profile(self, records_raw, policy_version, negative_pairs, return_data, candidate_config, scoring_config, execution_blocked_pairs):
        if return_data or not self.db_url:
            return self._run_all(records_raw, policy_version, negative_pairs, return_data,
                candidate_config, scoring_config, execution_blocked_pairs)
        from sqlalchemy import create_engine
        from sqlalchemy.exc import SQLAlchemyError
        from sqlalchemy.orm import sessionmaker

        from masterone.mdm.engine_adapter import feature
        from masterone.mdm.generation_staging import GenerationError, GenerationPublisher, config_identity, failure_code
        from masterone.mdm.models import ResolutionPublication, ResolutionRecord
        engine = create_engine(self.db_url)
        factory = sessionmaker(engine, expire_on_commit=False)
        identity = None
        try:
            with factory() as db:
                has_generation = db.get(ResolutionPublication, self.run_id) is not None
            if not feature("generation_staging_enabled") and not feature("stream_decide_materialize_enabled") and not feature("pipeline_resume_enabled") and not has_generation:
                return self._run_all(records_raw, policy_version, negative_pairs, return_data,
                    candidate_config, scoring_config, execution_blocked_pairs)
            publisher = GenerationPublisher(factory)
            identity = publisher.begin(self.run_id, config=config_identity(policy_version, scoring_config, candidate_config),
                attempt_id=self.attempt_id)
            # Direct callers may not substitute different input for imported IDs.
            if records_raw is not None:
                with factory() as db:
                    expected_count = db.query(ResolutionRecord).filter_by(run_id=self.run_id).count()
                    if len(records_raw) != expected_count or len({r['id'] for r in records_raw}) != expected_count:
                        raise GenerationError("GENERATION_INPUT_CHANGED")
                    for raw in records_raw:
                        row = db.get(ResolutionRecord, raw['id'])
                        fields = ('external_id', 'source_system', 'entity_type', 'raw_fields', 'normalized_fields', 'quality_flags')
                        if row is None or row.run_id != self.run_id or any(raw.get(f) != getattr(row, f) for f in fields):
                            raise GenerationError("GENERATION_INPUT_CHANGED")
            output = self._run_all(records_raw, policy_version, negative_pairs, True,
                candidate_config, scoring_config, execution_blocked_pairs)
            started = time.monotonic()
            publisher.stage(identity, output)
            publisher.promote(identity)
            self.stage_results[-1].duration_ms += int((time.monotonic() - started)*1000)
            return output
        except BaseException as error:
            if identity is not None:
                publisher.fail(identity, failure_code(error))
            if isinstance(error, SQLAlchemyError):
                raise GenerationError("GENERATION_DB_FAILED") from None
            raise
        finally:
            engine.dispose()

    def _run_all(self, records_raw: list[dict] = None, policy_version: str = "person-v3", negative_pairs: frozenset | None = None, return_data: bool = True, candidate_config: CandidateConfig | None = None, scoring_config: ScoringConfig | None = None, execution_blocked_pairs: frozenset = frozenset()) -> dict:
        from masterone.config import get_settings
        from masterone.mdm.pipeline.streaming import fallback_reason, requested
        from masterone.storage.r2 import get_store
        store = get_store(get_settings()) if requested() else None
        from masterone.mdm.engine_adapter import feature
        checkpoint_factory, checkpoint_engine = None, None
        if feature("pipeline_resume_enabled") and self.db_url:
            from sqlalchemy import create_engine
            from sqlalchemy.orm import sessionmaker

            from masterone.mdm.job_queue import current_claim
            from masterone.mdm.pipeline.checkpoints import DatabasePartitionStore
            checkpoint_engine = create_engine(self.db_url)
            checkpoint_factory = sessionmaker(checkpoint_engine, expire_on_commit=False)
            store = store or DatabasePartitionStore(checkpoint_factory, self.run_id, current_claim())
        serial_reason = fallback_reason(scoring_config, store)
        if serial_reason is None:
            from sqlalchemy import create_engine, select
            from sqlalchemy.orm import Session

            from masterone.mdm.models import ResolutionRecord
            from masterone.mdm.pipeline.decision_store import DecisionStore
            from masterone.mdm.pipeline.golden_v2 import record_payload
            from masterone.mdm.resolution_service import _compute
            database = checkpoint_engine or (create_engine(self.db_url) if self.db_url else None)
            def rows():
                if records_raw is not None:
                    yield from records_raw
                elif database is not None:
                    with Session(database) as db:
                        query = select(ResolutionRecord).where(ResolutionRecord.run_id == self.run_id).order_by(ResolutionRecord.id)
                        for row in db.scalars(query.execution_options(yield_per=get_settings().stream_partition_rows)):
                            yield record_payload(row)
                else:
                    raise ValueError("db_url required when records_raw is None")
            started = time.monotonic()
            try:
                output = _compute(rows(), self.run_id, policy_version, negative_pairs=negative_pairs,
                    candidate_config=candidate_config, scoring_config=scoring_config, scoring_output_dir=self.output_dir,
                    execution_blocked_pairs=execution_blocked_pairs, _stream_store=store,
                    _normalize=True, _attempt_id=self.attempt_id, _checkpoint_factory=checkpoint_factory)
                if isinstance(output["pairs"], DecisionStore):
                    self.decision_store = output["pairs"]
                self.stage_results.append(StageResult("STREAMING_COMPUTE", self.run_id, self.attempt_id, True,
                    int((time.monotonic()-started)*1000), {"candidate_count": output["candidate_count"]}))
                if not return_data and database is not None:
                    from masterone.mdm.resolution_persistence import persist_snapshot
                    with Session(database) as db, db.begin():
                        persist_snapshot(db, self.run_id, output)
                self.stage_results.append(StageResult("MATERIALIZE", self.run_id, self.attempt_id, True, 0, {}))
                return output
            finally:
                if database is not None:
                    database.dispose()
        if checkpoint_engine is not None:
            checkpoint_engine.dispose()
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
                # Direct in-memory callers historically provide only `id`.
                # Keep a source-neutral identity; DB snapshots already carry
                # their real external ID/source and are never rewritten.
                nr.setdefault("external_id", r["id"])
                nr.setdefault("source_system", "UNKNOWN")
                nr.setdefault("entity_type", "UNKNOWN")
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
            # psycopg streams the indexed run_id lookup straight into parquet. DuckDB's postgres
            # scanner took ~5-8 min to move the JSON columns of one 10k-record run.
            self._copy_snapshot_from_postgres(prep_path)
        
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
                    # Imported normalized fields define the selected snapshot,
                    # exactly as on the small path; never normalize it again.
                    if json.loads(r["normalized_fields"] or "{}"):
                        continue
                    raw = json.loads(r["raw_fields"]) if r["raw_fields"] else {}
                    nf, flags = standardize(raw, r.get("entity_type", "UNKNOWN"))
                    r["normalized_fields"] = json.dumps(nf)
                    r["quality_flags"] = json.dumps(flags)
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
            self.con.execute(f"CREATE TEMP TABLE norm1 AS SELECT * FROM read_parquet('{norm_final}')")
        
        self.stage_results.append(StageResult("NORMALIZE", self.run_id, self.attempt_id, True, int((time.monotonic() - t0)*1000), {"record_count": count}))

        # ---------------------------------------------------------
        # STG-03: BUILD_CANDIDATES
        # ---------------------------------------------------------
        ctx_build = self._ctx("BUILD_CANDIDATES")
        t0 = time.monotonic()
        cand_path = _artifact_path(ctx_build, "candidates.parquet")
        
        candidate_result = self.build_candidates(norm_final, cand_path, config=candidate_config)
        cand_count = len(candidate_result.pairs)
        self.stage_results.append(StageResult(
            'BUILD_CANDIDATES', self.run_id, self.attempt_id, True,
            int((time.monotonic() - t0)*1000), candidate_result.metrics))

        # ---------------------------------------------------------
        # STG-04: SCORE
        # ---------------------------------------------------------
        ctx_score = self._ctx("SCORE")
        t0 = time.monotonic()
        score_path = _artifact_path(ctx_score, "scored.parquet")
        
        scoring_metrics = self.score_candidates(norm_final, cand_path, score_path, scoring_config, negative_pairs)
        ctx_score.config_hash = scoring_metrics["config_hash"]
        self.stage_results.append(StageResult("SCORE", self.run_id, self.attempt_id, True,
            int((time.monotonic() - t0)*1000), scoring_metrics))

        # ---------------------------------------------------------
        # STG-05: DECIDE
        # ---------------------------------------------------------
        ctx_decide = self._ctx("DECIDE")
        t0 = time.monotonic()
        decide_path = _artifact_path(ctx_decide, "decisions.parquet")
        
        # SCORE contains every candidate. Filtering and enrichment belong here.
        # The existing decision/cluster consumer still materializes decisions;
        # it does not keep an additional full list of numerical SCORE results.
        from masterone.config import get_settings
        from masterone.mdm.pipeline.cluster_policy import enabled as cluster_enabled
        from masterone.mdm.pipeline.decision_store import DecisionStore
        # Optional semantic enrichment retains its existing global budget/cache.
        disk_decisions = cluster_enabled() and not get_settings().semantic_address_enabled
        self.decision_store = DecisionStore(self.output_dir) if disk_decisions else None
        decision_pairs = []
        by_id = {}
        # Only semantic enrichment reads records here; clustering looks them up on demand.
        keep_hydrated = get_settings().semantic_address_enabled
        insufficient_count = 0
        score_rows = pq.ParquetFile(score_path).metadata.num_rows
        for batch, hydrated, decisions in self.scoring_engine.decided_batches(
                iter_score_batches(score_path, self.scoring_engine.config.batch_size), score_rows,
                need_hydrated=keep_hydrated):
            for scored, result_pair in zip(batch, decisions):
                if result_pair["decision"] == "INSUFFICIENT_EVIDENCE":
                    insufficient_count += 1
                    continue
                pair_id = sha256(f"{self.run_id}:{scored.left_id}:{scored.right_id}:{policy_version}".encode()).hexdigest()
                payload = {"id": pair_id, **result_pair}
                if disk_decisions:
                    self.decision_store.add(payload)
                else:
                    decision_pairs.append(payload)
                    if keep_hydrated:
                        for rid in (scored.left_id, scored.right_id):
                            by_id[rid] = hydrated[rid]
        if disk_decisions:
            self.decision_store.seal()
            self.decision_store.write_parquet(decide_path, self.scoring_engine.config.batch_size)
            decisions_sorted = self.decision_store
        elif decision_pairs:
            enrich_pairs_with_semantic(decision_pairs, by_id)
            for p in decision_pairs:
                for k in ["blocking_reasons", "field_evidence", "comparisons", "matched_subgroups", "normalization_tags"]:
                    p[k] = json.dumps(p[k])
            dec_table = pa.Table.from_pylist(decision_pairs)
            self.con.execute(f"COPY (SELECT * FROM dec_table ORDER BY match_weight DESC NULLS LAST, left_id ASC, right_id ASC) TO '{decide_path}' (FORMAT PARQUET)")
            
            # Keep nullable contract fields as None. Pandas converts SQL NULL
            # into NaN, which the strict serving adapter correctly rejects.
            decisions_sorted = pq.read_table(decide_path).to_pylist()
            for p in decisions_sorted:
                for k in ["blocking_reasons", "field_evidence", "comparisons", "matched_subgroups", "normalization_tags"]:
                    if isinstance(p.get(k), str):
                        p[k] = json.loads(p[k])
        else:
            decisions_sorted = []
        del decision_pairs, by_id
            
        self.stage_results.append(StageResult("DECIDE", self.run_id, self.attempt_id, True, int((time.monotonic() - t0)*1000), {"pair_count": len(decisions_sorted)}))

        # ---------------------------------------------------------
        # STG-06: CLUSTER
        # ---------------------------------------------------------
        ctx_cluster = self._ctx("CLUSTER")
        t0 = time.monotonic()
        # Records are looked up by id from the snapshot index instead of loading every record here.
        snapshot = self.scoring_engine.snapshot
        snapshot.widen_cache(int(os.environ.get("MDM_CLUSTER_CACHE_RECORDS", CLUSTER_CACHE_RECORDS)))
        by_id_full = RecordView(snapshot)
        clusters = build_proposals(by_id_full, decisions_sorted, self.run_id, policy_version, negative_pairs=negative_pairs,
                                   execution_blocked_pairs=execution_blocked_pairs)
        
        review_cases = []
        pairs_by_id = None if disk_decisions else {}
        if pairs_by_id is not None:
            for item in decisions_sorted:
                pairs_by_id.setdefault(item["id"], item)
        for case in clusters["review_cases"]:
            pair = decisions_sorted.get_id(case["pair_ids"][0]) if disk_decisions else pairs_by_id.get(case["pair_ids"][0])
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
        
        output["pairs"] = decisions_sorted
        output["candidate_count"] = cand_count
        output["candidate_metrics"] = candidate_result.metrics
        output["scoring_metrics"] = scoring_metrics
        output["scoring_artifact"] = score_path
        output["insufficient_count"] = insufficient_count
        output["llm_stats"] = {}
        output["streaming"] = {"mode": "serial", "reason": serial_reason}
        
        if not return_data and self.db_url:
            from sqlalchemy import create_engine
            from sqlalchemy.orm import Session

            from masterone.mdm.resolution_persistence import persist_snapshot
            engine = create_engine(self.db_url)
            try:
                with Session(engine) as db, db.begin():
                    persist_snapshot(db, self.run_id, output)
            finally:
                engine.dispose()

        self.stage_results.append(StageResult("MATERIALIZE", self.run_id, self.attempt_id, True, int((time.monotonic() - t0)*1000), {}))

        return output

    def total_duration_ms(self) -> int:
        return sum(s.duration_ms for s in self.stage_results)

    def close_snapshot(self):
        engine = getattr(self, "scoring_engine", None)
        snapshot = getattr(engine, "snapshot", None)
        if snapshot is not None:
            snapshot.close()

    def close_decisions(self):
        store = getattr(self, "decision_store", None)
        if store is not None:
            store.close()
            self.decision_store = None
