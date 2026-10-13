"""Explicit revision-fenced corrections; remove only traced steward negative edges."""
from fastapi import HTTPException

from masterone.mdm.models import ResolutionAuditEvent, ResolutionNegativeEdge, ResolutionRecord
from masterone.mdm.workflow_models import WorkflowConflict, WorkflowPair
from masterone.mdm.workflow_review import apply_decision, pair_read
from masterone.mdm.workflow_service import (
    active_operation,
    audit,
    check_revision,
    conflict,
    enabled_run,
    new_operation,
    replay,
)


def release_originating_edges(db, run, pair):
    events = db.query(ResolutionAuditEvent).filter(ResolutionAuditEvent.run_id == run.id,
        ResolutionAuditEvent.action == 'WORKFLOW_PAIR_NON_MATCH',
        ResolutionAuditEvent.details['pair_id'].as_string() == pair.id).all()
    traced = [event for event in events if 'negative_edge_id' in event.details]
    if not traced:
        conflict('DENY_SOURCE_UNKNOWN')
    endpoints = tuple(sorted((pair.baseline['left_id'], pair.baseline['right_id'])))
    released = []
    for event in traced:
        identifier = event.details.get('negative_edge_id')
        if not identifier:
            continue
        edge = db.get(ResolutionNegativeEdge, identifier)
        if (edge and edge.active and edge.run_id == run.id and edge.reason == 'STEWARD_REJECT'
                and edge.created_by == event.actor_user_id and
                tuple(sorted((edge.left_record_id, edge.right_record_id))) == endpoints):
            edge.active = False
            released.append(edge.id)
    db.flush()
    return released


def refresh_negative_assessment(db, run, pair):
    """Re-evaluate cached cannot-link evidence after its source has been corrected."""
    from masterone.mdm.workflow_assessment_reads import effective_result
    result = effective_result(pair)
    if result.get('reason') != 'STEWARD_NEGATIVE_DECISION' and 'STEWARD_NEGATIVE_DECISION' not in result.get('constraint_codes', []):
        return
    from masterone.mdm.pipeline.decision_semantics import load_cannot_links
    from masterone.mdm.pipeline.golden_v2 import record_payload
    from masterone.mdm.pipeline.matching import value_frequencies
    from masterone.mdm.pipeline.pair_assessment import assess_pair
    records = [record_payload(record) for record in db.query(ResolutionRecord).filter_by(run_id=run.id)]
    by_id = {record['id']: record for record in records}
    evaluated = assess_pair(by_id[pair.baseline['left_id']], by_id[pair.baseline['right_id']],
                            value_frequencies(records), load_cannot_links(db, run.id))
    pair.assessment = {**evaluated, 'id': pair.id, 'soft_evaluation': {'status': 'NOT_CONFIGURED'}}
    pair.advice = None


def amend_decision(db, run_id, pair_id, request, actor):
    if not request.reason.strip():
        raise HTTPException(422, {'code': 'AMEND_REASON_REQUIRED'})
    payload = {**request.model_dump(), 'pair_id': pair_id}
    run = enabled_run(db, run_id, actor)
    prior = replay(db, run_id, 'AMEND_PAIR', request.request_id, payload)
    if prior:
        return prior.result
    check_revision(run, payload)
    if run.workflow_state != 'DECIDE_REVIEW' or active_operation(db, run_id):
        conflict('DECISIONS_LOCKED')
    pair = db.query(WorkflowPair).filter_by(run_id=run.id, id=pair_id).one_or_none()
    if pair is None:
        raise HTTPException(404, 'Pair not found')
    if pair.revision != request.expected_pair_revision:
        conflict('STALE_PAIR_REVISION')
    before = pair.decision
    if before == 'NON_MATCH' and request.decision == 'deny':
        # A no-op must not manufacture a new null provenance event for a legacy edge.
        op = new_operation(db, run, 'AMEND_PAIR', request.request_id, payload, actor)
        op.status, op.stage = 'COMPLETED', 'completed'
        op.result = {**pair_read(pair), 'workflow_revision': run.workflow_revision, 'decision_revision': run.decision_revision}
        audit(db, run, actor, 'WORKFLOW_AMEND_PAIR', {'pair_id': pair.id, 'before': before,
            'after': before, 'reason': request.reason.strip(), 'released_negative_edge_ids': [],
            'pair_revision': pair.revision})
        db.commit()
        return op.result
    released = release_originating_edges(db, run, pair) if before == 'NON_MATCH' and request.decision != 'deny' else []
    if released:
        refresh_negative_assessment(db, run, pair)
    pair.decision = 'UNDECIDED'
    apply_decision(db, run, pair, request.decision, actor)
    # Recheck affected old conflicts at the next publication; unrelated regions remain blocked.
    ids = {pair.baseline['left_id'], pair.baseline['right_id']}
    for row in db.query(WorkflowConflict).filter_by(run_id=run.id, resolved=False):
        if row.pair_id == pair.id or ids.intersection(row.payload.get('record_ids', [])):
            row.resolved = True
    run.workflow_revision += 1
    run.decision_revision += 1
    op = new_operation(db, run, 'AMEND_PAIR', request.request_id, payload, actor)
    op.status, op.stage = 'COMPLETED', 'completed'
    op.result = {**pair_read(pair), 'workflow_revision': run.workflow_revision, 'decision_revision': run.decision_revision}
    audit(db, run, actor, 'WORKFLOW_AMEND_PAIR', {'pair_id': pair.id, 'before': before,
        'after': pair.decision, 'reason': request.reason.strip(), 'released_negative_edge_ids': released,
        'pair_revision': pair.revision})
    db.commit()
    return op.result
