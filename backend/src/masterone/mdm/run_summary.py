"""Overview figures stored per run.

Overview reads one stored row instead of running a dozen counts over pairs, proposals,
review cases and the records table on every request:

- `source_counts` is written when the run is created at commit.
- `payload` (review and pipeline figures) is computed when the pipeline publishes, and
  again on the next read after any write to the run's pairs, proposals or review cases.
  The session hook in models.py bumps `version` on those writes; `payload_version`
  records which version the payload was computed at.
"""

from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from masterone.mdm.models import (
    ResolutionPair,
    ResolutionProposal,
    ResolutionRecord,
    ResolutionRun,
    ResolutionRunSummary,
)
from masterone.mdm.resolution_items import _review_groups, separate_record_count
from masterone.mdm.resolution_pairs import pending_pairs


def source_counts_for(db: Session, run_id: str) -> dict[str, int]:
    rows = (db.query(ResolutionRecord.source_system, func.count(ResolutionRecord.id))
            .filter_by(run_id=run_id).group_by(ResolutionRecord.source_system).all())
    return dict(rows)


def compute_payload(db: Session, run: ResolutionRun) -> dict:
    run_id = run.id
    counts = {
        "auto": db.query(ResolutionProposal).filter(
            ResolutionProposal.run_id == run_id,
            ResolutionProposal.status.in_(["AUTO_READY", "REVIEW_APPROVED", "REVIEW_STAGED_CONFLICT"])).count(),
        "rejected": db.query(ResolutionPair).filter(
            ResolutionPair.run_id == run_id,
            or_(ResolutionPair.pair_decision.in_(["NON_MATCH", "NOT_MERGEABLE"]),
                ResolutionPair.user_decision == "REJECTED"),
        ).count(),
        "review_cases": len(_review_groups(db, run_id)),
        "manual": db.query(ResolutionProposal).filter_by(run_id=run_id, status="REVIEW_REQUIRED").count(),
    }
    counts["auto"] += db.query(ResolutionPair).filter_by(run_id=run_id, staged_for_merge=True).count()
    counts["review"] = counts["review_cases"] + counts["manual"]
    counts["review_pairs"] = pending_pairs(db, run_id).count()
    counts["hard_blocked"] = db.query(ResolutionPair).filter_by(run_id=run_id, pair_decision="NOT_MERGEABLE").count()
    counts["group_a"] = db.query(ResolutionPair).filter_by(run_id=run_id, primary_group="A").count()
    counts["group_c"] = db.query(ResolutionPair).filter_by(run_id=run_id, primary_group="C").count()
    counts["group_d"] = db.query(ResolutionPair).filter_by(run_id=run_id, primary_group="D").count()
    counts["separate"] = separate_record_count(db, run_id, run.record_count)
    eligible_merge_group_count = db.query(ResolutionProposal).filter(
        ResolutionProposal.run_id == run_id,
        ResolutionProposal.status.in_(["AUTO_READY", "REVIEW_APPROVED"]),
    ).count()
    estimated_entity_count = counts["separate"] + eligible_merge_group_count
    return {
        "counts": counts,
        "eligible_merge_group_count": eligible_merge_group_count,
        "estimated_entity_count": estimated_entity_count,
        "duplicate_record_count": max(0, run.record_count - estimated_entity_count),
    }


def summary_for(db: Session, run: ResolutionRun) -> ResolutionRunSummary:
    """Return the stored summary, creating it for older runs and recomputing a stale payload."""
    summary = db.get(ResolutionRunSummary, run.id)
    if summary is None:
        summary = ResolutionRunSummary(run_id=run.id, source_counts=source_counts_for(db, run.id), version=0)
        db.add(summary)
        try:
            db.commit()
        except IntegrityError:
            # A concurrent request created the same row first; use it.
            db.rollback()
            summary = db.get(ResolutionRunSummary, run.id)
            if summary is None:
                raise RuntimeError("Run summary could not be created")
    if summary.payload is None or summary.payload_version != summary.version:
        version = summary.version
        summary.payload = compute_payload(db, run)
        summary.payload_version = version
        db.commit()
    return summary


def refresh_payload(db: Session, run: ResolutionRun) -> None:
    """Recompute the payload inside the publish transaction, after the pairs it describes are written."""
    summary = db.get(ResolutionRunSummary, run.id)
    if summary is None:
        summary = ResolutionRunSummary(run_id=run.id, source_counts=source_counts_for(db, run.id), version=0)
        db.add(summary)
    version = summary.version
    summary.payload = compute_payload(db, run)
    summary.payload_version = version
