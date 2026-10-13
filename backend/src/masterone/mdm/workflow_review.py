from fastapi import HTTPException
from sqlalchemy import tuple_

from masterone.mdm.models import ResolutionNegativeEdge
from masterone.mdm.pipeline.decision_semantics import ReviewDecision, adapt_evaluation
from masterone.mdm.workflow_assessment_reads import effective_column, effective_result
from masterone.mdm.workflow_models import WorkflowConflict, WorkflowPair
from masterone.mdm.workflow_service import (
    active_operation,
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
    return {'pair_id': pair.id, 'baseline': effective_result(pair), 'decision': pair.decision,
            'review_reason': pair.review_reason,
            'original_baseline': pair.baseline, 'assessment_id': pair.assessment_id,
            'advice': pair.advice, 'revision': pair.revision, 'eligibility': eligibility(pair), 'score_unit': 'p119_rule_v1'}


def scoped_pairs(db, run_id, filters=None):
    query = db.query(WorkflowPair).filter_by(run_id=run_id)
    filters = filters or {}
    if filters.get('group'):
        column = effective_column()['primary_group'].as_string()
        query = query.filter(column.is_(None) if filters['group'] == 'UNCLASSIFIED' else column == filters['group'])
    if filters.get('subgroup'):
        query = query.filter(effective_column()['subgroup'].as_string() == filters['subgroup'])
    if filters.get('resolution'):
        from masterone.mdm.workflow_review_rules import pending_predicate
        query = query.filter(pending_predicate() if filters['resolution'] == 'REQUIRED' else
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
    return {tuple(sorted(row)) for row in rows}


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
    payload = request.model_dump()
    payload['pair_id'] = pair_id
    run = enabled_run(db, run_id, actor)
    prior = replay(db, run_id, 'PAIR_DECISION', request.request_id, payload)
    if prior:
        return prior.result
    check_revision(run, payload)
    if run.workflow_state != 'DECIDE_REVIEW' or active_operation(db, run_id):
        conflict('DECISIONS_LOCKED')
    pair = db.query(WorkflowPair).filter_by(run_id=run_id, id=pair_id).one_or_none()
    if pair is None:
        raise HTTPException(404, 'Pair not found')
    if pair.revision != request.expected_pair_revision:
        conflict('STALE_PAIR_REVISION')
    if pair.decision == 'NON_MATCH':
        conflict('AMENDMENT_REQUIRED')
    apply_decision(db, run, pair, request.decision, actor, reason=request.reason)
    run.workflow_revision += 1
    run.decision_revision += 1
    op = new_operation(db, run, 'PAIR_DECISION', request.request_id, payload, actor)
    op.status, op.stage = 'COMPLETED', 'completed'
    op.result = {**pair_read(pair), 'workflow_revision': run.workflow_revision, 'decision_revision': run.decision_revision}
    db.commit()
    return op.result
