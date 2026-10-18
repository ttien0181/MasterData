"""SQL aggregates for the session dashboard; never materialize source rows in Python."""

from sqlalchemy import case, func, select, true, union_all
from sqlalchemy.orm import Session

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
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        entries = func.json_array_elements(ResolutionPair.field_evidence).table_valued("value").lateral()
        field = entries.c.value.op("->>")("field")
        state = entries.c.value.op("->>")("state")
    else:  # SQLite is the isolated test database.
        entries = func.json_each(ResolutionPair.field_evidence).table_valued("key", "value")
        field = func.json_extract(entries.c.value, "$.field")
        state = func.json_extract(entries.c.value, "$.state")
    rows = pending_pairs(db, run_id).join(entries, true()).with_entities(
        field.label("field"),
        func.count(func.distinct(case((state.in_(["CF", "DF"]), ResolutionPair.id)))).label("diff_count"),
        func.count(func.distinct(case((state == "CF", ResolutionPair.id)))).label("conflict_count"),
    ).filter(field.in_(FIELDS)).group_by(field).all()
    diff_counts = {r[0]: r[1] for r in rows}
    cf_counts = {r[0]: r[2] for r in rows}
    return {"basis": "pending_review_pairs", "unit": "pair", "review_pair_count": pending_pairs(db, run_id).count(),
            "fields": [{"field": field, "count": diff_counts.get(field, 0),
                        "difference_count": diff_counts.get(field, 0),
                        "conflict_count": cf_counts.get(field, 0)}
                       for field in sorted(FIELDS, key=lambda name: (-diff_counts.get(name, 0), -cf_counts.get(name, 0), name))]}


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
