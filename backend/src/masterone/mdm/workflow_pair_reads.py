"""Bounded source hydration and SQL summaries for the pair review workspace."""
from sqlalchemy import and_, exists, func, or_

from masterone.mdm.data_profile import record_read
from masterone.mdm.models import ResolutionNegativeEdge, ResolutionRecord
from masterone.mdm.workflow_models import WorkflowPair
from masterone.mdm.workflow_assessment_reads import active_assessment, effective_column
from masterone.mdm.workflow_review import negative_links, pair_read, scoped_pairs

UNRESOLVED = ('UNDECIDED', 'DEFERRED')
BLOCKED = ('NOT_MERGEABLE', 'BLOCKED', 'NON_MATCH')


def pair_page(db, run_id, limit, cursor, offset, filters):
    query = scoped_pairs(db, run_id, filters)
    total = query.count()
    if offset is not None:
        query = query.order_by(WorkflowPair.id).offset(offset)
    else:
        if cursor:
            query = query.filter(WorkflowPair.id > cursor)
        query = query.order_by(WorkflowPair.id)
    rows = query.limit(limit + 1).all()
    page = rows[:limit]
    ids = {p.baseline[key] for p in page for key in ('left_id', 'right_id')}
    records = {r.id: record_read(r) for r in db.query(ResolutionRecord).filter(
        ResolutionRecord.run_id == run_id, ResolutionRecord.id.in_(ids))}
    links = negative_links(db, run_id, page)
    items = []
    for pair in page:
        item = pair_read(pair)
        if tuple(sorted((pair.baseline['left_id'], pair.baseline['right_id']))) in links:
            item['eligibility'] = dict(manual_allowed=False, auto_allowed=False, blocked_reasons=['STEWARD_NEGATIVE_DECISION'])
        item.update(left_record=records.get(pair.baseline['left_id']), right_record=records.get(pair.baseline['right_id']))
        items.append(item)
    return dict(items=items, total=total, offset=offset or 0,
                next_cursor=page[-1].id if len(rows) > limit else None)


def aggregate(db, query):
    group = effective_column()['primary_group'].as_string()
    subgroup = effective_column()['subgroup'].as_string()
    baseline = effective_column()['decision'].as_string()
    left = WorkflowPair.baseline['left_id'].as_string()
    right = WorkflowPair.baseline['right_id'].as_string()
    negative = exists().where(ResolutionNegativeEdge.run_id == WorkflowPair.run_id,
        ResolutionNegativeEdge.active.is_(True), or_(
            and_(ResolutionNegativeEdge.left_record_id == left, ResolutionNegativeEdge.right_record_id == right),
            and_(ResolutionNegativeEdge.left_record_id == right, ResolutionNegativeEdge.right_record_id == left)))
    return query.with_entities(group, subgroup, baseline, WorkflowPair.decision, negative, func.count()).group_by(
        group, subgroup, baseline, WorkflowPair.decision, negative).all()


def pair_summary(db, run):
    counters = dict(total=0, needs_review=0, accepted=0, denied=0, kept_separate=0, deferred=0,
                    blocked=0, manual_available=0, auto_available=0)
    groups = {}
    for group, subgroup, baseline, decision, negative, count in aggregate(db, scoped_pairs(db, run.id)):
        counters['total'] += count
        counters['needs_review'] += count if decision == 'DEFERRED' or (decision == 'UNDECIDED' and baseline not in (*BLOCKED, 'MERGEABLE')) else 0
        for value, key in [('ACCEPTED', 'accepted'), ('NON_MATCH', 'denied'), ('REJECTED', 'kept_separate'), ('DEFERRED', 'deferred')]:
            counters[key] += count if decision == value else 0
        counters['blocked'] += count if baseline in BLOCKED or negative or decision in ('REJECTED', 'NON_MATCH') else 0
        eligible = decision in UNRESOLVED and baseline not in BLOCKED and not negative
        counters['manual_available'] += count if eligible else 0
        counters['auto_available'] += count if eligible and baseline == 'MERGEABLE' and decision == 'UNDECIDED' else 0
        key = group or 'UNCLASSIFIED'
        item = groups.setdefault(key, dict(key=key, total=0, pending=0, subgroups={}))
        item['total'] += count
        item['pending'] += count if decision in UNRESOLVED and baseline not in BLOCKED else 0
        if subgroup:
            item['subgroups'][subgroup] = item['subgroups'].get(subgroup, 0) + count
    assessment = active_assessment(db, run.id)
    return {**counters, 'decision_revision': run.decision_revision,
        'assessment_id': assessment.id if assessment else None,
        'assessment': assessment.configuration if assessment else None,
        'groups': [{**g, 'subgroups': [dict(key=k, total=v) for k, v in sorted(g['subgroups'].items())]} for g in groups.values()]}


def bulk_preview(db, run, request):
    from masterone.mdm.workflow_bulk import selected_pairs
    counts = dict(total=0, applicable=0, skipped=0, blocked=0)
    for _, _, baseline, decision, negative, count in aggregate(db, selected_pairs(db, run.id, request.scope.model_dump()).order_by(None)):
        counts['total'] += count
        if decision not in UNRESOLVED:
            counts['skipped'] += count
        elif request.decision == 'merge' and (baseline in BLOCKED or negative or
                (request.auto_only and (baseline != 'MERGEABLE' or decision != 'UNDECIDED'))):
            counts['blocked'] += count
        else:
            counts['applicable'] += count
    return {**counts, 'workflow_revision': run.workflow_revision, 'decision_revision': run.decision_revision}
