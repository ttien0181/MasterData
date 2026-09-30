"""Compute a complete resolution snapshot and persist it atomically."""

from hashlib import sha256
from uuid import uuid4

from sqlalchemy.orm import sessionmaker

from masterone.db.base import now_utc
from masterone.mdm.models import (ResolutionPair, ResolutionProposal, ResolutionRecord,
                                  ResolutionReviewCase, ResolutionRun, ResolutionTask)
from masterone.mdm.pipeline.blocking import generate_candidates
from masterone.mdm.pipeline.clustering import build_proposals
from masterone.mdm.pipeline.matching import score_pair
from masterone.mdm.pipeline.review_grouping import classify_pair


def _compute(records: list[dict], run_id: str, policy_version: str) -> dict:
    candidate_blocks = generate_candidates(records)
    by_id = {record["id"]: record for record in records}
    pairs = []
    for (left_id, right_id), reasons in candidate_blocks.items():
        result = score_pair(by_id[left_id], by_id[right_id], reasons)
        pair_id = sha256(f"{run_id}:{left_id}:{right_id}:{policy_version}".encode()).hexdigest()
        pairs.append({"id": pair_id, "left_id": left_id, "right_id": right_id,
                      "blocking_reasons": sorted(reasons), **result})
    clusters = build_proposals(records, pairs, run_id, policy_version)
    review_cases = []
    for case in clusters["review_cases"]:
        pair = next((item for item in pairs if item["id"] in case["pair_ids"]), None)
        if pair is None:
            continue
        classification = classify_pair(pair, by_id)
        if classification is None:
            continue
        review_cases.append({**case, **classification})
    return {"pairs": pairs, "proposals": clusters["proposals"], "review_cases": review_cases,
            "candidate_count": len(candidate_blocks)}


def run_pipeline(run_id: str, factory: sessionmaker, progress) -> None:
    with factory() as db:
        run = db.get(ResolutionRun, run_id)
        if run is None:
            return
        task = db.query(ResolutionTask).filter_by(run_id=run_id).one()
        task.status = "RUNNING"
        task.phase = "loading"
        task.updated_at = now_utc()
        records = [{"id": item.id, "external_id": item.external_id, "source_system": item.source_system,
                    "raw_fields": item.raw_fields, "normalized_fields": item.normalized_fields}
                   for item in db.query(ResolutionRecord).filter_by(run_id=run_id).all()]
        run.status = "RUNNING"
        run.phase = "loading"
        db.commit()

    progress("blocking", 0, len(records))
    computed = _compute(records, run_id, run.policy_version)
    progress("persisting", len(records), len(records))
    with factory.begin() as db:
        run = db.get(ResolutionRun, run_id)
        task = db.query(ResolutionTask).filter_by(run_id=run_id).one()
        for pair in computed["pairs"]:
            db.add(ResolutionPair(
                id=pair["id"], run_id=run_id, left_record_id=pair["left_id"],
                right_record_id=pair["right_id"], blocking_reasons=pair["blocking_reasons"],
                field_evidence=pair["field_evidence"], score=pair["score"],
                pair_decision=pair["decision"], decision_reason=pair["reason"],
                triage_status=pair["triage_status"], triage_reason=pair["triage_reason"],
            ))
        for proposal in computed["proposals"]:
            db.add(ResolutionProposal(id=proposal["id"], run_id=run_id,
                                      member_record_ids=proposal["member_record_ids"],
                                      golden_fields=proposal["golden_fields"], status=proposal["status"],
                                      revision=proposal["revision"]))
        for case in computed["review_cases"]:
            db.add(ResolutionReviewCase(id=case["id"], run_id=run_id, pair_ids=case["pair_ids"],
                                        member_record_ids=case["member_record_ids"],
                                        primary_group=case["primary_group"], subgroup=case["subgroup"],
                                        signature_hash=case["signature_hash"],
                                        triage_reason=case["triage_reason"]))
        task.status = "COMPLETED"
        task.phase = "completed"
        task.processed_count = len(records)
        task.total_count = len(records)
        task.updated_at = now_utc()
        run.status = "COMPLETED"
        run.phase = "completed"
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
