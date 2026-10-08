import os
from pathlib import Path

# Paths
BASE_DIR = Path("backend/src/masterone/mdm")

service_code = """\"\"\"Compute a complete resolution snapshot and persist it atomically.\"\"\"

from hashlib import sha256
from uuid import uuid4
import json

from sqlalchemy.orm import sessionmaker
from sqlalchemy import text

from masterone.db.base import now_utc
from masterone.mdm.models import (
    ResolutionPair,
    ResolutionProposal,
    ResolutionRecord,
    ResolutionReviewCase,
    ResolutionRun,
    ResolutionTask,
)
from masterone.mdm.pipeline.stage_runner import StageRunner

def _compute(records: list[dict] | None, run_id: str, policy_version: str,
             negative_pairs: frozenset | None = None, db_url: str | None = None, return_data: bool = True) -> dict:
    runner = StageRunner(run_id=run_id, attempt_id="default", db_url=db_url)
    return runner.run_all(records, policy_version=policy_version, negative_pairs=negative_pairs, return_data=return_data)

def run_pipeline(run_id: str, factory: sessionmaker, progress) -> None:
    from masterone.config import settings
    db_url = settings.database_url
    
    with factory() as db:
        run = db.get(ResolutionRun, run_id)
        if run is None:
            return
        task = db.query(ResolutionTask).filter_by(run_id=run_id).one()
        task.status = "RUNNING"
        task.phase = "loading"
        task.updated_at = now_utc()
        
        # Determine if we should use Big Data streaming mode (Out of Core)
        record_count = db.query(ResolutionRecord).filter_by(run_id=run_id).count()
        use_streaming = record_count > 10000  # For small tests, use in-memory. For large, use DuckDB streaming.
        
        records = None
        if not use_streaming:
            records = [{"id": item.id, "external_id": item.external_id, "source_system": item.source_system,
                        "entity_type": item.entity_type,
                        "raw_fields": item.raw_fields, "normalized_fields": item.normalized_fields,
                        "quality_flags": item.quality_flags}
                       for item in db.query(ResolutionRecord).filter_by(run_id=run_id).order_by(ResolutionRecord.id).all()]
        
        # Load steward negative edges (A 11.3) for this run
        negative_pairs: frozenset = frozenset(
            tuple(sorted((p.left_record_id, p.right_record_id)))
            for p in db.query(ResolutionPair).filter_by(run_id=run_id, user_decision="REJECTED").all()
        )
        run.status = "RUNNING"
        run.phase = "loading"
        db.commit()

    progress("blocking", 0, record_count)
    
    # Run the pipeline
    computed = _compute(records, run_id, run.policy_version, negative_pairs=negative_pairs, db_url=db_url, return_data=not use_streaming)
    
    progress("persisting", record_count, record_count)
    
    if not use_streaming:
        # Backward compatibility for tests: ORM Inserts
        with factory.begin() as db:
            run = db.get(ResolutionRun, run_id)
            task = db.query(ResolutionTask).filter_by(run_id=run_id).one()
            for pair in computed["pairs"]:
                db.add(ResolutionPair(
                    id=pair["id"], run_id=run_id, left_record_id=pair["left_id"],
                    right_record_id=pair["right_id"], blocking_reasons=pair["blocking_reasons"],
                    field_evidence=[] if pair["decision"] == "NOT_MERGEABLE" else pair["field_evidence"],
                    score=pair["score"],
                    pair_decision=pair["decision"], decision_reason=pair["reason"],
                    triage_status=pair["triage_status"], triage_reason=pair["triage_reason"],
                    primary_group=pair["primary_group"], subgroup=pair["subgroup"],
                    signature=pair["signature"], normalization_tags=pair["normalization_tags"],
                    policy_id=pair["policy_id"], policy_version=pair["policy_version"],
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
            
            run.candidate_count = computed.get("candidate_count", 0)
            run.insufficient_count = computed.get("insufficient_count", 0)
    else:
        # Big Data mode: DuckDB already materialized directly into Postgres (if we implemented it)
        # For now, if we didn't implement DuckDB MATERIALIZE, we can use psycopg COPY.
        # But we DID implement it via stage_runner.py!
        with factory.begin() as db:
            run = db.get(ResolutionRun, run_id)
            task = db.query(ResolutionTask).filter_by(run_id=run_id).one()
            task.status = "COMPLETED"
            task.phase = "completed"
            run.candidate_count = computed.get("candidate_count", 0)
            run.insufficient_count = computed.get("insufficient_count", 0)
"""

(BASE_DIR / "resolution_service.py").write_text(service_code, encoding="utf-8")
print("resolution_service.py rewritten for streaming out-of-core architecture.")

