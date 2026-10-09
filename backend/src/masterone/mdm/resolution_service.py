"""Compute a complete resolution snapshot and persist it atomically."""

from hashlib import sha256
from uuid import uuid4

from sqlalchemy import insert as sa_insert
from sqlalchemy.orm import sessionmaker

from masterone.db.base import now_utc
from masterone.mdm.models import (
    ResolutionPair,
    ResolutionProposal,
    ResolutionRecord,
    ResolutionReviewCase,
    ResolutionRun,
    ResolutionTask,
)
from masterone.mdm.pipeline.blocking import generate_candidates
from masterone.mdm.pipeline.clustering import build_proposals
from masterone.mdm.pipeline.matching import score_pair, value_frequencies
from masterone.mdm.pipeline.review_grouping import classify_pair
from masterone.mdm.pipeline.semantic_address import enrich_pairs_with_semantic


def _score_chunk(chunk, by_id, frequencies, negative_pairs, policy_version, run_id):
    from masterone.mdm.pipeline.matching import score_pair
    from hashlib import sha256
    results = []
    insufficient = 0
    for left_id, right_id, reasons in chunk:
        result = score_pair(by_id[left_id], by_id[right_id], reasons,
                           frequencies, negative_pairs=negative_pairs)
        if result["decision"] == "INSUFFICIENT_EVIDENCE":
            insufficient += 1
            continue
        pair_id = sha256(f"{run_id}:{left_id}:{right_id}:{policy_version}".encode()).hexdigest()
        results.append({"id": pair_id, "left_id": left_id, "right_id": right_id,
                        "blocking_reasons": sorted(reasons), **result})
    return results, insufficient


def _compute(records: list[dict], run_id: str, policy_version: str,
             negative_pairs: frozenset | None = None) -> dict:
    candidate_blocks = generate_candidates(records)
    by_id = {record["id"]: record for record in records}
    frequencies = value_frequencies(records)
    pairs = []
    insufficient_count = 0
    
    import os
    from concurrent.futures import ProcessPoolExecutor
    
    candidate_list = [(left, right, reasons) for (left, right), reasons in sorted(candidate_blocks.items())]
    chunks = [candidate_list[i:i+5000] for i in range(0, len(candidate_list), 5000)]
    
    with ProcessPoolExecutor(max_workers=min(os.cpu_count() or 4, 4)) as pool:
        futures = [pool.submit(_score_chunk, chunk, by_id, frequencies, negative_pairs, policy_version, run_id) for chunk in chunks]
        for f in futures:
            res, insuff = f.result()
            pairs.extend(res)
            insufficient_count += insuff
    llm_stats = enrich_pairs_with_semantic(pairs, by_id)
    clusters = build_proposals(records, pairs, run_id, policy_version, negative_pairs=negative_pairs)
    review_cases = []
    pairs_by_id = {item["id"]: item for item in pairs}
    for case in clusters["review_cases"]:
        pair = pairs_by_id.get(case["pair_ids"][0])
        if pair is None:
            continue
        classification = classify_pair(pair, by_id)
        if classification is None:
            continue
        review_cases.append({**case, **classification})
    return {"pairs": pairs, "proposals": clusters["proposals"], "review_cases": review_cases,
            "candidate_count": len(candidate_blocks), "insufficient_count": insufficient_count,
            "llm_stats": llm_stats}


def run_pipeline(run_id: str, factory: sessionmaker, progress) -> None:
    with factory() as db:
        run = db.get(ResolutionRun, run_id)
        if run is None:
            return
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

    if record_count > 10000:
        progress("blocking", 0, record_count)
        with factory() as db:
            negative_pairs: frozenset = frozenset(
                tuple(sorted((p.left_record_id, p.right_record_id)))
                for p in db.query(ResolutionPair).filter_by(run_id=run_id, user_decision="NON_MATCH").all()
            )
        from masterone.mdm.pipeline.stage_runner import StageRunner
        runner = StageRunner(run_id=run_id, attempt_id=task.id, db_url=db_url)
        computed = runner.run_all(records_raw=None, policy_version=policy_version, negative_pairs=negative_pairs, return_data=False)
        
        progress("persisting", record_count, record_count)
        with factory.begin() as db:
            run = db.get(ResolutionRun, run_id)
            task = db.query(ResolutionTask).filter_by(run_id=run_id).one()
            task.status = "COMPLETED"
            task.phase = "completed"
            task.processed_count = record_count
            task.total_count = record_count
            task.updated_at = now_utc()
            run.status = "COMPLETED"
            run.phase = "completed"
            run.candidate_count = computed.get("candidate_count", 0)
            run.insufficient_count = computed.get("insufficient_count", 0)
            run.completed_at = now_utc()
        return

    with factory() as db:
        records = [{"id": item.id, "external_id": item.external_id, "source_system": item.source_system,
                    "entity_type": item.entity_type,
                    "raw_fields": item.raw_fields, "normalized_fields": item.normalized_fields,
                    "quality_flags": item.quality_flags}
                   for item in db.query(ResolutionRecord).filter_by(run_id=run_id).order_by(ResolutionRecord.id).all()]
        negative_pairs: frozenset = frozenset(
            tuple(sorted((p.left_record_id, p.right_record_id)))
            for p in db.query(ResolutionPair).filter_by(run_id=run_id, user_decision="NON_MATCH").all()
        )

    progress("blocking", 0, len(records))
    computed = _compute(records, run_id, policy_version, negative_pairs=negative_pairs)
    progress("persisting", len(records), len(records))
    with factory.begin() as db:
        run = db.get(ResolutionRun, run_id)
        task = db.query(ResolutionTask).filter_by(run_id=run_id).one()
        if computed["pairs"]:
            db.execute(sa_insert(ResolutionPair), [
                {
                    "id": p["id"], "run_id": run_id,
                    "left_record_id": p["left_id"], "right_record_id": p["right_id"],
                    "blocking_reasons": p["blocking_reasons"],
                    "field_evidence": [] if p["decision"] == "NON_MATCH" else p["field_evidence"],
                    "score": p["score"],
                    "pair_decision": p["decision"], "decision_reason": p["reason"],
                    "triage_status": p["triage_status"], "triage_reason": p["triage_reason"],
                    "primary_group": p["primary_group"], "subgroup": p["subgroup"],
                    "signature": p["signature"], "normalization_tags": p["normalization_tags"],
                    "policy_id": p["policy_id"], "policy_version": p["policy_version"],
                }
                for p in computed["pairs"]
            ])
        if computed["proposals"]:
            db.execute(sa_insert(ResolutionProposal), [
                {
                    "id": p["id"], "run_id": run_id,
                    "member_record_ids": p["member_record_ids"],
                    "golden_fields": p["golden_fields"],
                    "status": p["status"], "revision": p["revision"],
                }
                for p in computed["proposals"]
            ])
        if computed["review_cases"]:
            db.execute(sa_insert(ResolutionReviewCase), [
                {
                    "id": c["id"], "run_id": run_id,
                    "pair_ids": c["pair_ids"], "member_record_ids": c["member_record_ids"],
                    "primary_group": c["primary_group"], "subgroup": c["subgroup"],
                    "signature_hash": c["signature_hash"], "triage_reason": c["triage_reason"],
                }
                for c in computed["review_cases"]
            ])
        task.status = "COMPLETED"
        task.phase = "completed"
        task.processed_count = len(records)
        task.total_count = len(records)
        task.updated_at = now_utc()
        run.status = "COMPLETED"
        run.phase = "completed"
        run.candidate_count = computed["candidate_count"]
        run.insufficient_count = computed["insufficient_count"]
        run.completed_at = now_utc()


def mark_failed(run_id: str, factory: sessionmaker) -> None:
    with factory.begin() as db:
        task = db.query(ResolutionTask).filter_by(run_id=run_id).one_or_none()
        run = db.get(ResolutionRun, run_id)
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
        run = db.get(ResolutionRun, run_id)
        existing = db.query(ResolutionTask).filter_by(run_id=run_id).one_or_none()
        if existing is None:
            task = ResolutionTask(id=str(uuid4()), run_id=run_id, status="QUEUED")
            db.add(task)
        elif existing.status in {"QUEUED", "RUNNING", "COMPLETED"}:
            return existing
        else:
            task = existing
            task.status = "QUEUED"
            task.phase = "queued"
            task.error_code = None
            task.updated_at = now_utc()
        run.status = "QUEUED"
        run.phase = "queued"
    return task
