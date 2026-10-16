"""Bounded source hydration and SQL summaries for the pair review workspace."""
from sqlalchemy import JSON, String, Boolean, and_, case, column, exists, func, or_, true

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
        from masterone.mdm.workflow_generation_reads import GenerationReader, virtual_cluster_id
        generation = db.query(WorkflowGeneration).filter_by(run_id=run.id, revision=run.cluster_revision).one()
        reader = GenerationReader(db, run, generation)
        if reader.virtual:
            for rid in ids:
                if rid in groups:
                    continue
                try:
                    reader.record(rid)
                except HTTPException:
                    continue
                groups[rid] = {'cluster_id': virtual_cluster_id(generation.id, rid), 'member_count': 1}
        item['cluster_context'] = {'revision': run.cluster_revision, 'stale': metadata['stale'],
            'left_cluster': groups.get(ids[0]), 'right_cluster': groups.get(ids[1])}
    return item


def aggregate(db, query, run_id, visibility=None, taxonomy=None):
    group = effective_column()['primary_group'].as_string()
    subgroup = effective_column()['subgroup'].as_string()
    baseline = effective_column()['decision'].as_string()
    triage = effective_column()['triage_status'].as_string()
    left = WorkflowPair.baseline['left_id'].as_string()
    right = WorkflowPair.baseline['right_id'].as_string()
    # Hash/merge a deduplicated relation instead of executing a correlated lookup
    # for every candidate (and again while grouping). UNION preserves counts when
    # multiple constraints or both endpoint orientations describe the same pair.
    edges = db.query(ResolutionNegativeEdge.run_id.label('run_id'),
        ResolutionNegativeEdge.left_record_id.label('left_id'),
        ResolutionNegativeEdge.right_record_id.label('right_id')).filter(
            ResolutionNegativeEdge.run_id == run_id, ResolutionNegativeEdge.active.is_(True))
    reverse = db.query(ResolutionNegativeEdge.run_id.label('run_id'),
        ResolutionNegativeEdge.right_record_id.label('left_id'),
        ResolutionNegativeEdge.left_record_id.label('right_id')).filter(
            ResolutionNegativeEdge.run_id == run_id, ResolutionNegativeEdge.active.is_(True))
    links = edges.union(reverse).subquery('summary_negative_links')
    if edges.first() is not None:
        query = query.outerjoin(links, and_(links.c.run_id == WorkflowPair.run_id,
            links.c.left_id == left, links.c.right_id == right))
        negative = links.c.run_id.is_not(None)
    else:
        # Avoid parsing baseline endpoint JSON at all in the common no-Deny case.
        negative = WorkflowPair.id.is_(None)  # Primary key is never NULL.
    evaluation = effective_column()
    coordinator = evaluation['coordinator_version'].as_string()
    eligibility = evaluation['eligibility']
    if db.bind.dialect.name == 'postgresql':
        # JSON storage reparses the full evidence payload on every -> extraction.
        # Expand it once per pair; keep only the small fields used by this summary.
        fields = func.json_to_record(evaluation).table_valued(
            column('primary_group', String), column('subgroup', String),
            column('decision', String), column('triage_status', String),
            column('coordinator_version', String), column('eligibility', JSON),
            column('reason',String),column('triage_reason',String),column('agent_disagreement',Boolean)
        ).render_derived(with_types=True).lateral('summary_evaluation')
        query = query.join(fields, true())
        group, subgroup, baseline, triage = (fields.c[name] for name in
            ('primary_group', 'subgroup', 'decision', 'triage_status'))
        coordinator, eligibility = fields.c.coordinator_version, fields.c.eligibility
    if visibility == 'review':
        query = query.filter(func.coalesce(group, '') != 'BLOCKED',
                             func.coalesce(baseline, '').notin_(BLOCKED))
    coordinated = coordinator == 'r1_v1'
    manual = case((coordinated, eligibility['manual_allowed'].as_boolean()), else_=None)
    automatic = case((coordinated, eligibility['auto_allowed'].as_boolean()), else_=None)
    if taxonomy=='actions_v1':
        from masterone.mdm.review_taxonomy import taxonomy_columns
        reason=func.coalesce(fields.c.triage_reason,fields.c.reason) if db.bind.dialect.name=='postgresql' else func.coalesce(evaluation['triage_reason'].as_string(),evaluation['reason'].as_string())
        disagreement=fields.c.agent_disagreement if db.bind.dialect.name=='postgresql' else evaluation['agent_disagreement'].as_boolean()
        group,subgroup=taxonomy_columns(group,subgroup,baseline,triage,reason,disagreement)
    return query.with_entities(group, subgroup, baseline, triage, WorkflowPair.decision, negative, manual, automatic, func.count()).group_by(
        group, subgroup, baseline, triage, WorkflowPair.decision, negative, manual, automatic).all()


def pair_summary(db, run, filters=None):
    counters = dict(total=0, needs_review=0, accepted=0, denied=0, kept_separate=0, deferred=0,
                    blocked=0, manual_available=0, auto_available=0, insufficient=0, unresolved=0)
    groups = {}
    filters = dict(filters or {})
    visibility = filters.pop('visibility', None)
    taxonomy=filters.pop('taxonomy',None)
    for group, subgroup, baseline, triage, decision, negative, manual, automatic, count in aggregate(db, scoped_pairs(db, run.id, filters), run.id, visibility,taxonomy):
        counters['total'] += count
        required = review_required(decision, {'decision': baseline, 'triage_status': triage})
        counters['needs_review'] += count if required else 0
        counters['insufficient'] += count if baseline == 'INSUFFICIENT_EVIDENCE' else 0
        for value, key in [('ACCEPTED', 'accepted'), ('NON_MATCH', 'denied'), ('REJECTED', 'kept_separate'), ('DEFERRED', 'deferred')]:
            counters[key] += count if decision == value else 0
        counters['blocked'] += count if baseline in BLOCKED or negative or decision in ('REJECTED', 'NON_MATCH') else 0
        eligible = decision in UNRESOLVED and baseline not in BLOCKED and not negative and manual is not False
        automatic_eligible = eligible and automatic is not False and baseline == 'MERGEABLE' and decision == 'UNDECIDED' and triage != 'REVIEW'
        counters['unresolved'] += count if decision in UNRESOLVED else 0
        counters['manual_available'] += count if eligible else 0
        counters['auto_available'] += count if automatic_eligible else 0
        key = group or 'UNCLASSIFIED'
        item = groups.setdefault(key, dict(key=key, total=0, pending=0, manual_available=0, auto_available=0, unresolved=0, subgroups={}))
        item['total'] += count
        item['pending'] += count if required else 0
        item['manual_available'] += count if eligible else 0
        item['auto_available'] += count if automatic_eligible else 0
        item['unresolved'] += count if decision in UNRESOLVED else 0
        if subgroup:
            sub = item['subgroups'].setdefault(subgroup, dict(key=subgroup, total=0, manual_available=0, auto_available=0, unresolved=0))
            sub['total'] += count
            sub['manual_available'] += count if eligible else 0
            sub['auto_available'] += count if automatic_eligible else 0
            sub['unresolved'] += count if decision in UNRESOLVED else 0
    assessment = active_assessment(db, run.id)
    return {**counters, 'decision_revision': run.decision_revision,
        'assessment_id': assessment.id if assessment else None,
        'assessment': assessment.configuration if assessment else None,
        'groups': [{**g, 'subgroups': [v for _, v in sorted(g['subgroups'].items())]} for g in groups.values()]}


def summary_merge_counts(db, run, request):
    """Reuse the authorized, revision-bound summary for simple whole scopes.

    Zero eligibility is conclusive for any subset. Positive fast counts are only
    used for action Group A, which cannot contain operative Group D reason gates.
    Individual/excluded pairs and decision filters keep the full preview path.
    """
    scope = request.scope.model_dump()
    filters = {k: v for k, v in (scope.get('filter') or {}).items() if v}
    if (request.decision != 'merge' or request.auto_only or scope.get('pair_ids') is not None
            or scope.get('excluded_pair_ids') or filters.get('resolution')
            or filters.get('visibility') != 'review' or filters.get('taxonomy') != 'actions_v1'
            or filters.get('subgroup') and not filters.get('group')):
        return None
    from masterone.mdm.pair_read_cache import cached_read
    summary_filters = dict(visibility='review', taxonomy='actions_v1')
    data = cached_read(db, run, 'summary', summary_filters, lambda: pair_summary(db, run, summary_filters))
    empty = dict(total=0, manual_available=0, unresolved=0)
    if filters.get('group'):
        data = next((g for g in data['groups'] if g['key'] == filters['group']), empty)
    if filters.get('subgroup'):
        data = next((g for g in data.get('subgroups', []) if g['key'] == filters['subgroup']), empty)
    if 'manual_available' not in data or 'unresolved' not in data:
        return None
    applicable = data['manual_available']
    if applicable and filters.get('group') != 'A':
        return None
    return dict(total=data['total'], applicable=applicable, skipped=data['total']-data['unresolved'],
                blocked=data['unresolved']-applicable, reason_required=0)


def bulk_preview(db, run, request):
    from masterone.mdm.workflow_bulk import selected_pairs, validate_soft_group
    fast = summary_merge_counts(db, run, request)
    if fast is not None:
        result = {**fast, 'new_negative_links': 0, 'group_review_required': False,
                  'workflow_revision': run.workflow_revision, 'decision_revision': run.decision_revision}
        if fast['applicable']:
            result['preview_token'] = bulk_preview_token(db, run, request.model_dump())
        return result
    counts = dict(total=0, applicable=0, skipped=0, blocked=0, reason_required=0)
    scope = request.scope.model_dump()
    selected = selected_pairs(db, run.id, scope).order_by(None)
    pair_ids = None
    if request.decision == 'merge' and (scope.get('filter') or {}).get('group') == 'A':
        # The same expensive taxonomy filter used to scan the entire run twice:
        # once for counters, once for the evidence receipt. Reuse a bounded set
        # of IDs for ordinary Group A batches; larger scopes retain streaming.
        identifiers = selected.with_entities(WorkflowPair.id).limit(10001).all()
        if len(identifiers) <= 10000:
            pair_ids = [row.id for row in identifiers]
            selected = db.query(WorkflowPair).filter(WorkflowPair.run_id == run.id, WorkflowPair.id.in_(pair_ids))
    for group, _, baseline, triage, decision, negative, manual, automatic, count in aggregate(db, selected, run.id):
        counts['total'] += count
        if decision not in UNRESOLVED:
            counts['skipped'] += count
        elif request.decision == 'merge' and (baseline in BLOCKED or negative or manual is False or
                (request.auto_only and (automatic is False or baseline != 'MERGEABLE' or decision != 'UNDECIDED' or triage == 'REVIEW'))):
            counts['blocked'] += count
        else:
            counts['applicable'] += count
            if request.decision == 'merge' and group == 'D':
                counts['reason_required'] += count
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
    group_review_required = bool(counts['reason_required'] and not request.review_group_token)
    if counts['reason_required'] and request.review_group_token:
        validate_soft_group(db, run, request.model_dump())
    result = {**counts, 'new_negative_links': new_negative_links, 'group_review_required': group_review_required,
            'workflow_revision': run.workflow_revision, 'decision_revision': run.decision_revision,
            }
    if counts['applicable']:
        result['preview_token'] = bulk_preview_token(db, run, request.model_dump(), pair_ids=pair_ids)
    return result


def bulk_preview_token(db, run, payload, *, pair_ids=None):
    from hashlib import sha256

    from masterone.mdm.generation_staging import digest
    from masterone.mdm.workflow_bulk import selected_pairs
    token = sha256(digest({'decision': payload['decision'], 'scope': payload['scope'],
        'auto_only': payload.get('auto_only', False)}).encode())
    if payload.get('review_group_token') or payload.get('review_group_record_ids'):
        token.update(digest({'review_group_token': payload.get('review_group_token'),
                             'review_group_record_ids': payload.get('review_group_record_ids')}).encode())
    # Hash exactly the same evidence, but do not hydrate both historical and
    # current JSON, advice, automation and ORM objects for every selected pair.
    query = selected_pairs(db, run.id, payload['scope']) if pair_ids is None else db.query(WorkflowPair).filter(
        WorkflowPair.run_id == run.id, WorkflowPair.id.in_(pair_ids)).order_by(WorkflowPair.id)
    query = query.with_entities(
        WorkflowPair.id, WorkflowPair.revision, WorkflowPair.decision,
        effective_column().label('evaluation'))
    for row in query.yield_per(1000):
        evaluation = row.evaluation
        if not evaluation:
            # Retain the legacy fallback for an empty assessment object.
            evaluation = db.query(WorkflowPair.baseline).filter(WorkflowPair.id == row.id).scalar()
        token.update(digest({'id': row.id, 'revision': row.revision, 'decision': row.decision,
                             'evaluation': evaluation}).encode())
    for left, right, identifier in db.query(ResolutionNegativeEdge.left_record_id, ResolutionNegativeEdge.right_record_id,
        ResolutionNegativeEdge.id).filter_by(run_id=run.id, active=True).order_by(ResolutionNegativeEdge.id).yield_per(1000):
        token.update(digest([left, right, identifier]).encode())
    return token.hexdigest()


def validate_bulk_preview(db, run, payload):
    if payload.get('preview_token') and payload['preview_token'] != bulk_preview_token(db, run, payload):
        from masterone.mdm.workflow_service import conflict
        conflict('BULK_PREVIEW_CHANGED')
