"""Compute a complete resolution snapshot and persist it atomically."""

from hashlib import sha256
from pathlib import Path
from tempfile import mkdtemp
from uuid import uuid4

from sqlalchemy.orm import sessionmaker

from masterone.db.base import now_utc
from masterone.mdm.job_queue import completed, current_claim, guard
from masterone.mdm.models import (
    ResolutionPair,
    ResolutionRecord,
    ResolutionRun,
    ResolutionTask,
)
from masterone.mdm.pipeline.candidate_engine import CandidateConfig, CandidateEngine
from masterone.mdm.pipeline.clustering import build_proposals
from masterone.mdm.pipeline.decision_semantics import load_cannot_links
from masterone.mdm.pipeline.golden_v2 import record_payload
from masterone.mdm.pipeline.review_grouping import classify_pair
from masterone.mdm.pipeline.scoring_engine import MemorySnapshot, ScoringConfig, ScoringEngine, iter_score_batches
from masterone.mdm.pipeline.semantic_address import enrich_pairs_with_semantic
from masterone.mdm.resolution_persistence import persist_snapshot


def _compute(records: list[dict], run_id: str, policy_version: str,
             negative_pairs: frozenset | None = None,
             candidate_config: CandidateConfig | None = None,
             scoring_config: ScoringConfig | None = None,
             scoring_output_dir: Path | None = None,
             execution_blocked_pairs: frozenset = frozenset(),
             _stream_store=None, _stream_event=None, _normalize=False, _attempt_id=None,
             _checkpoint_factory=None, _checkpoint_fault=None) -> dict:
    from masterone.config import get_settings
    from masterone.mdm.pipeline import streaming
    from masterone.storage.r2 import get_store
    score_dir = Path(scoring_output_dir) if scoring_output_dir is not None else Path(mkdtemp(prefix="p119-scores-"))
    store = (_stream_store if _stream_store is not None else get_store(get_settings())) if streaming.requested() else None
    from masterone.mdm.engine_adapter import feature
    resume = None
    if (feature("pipeline_resume_enabled") and _checkpoint_factory is not None
            and not get_settings().semantic_address_enabled and (scoring_config is None or scoring_config.mode == "rule")):
        from masterone.mdm.pipeline.checkpoints import CheckpointManager
        resume = CheckpointManager(_checkpoint_factory, run_id,
            _attempt_id or (current_claim().attempt_id if current_claim() else str(uuid4())), score_dir,
            policy_version=policy_version, candidate_config=candidate_config, scoring_config=scoring_config,
            negative_pairs=negative_pairs, execution_blocked_pairs=execution_blocked_pairs,
            store=store, fault=_checkpoint_fault, event=_stream_event, normalize=_normalize)
        store = resume.store
        _attempt_id = resume.attempt_id
        restored = resume.restore_result()
        if restored is not None:
            return restored
    fallback = streaming.fallback_reason(scoring_config, store)
    prepared = None
    if fallback is None:
        records, candidate_result, scoring_engine, scoring_metrics, score_path, prepared = streaming.compute_scores(
            records, run_id, workspace=score_dir, attempt_id=_attempt_id or (current_claim().attempt_id if current_claim() else None),
            store=store, candidate_config=candidate_config, scoring_config=scoring_config, negative_pairs=negative_pairs,
            normalize=_normalize, event=_stream_event, resume=resume)
    else:
        if _stream_event:
            _stream_event("fallback:" + fallback, None)
        candidate_result = CandidateEngine(candidate_config).generate(records)
        scoring_engine = ScoringEngine(MemorySnapshot(records), scoring_config, negative_pairs=negative_pairs)
        score_path = score_dir / "scored.parquet"
        scoring_metrics = scoring_engine.write_scores(
            ({"left_id": a, "right_id": b, "reasons": sorted(reasons)} for (a, b), reasons in sorted(candidate_result.pairs.items())),
            score_path, expected_count=len(candidate_result.pairs))
    candidate_blocks = candidate_result.pairs
    by_id = {record["id"]: record for record in records}
    from masterone.mdm.pipeline.cluster_policy import enabled as cluster_enabled
    from masterone.mdm.pipeline.decision_store import DecisionStore
    disk_decisions = cluster_enabled() and not get_settings().semantic_address_enabled
    pairs = DecisionStore(score_dir) if disk_decisions else []
    insufficient_count = 0
    decisions = prepared if prepared is not None else (
        {"left_id": scored.left_id, "right_id": scored.right_id, "result": scoring_engine.decide(scored, by_id)}
        for batch in iter_score_batches(score_path, scoring_engine.config.batch_size) for scored in batch)
    for item in decisions:
        result = item["result"]
        if result["decision"] == "INSUFFICIENT_EVIDENCE":
            insufficient_count += 1
            continue
        pair_id = sha256(f"{run_id}:{item['left_id']}:{item['right_id']}:{policy_version}".encode()).hexdigest()
        payload = {"id": pair_id, **result}
        if disk_decisions:
            pairs.add(payload)
        else:
            pairs.append(payload)
    if disk_decisions:
        pairs.seal()
    llm_stats = {"eligible": 0, "checked": 0, "changed": 0, "errors": 0} if disk_decisions else enrich_pairs_with_semantic(pairs, by_id)
    clusters = build_proposals(records, pairs, run_id, policy_version, negative_pairs=negative_pairs,
                               execution_blocked_pairs=execution_blocked_pairs)
    review_cases = []
    pairs_by_id = None if disk_decisions else {item["id"]: item for item in pairs}
    for case in clusters["review_cases"]:
        pair = pairs.get_id(case["pair_ids"][0]) if disk_decisions else pairs_by_id.get(case["pair_ids"][0])
        if pair is None:
            continue
        classification = classify_pair(pair, by_id)
        if classification is None:
            continue
        review_cases.append({**case, **classification})
    computed = {"pairs": pairs, "proposals": clusters["proposals"], "review_cases": review_cases,
            "candidate_count": len(candidate_blocks), "insufficient_count": insufficient_count,
            "llm_stats": llm_stats, "candidate_metrics": candidate_result.metrics,
            "scoring_metrics": scoring_metrics, "scoring_artifact": str(score_path),
            "cluster_metrics": clusters.get("cluster_metrics", {}),
            "streaming": {"mode": "serial" if fallback else "streaming", "reason": fallback},
            "_streaming_workspace": str(score_dir) if fallback is None else None}
    if resume:
        resume.save_result(computed)
    return computed


def run_pipeline(run_id: str, factory: sessionmaker, progress, *, scoring_config: ScoringConfig | None = None) -> None:
    from masterone.mdm.engine_adapter import engine_context, select_engine
    configuration = select_engine(factory, run_id)
    if configuration is None:
        return
    with engine_context(configuration):
        return _run_selected_pipeline(run_id, factory, progress, scoring_config=scoring_config)


def _run_selected_pipeline(run_id, factory, progress, *, scoring_config=None):
    from masterone.mdm.engine_adapter import feature
    from masterone.mdm.models import ResolutionPublication
    with factory() as db:
        has_generation = db.get(ResolutionPublication, run_id) is not None
    if feature("generation_staging_enabled") or feature("stream_decide_materialize_enabled") or feature("pipeline_resume_enabled") or has_generation:
        return _run_staged_pipeline(run_id, factory, progress, scoring_config=scoring_config)
    with factory() as db:
        run = db.get(ResolutionRun, run_id)
        if run is None:
            return
        guard(db, run_id)
        task = db.query(ResolutionTask).filter_by(run_id=run_id).one()
        task.status = "RUNNING"
        task.phase = "loading"
        task.updated_at = now_utc()
        record_count = db.query(ResolutionRecord).filter_by(run_id=run_id).count()
        run.status = "RUNNING"
        run.phase = "loading"
        policy_version = run.policy_version
        db_url = db.get_bind().url.render_as_string(hide_password=False)
        db.commit()

    with factory() as db:
        negative_pairs = load_cannot_links(db, run_id)
        execution_blocked_pairs = frozenset(tuple(sorted((p.left_record_id, p.right_record_id)))
            for p in db.query(ResolutionPair).filter_by(run_id=run_id, user_decision="REJECTED").all())

    progress("blocking", 0, record_count)
    if record_count > 10000:
        from masterone.mdm.pipeline.stage_runner import StageRunner
        runner = StageRunner(run_id=run_id, attempt_id=current_claim().attempt_id if current_claim() else task.id,
                             output_dir=Path(mkdtemp(prefix="p119-attempt-")), db_url=db_url)
        try:
            computed = runner.run_all(records_raw=None, policy_version=policy_version,
                negative_pairs=negative_pairs, return_data=True, scoring_config=scoring_config,
                execution_blocked_pairs=execution_blocked_pairs)
        finally:
            runner.close_snapshot()
            runner.con.close()
    else:
        with factory() as db:
            records = [record_payload(item) for item in db.query(ResolutionRecord)
                       .filter_by(run_id=run_id).order_by(ResolutionRecord.id).all()]
        computed = _compute(records, run_id, policy_version, negative_pairs=negative_pairs,
                            scoring_config=scoring_config, execution_blocked_pairs=execution_blocked_pairs)
    progress("persisting", record_count, record_count)
    try:
        with factory.begin() as db:
            guard(db, run_id)
            run = db.get(ResolutionRun, run_id)
            task = db.query(ResolutionTask).filter_by(run_id=run_id).one()
            persist_snapshot(db, run_id, computed)
            completed(db, run_id)
            task.status = "COMPLETED"
            task.phase = "completed"
            task.processed_count = record_count
            task.total_count = record_count
            task.updated_at = now_utc()
            run.status = "COMPLETED"
            run.phase = "completed"
            run.candidate_count = computed["candidate_count"]
            run.insufficient_count = computed["insufficient_count"]
            run.completed_at = now_utc()
    finally:
        from masterone.mdm.pipeline.decision_store import DecisionStore
        if isinstance(computed["pairs"], DecisionStore):
            computed["pairs"].close()



def _run_staged_pipeline(run_id, factory, progress, *, scoring_config=None):
    from sqlalchemy.exc import SQLAlchemyError

    from masterone.mdm.engine_adapter import feature
    from masterone.mdm.generation_staging import GenerationError, GenerationPublisher, config_identity, failure_code
    from masterone.mdm.models import ResolutionPublication
    from masterone.mdm.pipeline.decision_store import DecisionStore

    with factory() as db:
        run = db.get(ResolutionRun, run_id)
        if run is None:
            return
        policy_version = run.policy_version
        db_url = db.get_bind().url.render_as_string(hide_password=False)
        publication = db.get(ResolutionPublication, run_id)
        previous_attempt_id = publication.pipeline_attempt_id if publication else None
    publisher = GenerationPublisher(factory)
    attempt_id = current_claim().attempt_id if current_claim() else str(uuid4())
    try:
        identity = publisher.begin(run_id, config=config_identity(policy_version, scoring_config), attempt_id=attempt_id)
    except BaseException:
        if current_claim() is None:
            publisher.start_failed(run_id, attempt_id, previous_attempt_id)
        raise
    computed, runner = None, None
    try:
        with factory.begin() as db:
            run, generation, _ = publisher._lock(db, identity)
            if generation.state != "PROMOTED":
                run.status = "RUNNING"
                run.phase = "loading"
                task = db.query(ResolutionTask).filter_by(run_id=run_id).one()
                task.status = "RUNNING"
                task.phase = "loading"
                task.error_code = None
                task.updated_at = now_utc()
            record_count = generation.record_count
            already_promoted = generation.state == "PROMOTED"
            negative_pairs = load_cannot_links(db, run_id)
            vetoes = frozenset(tuple(sorted((p.left_record_id, p.right_record_id)))
                for p in db.query(ResolutionPair).filter_by(run_id=run_id, user_decision="REJECTED"))
        if already_promoted:
            publisher.promote(identity)
            return
        if feature("pipeline_resume_enabled") and publisher.resume_ready(identity):
            progress("promoting", record_count, record_count)
            publisher.promote(identity)
            return
        progress("blocking", 0, record_count)
        if record_count > 10000:
            from masterone.mdm.pipeline.stage_runner import StageRunner
            # Scratch paths cannot collide across retries; publication checkpoints
            # are separate from these disposable local scoring artifacts.
            scratch = Path(mkdtemp(prefix=f"p119-{identity.generation_id}-{identity.attempt_id}-"))
            runner = StageRunner(run_id=run_id, attempt_id=identity.attempt_id, output_dir=scratch, db_url=db_url)
            computed = runner.run_all(records_raw=None, policy_version=policy_version,
                negative_pairs=negative_pairs, return_data=True, scoring_config=scoring_config,
                execution_blocked_pairs=vetoes)
        else:
            with factory() as db:
                records = [record_payload(item) for item in db.query(ResolutionRecord)
                           .filter_by(run_id=run_id).order_by(ResolutionRecord.id)]
            computed = _compute(records, run_id, policy_version, negative_pairs=negative_pairs,
                scoring_config=scoring_config, execution_blocked_pairs=vetoes, _attempt_id=identity.attempt_id,
                _checkpoint_factory=factory)
        progress("staging", record_count, record_count)
        publisher.stage(identity, computed)
        progress("promoting", record_count, record_count)
        publisher.promote(identity)
    except BaseException as error:
        if current_claim() is None:
            publisher.fail(identity, failure_code(error))
        if isinstance(error, SQLAlchemyError):
            raise GenerationError("GENERATION_DB_FAILED") from None
        raise
    finally:
        if computed is not None and isinstance(computed["pairs"], DecisionStore):
            computed["pairs"].close()
        elif runner is not None:
            runner.close_decisions()
        if runner is not None:
            runner.close_snapshot()
            runner.con.close()


def mark_failed(run_id: str, factory: sessionmaker) -> None:
    with factory.begin() as db:
        guard(db, run_id)
        task = db.query(ResolutionTask).filter_by(run_id=run_id).one_or_none()
        run = db.get(ResolutionRun, run_id)
        from masterone.mdm.models import ResolutionPublication
        if run is not None and db.get(ResolutionPublication, run_id) is not None:
            # Generation attempts own failure/commit acknowledgment reconciliation.
            return
        if task is not None:
            task.status = "RETRYABLE_FAILED"
            task.phase = "failed"
            task.error_code = "PIPELINE_FAILED"
            task.updated_at = now_utc()
        if run is not None:
            run.status = "RETRYABLE_FAILED"
            run.phase = "failed"


def create_task(run_id: str, factory: sessionmaker) -> ResolutionTask:
    with factory.begin() as db:
        from masterone.mdm.generation_staging import lock_run
        run = lock_run(db, run_id)
        existing = db.query(ResolutionTask).filter_by(run_id=run_id).one_or_none()
        if existing is None:
            task = ResolutionTask(id=str(uuid4()), run_id=run_id, status="QUEUED")
            db.add(task)
        elif existing.status in {"QUEUED", "RUNNING", "COMPLETED"}:
            return existing
        else:
            task = existing
            task.attempt_id = None
            task.attempt_count = 0
            task.cancel_requested = False
            task.status = "QUEUED"
            task.phase = "queued"
            task.error_code = None
            task.updated_at = now_utc()
        run.status = "QUEUED"
        run.phase = "queued"
    return task
