from fastapi import HTTPException
from sqlalchemy import tuple_

from masterone.mdm.models import ResolutionNegativeEdge
from masterone.mdm.pipeline.decision_semantics import ReviewDecision, adapt_evaluation
from masterone.mdm.workflow_assessment_reads import effective_column, effective_result
from masterone.mdm.workflow_models import WorkflowConflict, WorkflowPair
from masterone.mdm.workflow_service import (
    audit,
    check_revision,
    conflict,
    enabled_run,
    new_operation,
    replay,
)

DECISIONS = {'merge': 'ACCEPTED', 'no_merge': 'REJECTED', 'deny': 'NON_MATCH', 'defer': 'DEFERRED'}


def paginate(query, column, limit, cursor, serialize):
    total = query.count()
    if cursor:
        query = query.filter(column > cursor)
    rows = query.order_by(column).limit(limit + 1).all()
    page = rows[:limit]
    return {'items': [serialize(row) for row in page], 'total': total,
            'next_cursor': getattr(page[-1], column.key) if len(rows) > limit else None}


def eligibility(pair):
    result = effective_result(pair)
    if result.get('coordinator_version') == 'r1_v1':
        permission = dict(result['eligibility'])
        if pair.decision in ('REJECTED', 'NON_MATCH'):
            permission.update(manual_allowed=False, auto_allowed=False,
                              blocked_reasons=['STEWARD_NEGATIVE_DECISION' if pair.decision == 'NON_MATCH' else 'REVIEW_VETO'])
        if result.get('primary_group') == 'D':
            permission['reason_required'] = True
        return permission
    permission = adapt_evaluation(result, review=ReviewDecision(pair.decision))[2]
    reasons = list(permission.reasons)
    if not permission.manual_allowed and not reasons:
        reasons = ['STEWARD_NEGATIVE_DECISION' if pair.decision == 'NON_MATCH' else
                   'REVIEW_VETO' if pair.decision == 'REJECTED' else
                   result.get('reason') or 'PAIR_MERGE_BLOCKED']
    return {**({'reason_required': True} if result.get('primary_group') == 'D' else {}),
            'manual_allowed': permission.manual_allowed,
            'auto_allowed': permission.auto_allowed and result.get('triage_status') != 'REVIEW',
            'blocked_reasons': reasons}


def pair_read(pair):
    from masterone.mdm.review_taxonomy import taxonomy, mvp_taxonomy
    return {**taxonomy(effective_result(pair)), **mvp_taxonomy(effective_result(pair)), 'pair_id': pair.id, 'baseline': effective_result(pair), 'decision': pair.decision,
            'review_reason': pair.review_reason, 'automation': pair.automation,
            'original_baseline': pair.baseline, 'assessment_id': pair.assessment_id,
            'advice': pair.advice, 'revision': pair.revision, 'eligibility': eligibility(pair), 'score_unit': 'p119_rule_v1'}


def scoped_pairs(db, run_id, filters=None):
    query = db.query(WorkflowPair).filter_by(run_id=run_id)
    filters = filters or {}
    projected = None
    if filters.get('visibility') == 'review' or filters.get('taxonomy') in ('actions_v1', 'mvp_v1'):
        from sqlalchemy import String, Boolean, column, func, true
        effective = effective_column()
        if db.bind.dialect.name == 'postgresql':
            projected = func.json_to_record(effective).table_valued(
                column('primary_group', String), column('subgroup', String),
                column('decision', String), column('triage_status', String),
                column('reason',String),column('triage_reason',String),column('agent_disagreement',Boolean)
            ).render_derived(with_types=True).lateral('review_visibility')
            query = query.join(projected, true())
        group = projected.c.primary_group if projected is not None else effective['primary_group'].as_string()
        decision = projected.c.decision if projected is not None else effective['decision'].as_string()
        if filters.get('visibility') == 'review':
            query = query.filter(func.coalesce(group, '') != 'BLOCKED',
                                 func.coalesce(decision, '').notin_(('BLOCKED', 'NOT_MERGEABLE', 'NON_MATCH')))
    review_group,review_subgroup=None,None
    if filters.get('taxonomy') in ('actions_v1', 'mvp_v1'):
        from masterone.mdm.review_taxonomy import taxonomy_columns
        evaluation=effective_column()
        def field(key):
            return projected.c[key] if projected is not None else evaluation[key].as_string()
        disagreement=projected.c.agent_disagreement if projected is not None else evaluation['agent_disagreement'].as_boolean()
        review_group,review_subgroup=taxonomy_columns(field('primary_group'),field('subgroup'),field('decision'),
            field('triage_status'),func.coalesce(field('triage_reason'),field('reason')),disagreement)
    if filters.get('taxonomy') == 'mvp_v1':
        from masterone.mdm.review_taxonomy import mvp_columns
        review_group, review_subgroup = mvp_columns(review_group, review_subgroup)
        query = query.filter(review_group.notin_(('C', 'BLOCKED')))
    if filters.get('group'):
        group = projected.c.primary_group if projected is not None else effective_column()['primary_group'].as_string()
        if review_group is not None:group=review_group
        query = query.filter(group.is_(None) if filters['group'] == 'UNCLASSIFIED' else group == filters['group'])
    if filters.get('subgroup'):
        subgroup = projected.c.subgroup if projected is not None else effective_column()['subgroup'].as_string()
        if review_subgroup is not None:subgroup=review_subgroup
        query = query.filter(subgroup == filters['subgroup'])
    if filters.get('resolution'):
        from masterone.mdm.workflow_review_rules import pending_predicate
        pending = pending_predicate(baseline=projected.c.decision, triage=projected.c.triage_status) if projected is not None else pending_predicate()
        baseline = projected.c.decision if projected is not None else effective_column()['decision'].as_string()
        query = query.filter(pending if filters['resolution'] == 'REQUIRED' else
                             baseline == 'INSUFFICIENT_EVIDENCE' if filters['resolution'] == 'INSUFFICIENT' else
                             WorkflowPair.decision.in_(('UNDECIDED', 'DEFERRED')) if filters['resolution'] == 'PENDING'
                             else WorkflowPair.decision == filters['resolution'])
    return query


def negative_links(db, run_id, pairs):
    endpoints = {tuple(sorted((p.baseline['left_id'], p.baseline['right_id']))) for p in pairs}
    if not endpoints:
        return set()
    orientations = endpoints | {(b, a) for a, b in endpoints}
    rows = db.query(ResolutionNegativeEdge.left_record_id, ResolutionNegativeEdge.right_record_id).filter(
        ResolutionNegativeEdge.run_id == run_id, ResolutionNegativeEdge.active.is_(True),
        tuple_(ResolutionNegativeEdge.left_record_id, ResolutionNegativeEdge.right_record_id).in_(orientations))
    from masterone.mdm.workflow_structure import structure_links
    return {tuple(sorted(row)) for row in rows} | (structure_links(db, run_id) & endpoints)


def apply_decision(db, run, pair, decision, actor, *, links=None, resolve_conflicts=True, reason=None):
    key = tuple(sorted((pair.baseline['left_id'], pair.baseline['right_id'])))
    if links is None:
        links = negative_links(db, run.id, [pair])
    if decision == 'merge' and (not eligibility(pair)['manual_allowed'] or key in links):
        conflict('PAIR_MERGE_BLOCKED')
    if decision == 'merge' and effective_result(pair).get('primary_group') == 'D' and not (reason or '').strip():
        conflict('SOFT_ACCEPT_REASON_REQUIRED')
    edge_id = None
    if decision == 'deny' and key not in links:
        from uuid import uuid4
        edge_id = str(uuid4())
        db.add(ResolutionNegativeEdge(id=edge_id, run_id=run.id, left_record_id=key[0], right_record_id=key[1],
            created_by=actor, reason='STEWARD_REJECT', active=True))
        links.add(key)
    pair.decision = DECISIONS[decision]
    pair.automation = None
    pair.review_reason = (reason or '').strip() or None
    pair.revision += 1
    # Preserve historical no_merge semantics; explicit Deny is a durable cannot-link.
    if resolve_conflicts and decision in ('no_merge', 'deny'):
        for blocker in db.query(WorkflowConflict).filter_by(run_id=run.id, pair_id=pair.id, resolved=False):
            blocker.resolved = True
    audit(db, run, actor, 'WORKFLOW_PAIR_' + pair.decision,
          {'pair_id': pair.id, 'pair_revision': pair.revision, 'negative_edge_id': edge_id,
           'reason': pair.review_reason})


def decide_pair(db, run_id, pair_id, request, actor):
    payload = request.model_dump(exclude={'reason'} if request.reason is None else set())
    payload['pair_id'] = pair_id
    run = enabled_run(db, run_id, actor)
    prior = replay(db, run_id, 'PAIR_DECISION', request.request_id, payload)
    if prior:
        return prior.result
    check_revision(run, payload)
    from masterone.mdm.workflow_service import review_blocking_operation
    if run.workflow_state not in ('DECIDE_REVIEW','GOLDEN_REVIEW') or review_blocking_operation(db, run_id):
        conflict('DECISIONS_LOCKED')
    pair = db.query(WorkflowPair).filter_by(run_id=run_id, id=pair_id).one_or_none()
    if pair is None:
        raise HTTPException(404, 'Pair not found')
    if pair.revision != request.expected_pair_revision:
        conflict('STALE_PAIR_REVISION')
    if pair.decision == 'NON_MATCH':
        conflict('AMENDMENT_REQUIRED')
    from masterone.mdm.steward_feedback import capture_review_snapshot, persist_feedback
    snapshot = capture_review_snapshot(db, run, pair)
    apply_decision(db, run, pair, request.decision, actor, reason=request.reason)
    run.workflow_state = 'DECIDE_REVIEW'
    run.workflow_revision += 1
    run.decision_revision += 1
    op = new_operation(db, run, 'PAIR_DECISION', request.request_id, payload, actor)
    persist_feedback(db, run, pair, op, actor, snapshot, 'HUMAN_DIRECT')
    op.status, op.stage = 'COMPLETED', 'completed'
    op.result = {**pair_read(pair), 'workflow_revision': run.workflow_revision, 'decision_revision': run.decision_revision}
    if request.decision == 'merge':
        from masterone.mdm.workflow_cluster_queue import schedule_cluster_refresh
        schedule_cluster_refresh(db, run, op)
        op.result = {**op.result, 'workflow_revision': run.workflow_revision}
    db.commit()
    return op.result
