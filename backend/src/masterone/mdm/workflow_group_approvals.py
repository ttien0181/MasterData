"""Exact-member, evidence-bound exceptions for missing direct bridge evidence."""
from contextlib import closing

from masterone.mdm.generation_staging import digest


def approval_fingerprint(snapshot, record_ids):
    ids = set(record_ids)
    return digest({'records': sorted((r for r in snapshot['records'] if r['id'] in ids), key=lambda r: r['id']),
        'pairs': sorted(({'pair': p, 'decision': snapshot['overlays'].get(p['id'], 'UNDECIDED'),
                         'reason': snapshot.get('review_reasons', {}).get(p['id'])}
                        for p in snapshot['pairs'] if {p['left_id'], p['right_id']} <= ids),
                       key=lambda p: p['pair']['id']),
        'negative_pairs': sorted(p for p in snapshot['negative_pairs'] if set(p) <= ids),
        'assessment_id': snapshot.get('assessment_id'), 'profile': snapshot.get('profile'),
        'execution_identity': snapshot.get('execution_identity')})


def validate_group(snapshot, record_ids):
    from masterone.mdm.pipeline.cluster_guards import check_component
    from masterone.mdm.pipeline.cluster_policy import GuardBudget, load_policy
    from masterone.mdm.pipeline.decision_store import DecisionStore
    from masterone.mdm.pipeline.matching import value_frequencies

    ids = set(record_ids)
    by_id = {r['id']: r for r in snapshot['records']}
    if len(ids) != len(record_ids) or not 2 <= len(ids) <= 20 or not ids <= by_id.keys():
        return 'GROUP_MEMBERS_INVALID'
    adjacency = {rid: set() for rid in ids}
    internal = [p for p in snapshot['pairs'] if {p['left_id'], p['right_id']} <= ids]
    for pair in internal:
        if snapshot['overlays'].get(pair['id']) == 'ACCEPTED':
            adjacency[pair['left_id']].add(pair['right_id'])
            adjacency[pair['right_id']].add(pair['left_id'])
    visited, pending = set(), [min(ids)]
    while pending:
        rid = pending.pop()
        if rid not in visited:
            visited.add(rid)
            pending.extend(adjacency[rid] - visited)
    if visited != ids:
        return 'GROUP_ACCEPTED_LINKS_REQUIRED'
    with closing(DecisionStore()) as store:
        for pair in internal:
            store.add({**pair, 'user_decision': snapshot['overlays'].get(pair['id'], 'UNDECIDED'),
                       'review_reason': snapshot.get('review_reasons', {}).get(pair['id'])})
        store.seal()
        links = frozenset(tuple(sorted(p)) for p in snapshot['negative_pairs'])
        vetoes = frozenset(tuple(sorted((p['left_id'], p['right_id']))) for p in internal
                          if snapshot['overlays'].get(p['id']) in ('REJECTED', 'NON_MATCH'))
        return check_component(ids, by_id, store, load_policy(), GuardBudget(),
                               value_frequencies(snapshot['records']), links, vetoes,
                               allow_missing_evidence=True)


def approve_group(db, run_id, request, actor):
    from masterone.mdm.engine_adapter import engine_context
    from masterone.mdm.workflow_finalize import decision_snapshot
    from masterone.mdm.workflow_service import (
        active_operation, audit, check_revision, conflict, enabled_run, new_operation, operation_read, replay,
    )

    payload = request.model_dump()
    if not request.reason.strip():
        conflict('GROUP_APPROVAL_REASON_REQUIRED')
    run = enabled_run(db, run_id, actor)
    prior = replay(db, run_id, 'GROUP_APPROVAL', request.request_id, payload)
    if prior:
        return operation_read(prior)
    check_revision(run, payload)
    if run.workflow_state != 'DECIDE_REVIEW' or active_operation(db, run_id):
        conflict('DECISIONS_LOCKED')
    snapshot = decision_snapshot(db, run, allow_partial=True)
    ids = sorted(request.record_ids)
    with engine_context(snapshot['profile']):
        reason = validate_group(snapshot, ids)
    if reason:
        conflict('GROUP_APPROVAL_BLOCKED', reason=reason)
    op = new_operation(db, run, 'GROUP_APPROVAL', request.request_id, payload, actor)
    op.status, op.stage = 'COMPLETED', 'completed'
    op.result = {'record_ids': ids, 'reason': request.reason.strip(),
                 'fingerprint': approval_fingerprint(snapshot, ids), 'operation_id': op.id}
    # Approval affects the effective grouping decisions and invalidates a preview.
    run.decision_revision += 1
    run.workflow_revision += 1
    audit(db, run, actor, 'WORKFLOW_GROUP_APPROVAL', op.result)
    db.commit()
    return operation_read(op)
