"""Compute a complete resolution snapshot and persist it atomically."""

from hashlib import sha256
from pathlib import Path
from tempfile import mkdtemp
from time import perf_counter
from uuid import uuid4

from sqlalchemy.orm import sessionmaker

from masterone.config import get_settings
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
from masterone.mdm.pipeline.scoring_engine import MemorySnapshot, ScoringConfig, ScoringEngine, timed_score_batches
from masterone.mdm.pipeline.semantic_address import enrich_pairs_with_semantic
from masterone.mdm.resolution_persistence import persist_snapshot
from masterone.mdm.trace import emit, span, trace_context
from masterone.mdm.trace import enabled as trace_enabled


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
        with span("pipeline.resume_restore"):
            restored = resume.restore_result()
        if restored is not None:
            return restored
    fallback = streaming.fallback_reason(scoring_config, store)
    prepared = None
    if fallback is None:
        with span("pipeline.streaming_compute"):
            records, candidate_result, scoring_engine, scoring_metrics, score_path, prepared = streaming.compute_scores(
                records, run_id, workspace=score_dir, attempt_id=_attempt_id or (current_claim().attempt_id if current_claim() else None),
                store=store, candidate_config=candidate_config, scoring_config=scoring_config, negative_pairs=negative_pairs,
                normalize=_normalize, event=_stream_event, resume=resume)
    else:
        if _stream_event:
            _stream_event("fallback:" + fallback, None)
        with span("pipeline.build_candidates"):
            candidate_result = CandidateEngine(candidate_config).generate(records)
        scoring_engine = ScoringEngine(MemorySnapshot(records), scoring_config, negative_pairs=negative_pairs)
        score_path = score_dir / "scored.parquet"
        with span("pipeline.score", candidate_count=len(candidate_result.pairs)):
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
    decide_timings = {}
    def decisions_from_scores():
        for batch in timed_score_batches(score_path, scoring_engine.config.batch_size, decide_timings):
            started = perf_counter()
            decided = [{"left_id": scored.left_id, "right_id": scored.right_id,
                        "result": scoring_engine.decide(scored, by_id)} for scored in batch]
            decide_timings["policy_compute_ms"] = decide_timings.get("policy_compute_ms", 0.0) + (perf_counter() - started) * 1000
            yield from decided
    decisions = prepared if prepared is not None else decisions_from_scores()
    route_ms = 0.0
    with span("pipeline.decide", candidate_count=len(candidate_blocks)) as metrics:
        for item in decisions:
            route_started = perf_counter()
            result = item["result"]
            if result["decision"] == "INSUFFICIENT_EVIDENCE":
                insufficient_count += 1
                route_ms += (perf_counter() - route_started) * 1000
                continue
            pair_id = sha256(f"{run_id}:{item['left_id']}:{item['right_id']}:{policy_version}".encode()).hexdigest()
            payload = {"id": pair_id, **result}
            if disk_decisions:
                pairs.add(payload)
            else:
                pairs.append(payload)
            route_ms += (perf_counter() - route_started) * 1000
        metrics["insufficient_count"] = insufficient_count
    decide_timings["route_ms"] = route_ms
    for key, step in (("score_read_ms", "score_read"), ("policy_compute_ms", "policy_compute"),
                      ("route_ms", "route")):
        if key in decide_timings:
            emit("pipeline.decide." + step, "completed", duration_ms=round(decide_timings[key], 3),
                 batch_count=decide_timings.get("batch_count"), candidate_count=len(candidate_blocks))
    if disk_decisions:
        with span("pipeline.decide.spool_seal"):
            pairs.seal()
    with span("pipeline.decide.semantic"):
        llm_stats = {"eligible": 0, "checked": 0, "changed": 0, "errors": 0} if disk_decisions else enrich_pairs_with_semantic(pairs, by_id)
    with span("pipeline.cluster") as metrics:
        clusters = build_proposals(records, pairs, run_id, policy_version, negative_pairs=negative_pairs,
                                   execution_blocked_pairs=execution_blocked_pairs)
        metrics["auto_proposal_count"] = len(clusters["proposals"])
        metrics["review_case_count"] = len(clusters["review_cases"])
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
        with span("pipeline.checkpoint_save"):
            resume.save_result(computed)
    return computed


def run_pipeline(run_id: str, factory: sessionmaker, progress, *, scoring_config: ScoringConfig | None = None) -> None:
    from masterone.mdm.engine_adapter import engine_context, select_engine
    with factory() as db:
        run = db.get(ResolutionRun, run_id)
        upload_session_id = run.upload_session_id if run else None
    claim = current_claim()
    with trace_context(run_id=run_id, upload_session_id=upload_session_id,
                       attempt_id=claim.attempt_id if claim else None):
        with span("pipeline.total"):
            with span("pipeline.select_engine"):
                configuration = select_engine(factory, run_id)
            if configuration is None:
                return
            with engine_context(configuration):
                return _run_selected_pipeline(run_id, factory, progress, scoring_config=scoring_config)


def _emit_summary(factory, run_id: str, branch: str) -> None:
    """Report committed results; pair and proposal counts have distinct meanings."""
    from sqlalchemy import func

    from masterone.mdm.models import ResolutionProposal, ResolutionReviewCase

    with factory() as db:
        run = db.get(ResolutionRun, run_id)
        pairs = dict(db.query(ResolutionPair.triage_status, func.count(ResolutionPair.id))
                     .filter_by(run_id=run_id).group_by(ResolutionPair.triage_status).all())
        auto = db.query(ResolutionProposal.member_record_ids).filter_by(
            run_id=run_id, status="AUTO_READY").all()
        review_cases = db.query(func.count(ResolutionReviewCase.id)).filter_by(
            run_id=run_id, status="OPEN").scalar()
        emit("run.summary", "completed", run_id=run_id, branch=branch,
             record_count=run.record_count, file_count=run.file_count,
             candidate_count=run.candidate_count, insufficient_count=run.insufficient_count,
             pair_count=sum(pairs.values()), review_pair_count=pairs.get("REVIEW", 0),
             auto_pair_count=pairs.get("HIGH_CONFIDENCE_PROPOSAL", 0),
             blocked_pair_count=sum(count for status, count in pairs.items()
                                    if status in {"NOT_MERGEABLE", "BLOCKED", "NON_MATCH"}),
             auto_proposal_count=len(auto),
             auto_record_count=sum(len(members) for (members,) in auto),
             review_case_count=review_cases)


def _safe_summary(factory, run_id: str, branch: str) -> None:
    if not trace_enabled():
        return
    try:
        _emit_summary(factory, run_id, branch)
    except Exception as error:
        emit("run.summary", "failed", run_id=run_id, branch=branch,
             error_type=type(error).__name__)


def _run_selected_pipeline(run_id, factory, progress, *, scoring_config=None):
    from masterone.mdm.engine_adapter import feature
    from masterone.mdm.models import ResolutionPublication
    with factory() as db:
        has_generation = db.get(ResolutionPublication, run_id) is not None
    if feature("generation_staging_enabled") or feature("stream_decide_materialize_enabled") or feature("pipeline_resume_enabled") or has_generation:
        emit("pipeline.branch", "selected", branch="generation_staging")
        return _run_staged_pipeline(run_id, factory, progress, scoring_config=scoring_config)
    with span("pipeline.load_metadata"):
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
    if record_count > get_settings().mdm_stage_runner_threshold:
        emit("pipeline.branch", "selected", branch="stage_runner", record_count=record_count)
        from masterone.mdm.pipeline.stage_runner import StageRunner
        runner = StageRunner(run_id=run_id, attempt_id=current_claim().attempt_id if current_claim() else task.id,
                             output_dir=Path(mkdtemp(prefix="p119-attempt-")), db_url=db_url)
        try:
            computed = runner.run_all(records_raw=None, policy_version=policy_version,
                negative_pairs=negative_pairs, return_data=True, scoring_config=scoring_config,
                execution_blocked_pairs=execution_blocked_pairs)
        finally:
            runner.con.close()
    else:
        emit("pipeline.branch", "selected", branch="memory", record_count=record_count)
        with span("pipeline.load_records", record_count=record_count):
            with factory() as db:
                records = [record_payload(item) for item in db.query(ResolutionRecord)
                           .filter_by(run_id=run_id).order_by(ResolutionRecord.id).all()]
        computed = _compute(records, run_id, policy_version, negative_pairs=negative_pairs,
                            scoring_config=scoring_config, execution_blocked_pairs=execution_blocked_pairs)
    progress("persisting", record_count, record_count)
    try:
        with span("pipeline.persist", record_count=record_count):
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
        _safe_summary(factory, run_id, "stage_runner" if record_count > get_settings().mdm_stage_runner_threshold else "memory")
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
        with span("pipeline.generation_begin"):
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
            with span("pipeline.promote"):
                publisher.promote(identity)
            _safe_summary(factory, run_id, "generation_staging")
            return
        if feature("pipeline_resume_enabled") and publisher.resume_ready(identity):
            progress("promoting", record_count, record_count)
            with span("pipeline.promote"):
                publisher.promote(identity)
            _safe_summary(factory, run_id, "generation_resume")
            return
        progress("blocking", 0, record_count)
        if record_count > get_settings().mdm_stage_runner_threshold:
            emit("pipeline.branch", "selected", branch="stage_runner", record_count=record_count)
            from masterone.mdm.pipeline.stage_runner import StageRunner
            # Scratch paths cannot collide across retries; publication checkpoints
            # are separate from these disposable local scoring artifacts.
            scratch = Path(mkdtemp(prefix=f"p119-{identity.generation_id}-{identity.attempt_id}-"))
            runner = StageRunner(run_id=run_id, attempt_id=identity.attempt_id, output_dir=scratch, db_url=db_url)
            computed = runner.run_all(records_raw=None, policy_version=policy_version,
                negative_pairs=negative_pairs, return_data=True, scoring_config=scoring_config,
                execution_blocked_pairs=vetoes)
        else:
            emit("pipeline.branch", "selected", branch="memory", record_count=record_count)
            with span("pipeline.load_records", record_count=record_count):
                with factory() as db:
                    records = [record_payload(item) for item in db.query(ResolutionRecord)
                               .filter_by(run_id=run_id).order_by(ResolutionRecord.id)]
            computed = _compute(records, run_id, policy_version, negative_pairs=negative_pairs,
                scoring_config=scoring_config, execution_blocked_pairs=vetoes, _attempt_id=identity.attempt_id,
                _checkpoint_factory=factory)
        progress("staging", record_count, record_count)
        with span("pipeline.stage", record_count=record_count):
            publisher.stage(identity, computed)
        progress("promoting", record_count, record_count)
        with span("pipeline.promote", record_count=record_count):
            publisher.promote(identity)
        _safe_summary(factory, run_id, "generation_staging")
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
