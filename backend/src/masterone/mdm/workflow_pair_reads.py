"""Bounded source hydration and SQL summaries for the pair review workspace."""
from sqlalchemy import and_, case, exists, func, or_

from masterone.mdm.data_profile import record_read
from masterone.mdm.models import ResolutionNegativeEdge, ResolutionRecord
from masterone.mdm.workflow_assessment_reads import active_assessment, effective_column
from masterone.mdm.workflow_models import WorkflowPair
from masterone.mdm.workflow_review import negative_links, pair_read, scoped_pairs
from masterone.mdm.workflow_review_rules import review_required

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


def pair_detail(db, run, pair_id):
    from fastapi import HTTPException

    from masterone.mdm.workflow_cluster_reads import generation_metadata
    from masterone.mdm.workflow_models import WorkflowCluster, WorkflowGeneration, WorkflowMember
    pair = db.query(WorkflowPair).filter_by(run_id=run.id, id=pair_id).one_or_none()
    if pair is None:
        raise HTTPException(404, 'Pair not found')
    item = pair_read(pair)
    ids = [pair.baseline[key] for key in ('left_id', 'right_id')]
    records = {record.id: record_read(record) for record in db.query(ResolutionRecord).filter(
        ResolutionRecord.run_id == run.id, ResolutionRecord.id.in_(ids))}
    item.update(left_record=records.get(ids[0]), right_record=records.get(ids[1]))
    if tuple(sorted(ids)) in negative_links(db, run.id, [pair]):
        item['eligibility'] = dict(manual_allowed=False, auto_allowed=False, blocked_reasons=['STEWARD_NEGATIVE_DECISION'])
    if run.cluster_revision:
        metadata = generation_metadata(db, run)
        memberships = db.query(WorkflowMember.record_id, WorkflowCluster).join(
            WorkflowCluster, WorkflowCluster.id == WorkflowMember.cluster_id).join(
            WorkflowGeneration, WorkflowGeneration.id == WorkflowMember.generation_id).filter(
                WorkflowGeneration.run_id == run.id, WorkflowGeneration.revision == run.cluster_revision,
                WorkflowMember.record_id.in_(ids)).all()
        groups = {rid: {'cluster_id': group.id, 'member_count': len(group.payload['member_record_ids'])}
                  for rid, group in memberships}
        item['cluster_context'] = {'revision': run.cluster_revision, 'stale': metadata['stale'],
            'left_cluster': groups.get(ids[0]), 'right_cluster': groups.get(ids[1])}
    return item


def aggregate(db, query):
    group = effective_column()['primary_group'].as_string()
    subgroup = effective_column()['subgroup'].as_string()
    baseline = effective_column()['decision'].as_string()
    triage = effective_column()['triage_status'].as_string()
    left = WorkflowPair.baseline['left_id'].as_string()
    right = WorkflowPair.baseline['right_id'].as_string()
    negative = exists().where(ResolutionNegativeEdge.run_id == WorkflowPair.run_id,
        ResolutionNegativeEdge.active.is_(True), or_(
            and_(ResolutionNegativeEdge.left_record_id == left, ResolutionNegativeEdge.right_record_id == right),
            and_(ResolutionNegativeEdge.left_record_id == right, ResolutionNegativeEdge.right_record_id == left)))
    return query.with_entities(group, subgroup, baseline, triage, WorkflowPair.decision, negative, func.count()).group_by(
        group, subgroup, baseline, triage, WorkflowPair.decision, negative).all()


def pair_summary(db, run):
    counters = dict(total=0, needs_review=0, accepted=0, denied=0, kept_separate=0, deferred=0,
                    blocked=0, manual_available=0, auto_available=0)
    groups = {}
    for group, subgroup, baseline, triage, decision, negative, count in aggregate(db, scoped_pairs(db, run.id)):
        counters['total'] += count
        required = review_required(decision, {'decision': baseline, 'triage_status': triage})
        counters['needs_review'] += count if required else 0
        for value, key in [('ACCEPTED', 'accepted'), ('NON_MATCH', 'denied'), ('REJECTED', 'kept_separate'), ('DEFERRED', 'deferred')]:
            counters[key] += count if decision == value else 0
        counters['blocked'] += count if baseline in BLOCKED or negative or decision in ('REJECTED', 'NON_MATCH') else 0
        eligible = decision in UNRESOLVED and baseline not in BLOCKED and not negative
        counters['manual_available'] += count if eligible else 0
        counters['auto_available'] += count if eligible and baseline == 'MERGEABLE' and decision == 'UNDECIDED' and triage != 'REVIEW' else 0
        key = group or 'UNCLASSIFIED'
        item = groups.setdefault(key, dict(key=key, total=0, pending=0, subgroups={}))
        item['total'] += count
        item['pending'] += count if required else 0
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
    for _, _, baseline, triage, decision, negative, count in aggregate(db, selected_pairs(db, run.id, request.scope.model_dump()).order_by(None)):
        counts['total'] += count
        if decision not in UNRESOLVED:
            counts['skipped'] += count
        elif request.decision == 'merge' and (baseline in BLOCKED or negative or
                (request.auto_only and (baseline != 'MERGEABLE' or decision != 'UNDECIDED' or triage == 'REVIEW'))):
            counts['blocked'] += count
        else:
            counts['applicable'] += count
    new_negative_links = 0
    if request.decision == 'deny':
        left = WorkflowPair.baseline['left_id'].as_string()
        right = WorkflowPair.baseline['right_id'].as_string()
        negative = exists().where(ResolutionNegativeEdge.run_id == run.id, ResolutionNegativeEdge.active.is_(True), or_(
            and_(ResolutionNegativeEdge.left_record_id == left, ResolutionNegativeEdge.right_record_id == right),
            and_(ResolutionNegativeEdge.left_record_id == right, ResolutionNegativeEdge.right_record_id == left)))
        new_negative_links = selected_pairs(db, run.id, request.scope.model_dump()).order_by(None).filter(
            WorkflowPair.decision.in_(UNRESOLVED), ~negative).with_entities(
                case((left < right, left), else_=right), case((left < right, right), else_=left)).distinct().count()
    return {**counts, 'new_negative_links': new_negative_links,
            'workflow_revision': run.workflow_revision, 'decision_revision': run.decision_revision,
            'preview_token': bulk_preview_token(db, run, request.model_dump())}


def bulk_preview_token(db, run, payload):
    from hashlib import sha256

    from masterone.mdm.generation_staging import digest
    from masterone.mdm.workflow_bulk import selected_pairs
    token = sha256(digest({'decision': payload['decision'], 'scope': payload['scope'],
        'auto_only': payload.get('auto_only', False)}).encode())
    query = selected_pairs(db, run.id, payload['scope'])
    for row in query.yield_per(1000):
        token.update(digest({'id': row.id, 'revision': row.revision, 'decision': row.decision,
                             'evaluation': row.assessment or row.baseline}).encode())
    for left, right, identifier in db.query(ResolutionNegativeEdge.left_record_id, ResolutionNegativeEdge.right_record_id,
        ResolutionNegativeEdge.id).filter_by(run_id=run.id, active=True).order_by(ResolutionNegativeEdge.id).yield_per(1000):
        token.update(digest([left, right, identifier]).encode())
    return token.hexdigest()


def validate_bulk_preview(db, run, payload):
    if payload.get('preview_token') and payload['preview_token'] != bulk_preview_token(db, run, payload):
        from masterone.mdm.workflow_service import conflict
        conflict('BULK_PREVIEW_CHANGED')
