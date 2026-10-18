import pytest
from masterone.mdm.models import ResolutionAuditEvent, ResolutionRecord
from masterone.mdm.workflow_models import WorkflowOperation, WorkflowPair

from tests.test_mdm.test_workflow_contract import request
from tests.test_mdm.test_workflow_stages import read, ready_decisions, run_one


def prepare_group(client, run_id):
    ready_decisions(client, run_id)
    with client.session_factory() as db:
        ids = sorted(r.id for r in db.query(ResolutionRecord).filter_by(run_id=run_id)
                     if r.external_id in ('1', '2'))
        pair = next(p for p in db.query(WorkflowPair).filter_by(run_id=run_id)
                    if {p.baseline['left_id'], p.baseline['right_id']} == set(ids))
        pid, revision = pair.id, pair.revision
    root = f'/api/v1/mdm/runs/{run_id}/workflow'
    result = client.post(root + f'/pairs/{pid}/decision', json=request(
        read(client, run_id)['workflow_revision'], expected_pair_revision=revision, decision='merge'))
    assert result.status_code == 200, result.text
    run_one(client)  # The accepted pair now schedules the first full refresh.
    return root, ids


def test_bulk_groups_preserve_per_group_receipts_and_replay(client, workflow_run):
    root, ids = prepare_group(client, workflow_run)
    state = read(client, workflow_run)
    body = request(state['workflow_revision'], expected_decision_revision=state['decision_revision'],
        groups=[{'record_ids': ids, 'reason': 'Checked sources'}] * 2)
    response = client.post(root + '/group-approvals/bulk', json=body)
    assert response.status_code == 200, response.text
    assert client.post(root + '/group-approvals/bulk', json=body).json() == response.json()
    current = read(client, workflow_run)
    assert current['decision_revision'] == state['decision_revision'] + 2
    assert current['workflow_revision'] == state['workflow_revision'] + 3
    with client.session_factory() as db:
        assert db.query(WorkflowOperation).filter_by(run_id=workflow_run, operation_type='GROUP_APPROVAL').count() == 2
        assert db.query(ResolutionAuditEvent).filter_by(run_id=workflow_run, action='WORKFLOW_GROUP_APPROVAL').count() == 2
    changed = {**body, 'groups': [{'record_ids': ids, 'reason': 'Different payload'}]}
    assert client.post(root + '/group-approvals/bulk', json=changed).status_code == 409


@pytest.mark.parametrize('fault', ['invalid-member', 'stale'])
def test_bulk_groups_are_atomic(client, workflow_run, fault):
    root, ids = prepare_group(client, workflow_run)
    state = read(client, workflow_run)
    body = request(state['workflow_revision'], expected_decision_revision=state['decision_revision'],
        groups=[{'record_ids': ids, 'reason': 'Checked'}, {'record_ids': [ids[0], 'missing'], 'reason': 'Checked'}])
    if fault == 'stale':
        body['expected_decision_revision'] += 1
    assert client.post(root + '/group-approvals/bulk', json=body).status_code == 409
    assert read(client, workflow_run)['decision_revision'] == state['decision_revision']
    assert read(client, workflow_run)['workflow_revision'] == state['workflow_revision']
    with client.session_factory() as db:
        assert db.query(WorkflowOperation).filter_by(run_id=workflow_run, operation_type='GROUP_APPROVAL').count() == 0


def test_bulk_group_endpoint_enforces_permissions_and_batch_bound(client, workflow_run):
    from masterone.auth.models import User
    root, ids = prepare_group(client, workflow_run)
    state = read(client, workflow_run)
    body = request(state['workflow_revision'], expected_decision_revision=state['decision_revision'],
        groups=[{'record_ids': ids, 'reason': 'Checked'}] * 51)
    assert client.post(root + '/group-approvals/bulk', json=body).status_code == 422
    body['groups'] = body['groups'][:1]
    with client.session_factory.begin() as db:
        db.query(User).filter_by(email_normalized='steward@example.com').one().role = 'VIEWER'
    assert client.post(root + '/group-approvals/bulk', json=body).status_code == 403


def test_group_context_never_caches_decisions_or_crosses_transactions(client, workflow_run):
    from masterone.mdm.models import ResolutionRun
    from masterone.mdm.workflow_group_approvals import group_snapshot
    _, ids = prepare_group(client, workflow_run)
    with client.session_factory() as db:
        run = db.get(ResolutionRun, workflow_run)
        context = {}
        before = group_snapshot(db, run, ids, _context=context)
        pair = next(p for p in db.query(WorkflowPair).filter_by(run_id=workflow_run)
                    if {p.baseline['left_id'], p.baseline['right_id']} == set(ids))
        pair.decision, pair.review_reason = 'NON_MATCH', 'Changed after first group'
        db.flush()
        after = group_snapshot(db, run, ids, _context=context)
        assert before['overlays'][pair.id] == 'ACCEPTED'
        assert after['overlays'][pair.id] == 'NON_MATCH'
        assert after['review_reasons'][pair.id] == 'Changed after first group'
        db.rollback()
        run = db.get(ResolutionRun, workflow_run)
        with pytest.raises(ValueError, match='GROUP_BATCH_CONTEXT_CHANGED'):
            group_snapshot(db, run, ids, _context=context)
