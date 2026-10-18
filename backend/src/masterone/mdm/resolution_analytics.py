"""SQL aggregates for the session dashboard; never materialize source rows in Python."""

from sqlalchemy import case, func, select, union_all
from sqlalchemy.orm import Session, aliased

from masterone.mdm.models import ResolutionPair, ResolutionRecord
from masterone.mdm.resolution_pairs import pending_pairs

FIELDS = ("name", "phone", "email", "address")
ASSESSED = ("VALID", "MISSING", "INVALID", "PARTIAL", "PLACEHOLDER", "SHARED")


def decision_distribution(db: Session, run_id: str):
    # Human decisions don't rewrite triage_status. Review takes precedence when a
    # record occurs in both a safe edge and a review edge; every source row counts once.
    priority = case((ResolutionPair.triage_status == "REVIEW", 2), else_=1)
    condition = (ResolutionPair.run_id == run_id) & ResolutionPair.triage_status.in_(
        ["REVIEW", "HIGH_CONFIDENCE_PROPOSAL", "AUTO_MERGE"])
    edges = union_all(
        select(ResolutionPair.left_record_id.label("record_id"), priority.label("priority")).where(condition),
        select(ResolutionPair.right_record_id.label("record_id"), priority.label("priority")).where(condition),
    ).subquery()
    classification = select(edges.c.record_id, func.max(edges.c.priority).label("priority")).group_by(
        edges.c.record_id).subquery()
    total, automatic, review = db.execute(select(
        func.count(ResolutionRecord.id),
        func.coalesce(func.sum(case((classification.c.priority == 1, 1), else_=0)), 0),
        func.coalesce(func.sum(case((classification.c.priority == 2, 1), else_=0)), 0),
    ).select_from(ResolutionRecord).outerjoin(classification, classification.c.record_id == ResolutionRecord.id)
        .where(ResolutionRecord.run_id == run_id)).one()
    return {"basis": "initial_classification", "unit": "source_record", "total": total,
            "automatic": automatic, "review": review, "no_duplicate_detected": total - automatic - review,
            "automatic_percent": round(automatic / total * 100, 1) if total else 0}


def field_conflicts(db: Session, run_id: str):
    left = aliased(ResolutionRecord)
    right = aliased(ResolutionRecord)
    # Match the raw-value comparison used by the "Cần xem xét" pair table.
    counts = [func.coalesce(func.sum(case((
        func.coalesce(left.raw_fields[field].as_string(), "") !=
        func.coalesce(right.raw_fields[field].as_string(), ""), 1), else_=0)), 0)
        for field in FIELDS]
    total, *differences = pending_pairs(db, run_id).join(
        left, ResolutionPair.left_record_id == left.id).join(
        right, ResolutionPair.right_record_id == right.id).with_entities(
        func.count(ResolutionPair.id), *counts).one()
    by_field = dict(zip(FIELDS, differences))
    return {"basis": "pending_review_pairs", "unit": "pair", "review_pair_count": total,
            "fields": [{"field": field, "count": by_field[field]}
                       for field in sorted(FIELDS, key=lambda name: (-by_field[name], name))]}


def source_quality(db: Session, run_id: str):
    qualities = [ResolutionRecord.normalized_fields["_field_quality"][field].as_string() for field in FIELDS]
    valid = sum(case((quality == "VALID", 1), else_=0) for quality in qualities)
    assessed = sum(case((quality.in_(ASSESSED), 1), else_=0) for quality in qualities)
    rows = db.execute(select(ResolutionRecord.source_system, func.count(ResolutionRecord.id),
        func.sum(valid), func.sum(assessed)).where(ResolutionRecord.run_id == run_id)
        .group_by(ResolutionRecord.source_system).order_by(ResolutionRecord.source_system)).all()
    return {"basis": "valid_assessed_fields", "fields": list(FIELDS), "sources": [
        {"source": source, "record_count": count, "valid_fields": good, "assessed_fields": checked,
         "expected_fields": count * len(FIELDS),
         "score_percent": round(good / checked * 100, 1) if checked else None,
         "coverage_percent": round(checked / (count * len(FIELDS)) * 100, 1) if count else 0}
        for source, count, good, checked in rows]}


def session_analytics(db: Session, run_id: str):
    return {"run_id": run_id, "metric_version": "1",
            "decisions": decision_distribution(db, run_id), "conflicts": field_conflicts(db, run_id),
            "quality": source_quality(db, run_id)}
