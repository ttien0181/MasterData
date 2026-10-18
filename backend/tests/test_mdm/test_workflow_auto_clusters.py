"""Accepted reviews durably schedule a full background group refresh."""
import pytest

from masterone.mdm.workflow_models import WorkflowOperation, WorkflowPair
from tests.test_mdm.test_workflow_contract import request
from tests.test_mdm.test_workflow_stages import read, ready_decisions, run_one


def reviewable_pair(client, run_id):
    state = ready_decisions(client, run_id)
    with client.session_factory() as db:
        pair = next(p for p in db.query(WorkflowPair).filter_by(run_id=run_id)
                    if p.baseline['decision'] == 'MERGEABLE')
        return state, pair.id


def assert_refresh(client, run_id):
    state = read(client, run_id)
    assert state['workflow_state'] == 'CLUSTER_QUEUED'
    assert state['active_operation']['operation_type'] == 'CLUSTER'
    with client.session_factory() as db:
        child = db.get(WorkflowOperation, state['active_operation']['operation_id'])
        assert child.payload['queue_mode'] == 'all'
        assert child.payload['expected_decision_revision'] == state['decision_revision']
    return state


def test_single_accept_queues_full_refresh_atomically_and_replay_does_not_duplicate(client, workflow_run):
    state, pid = reviewable_pair(client, workflow_run)
    url = f'/api/v1/mdm/runs/{workflow_run}/workflow/pairs/{pid}/decision'
    body = request(state['workflow_revision'], expected_pair_revision=0, decision='merge')
    response = client.post(url, json=body)
    assert response.status_code == 200, response.text
    queued = assert_refresh(client, workflow_run)
    assert queued['cluster_revision'] == 0  # No grouping work on the HTTP request.
    assert response.json()['workflow_revision'] == queued['workflow_revision']
    assert client.post(url, json=body).json() == response.json()
    with client.session_factory() as db:
        assert db.query(WorkflowOperation).filter_by(run_id=workflow_run, operation_type='CLUSTER').count() == 1
    run_one(client)
    after = read(client, workflow_run)
    assert after['cluster_revision'] == 1
    assert after['active_operation'] is None
    assert client.get(url.rsplit('/pairs/', 1)[0] + '/cluster-queue').json()['pending_count'] == 0


def test_bulk_accept_schedules_once_after_all_decisions_commit(client, workflow_run):
    state, pid = reviewable_pair(client, workflow_run)
    url = f'/api/v1/mdm/runs/{workflow_run}/workflow/bulk-decisions'
    body = request(state['workflow_revision'], expected_decision_revision=state['decision_revision'],
                   decision='merge', scope={'pair_ids': [pid]}, auto_only=False)
    response = client.post(url, json=body)
    assert response.status_code == 202, response.text
    run_one(client)  # Review worker hands the durable queue to the cluster worker.
    assert_refresh(client, workflow_run)
    assert client.post(url, json=body).json()['operation_id'] == response.json()['operation_id']
    run_one(client)
    assert read(client, workflow_run)['cluster_revision'] == 1


@pytest.mark.parametrize('decision', ['no_merge', 'deny', 'defer'])
def test_non_accept_does_not_start_grouping(client, workflow_run, decision):
    state, pid = reviewable_pair(client, workflow_run)
    response = client.post(f'/api/v1/mdm/runs/{workflow_run}/workflow/pairs/{pid}/decision',
        json=request(state['workflow_revision'], expected_pair_revision=0, decision=decision))
    assert response.status_code == 200, response.text
    assert read(client, workflow_run)['active_operation'] is None


def test_queue_failure_rolls_back_the_accept_too(client, workflow_run, monkeypatch):
    state, pid = reviewable_pair(client, workflow_run)
    def fail(*args, **kwargs):
        raise RuntimeError('queue unavailable')
    monkeypatch.setattr('masterone.mdm.workflow_service.enqueue', fail)
    with pytest.raises(RuntimeError, match='queue unavailable'):
        client.post(f'/api/v1/mdm/runs/{workflow_run}/workflow/pairs/{pid}/decision',
            json=request(state['workflow_revision'], expected_pair_revision=0, decision='merge'))
    with client.session_factory() as db:
        assert db.get(WorkflowPair, pid).decision == 'UNDECIDED'
        assert db.query(WorkflowOperation).filter_by(run_id=workflow_run, operation_type='CLUSTER').count() == 0


def test_accepts_during_optional_preview_coalesce_into_one_full_refresh(client, workflow_run):
    state, pid = reviewable_pair(client, workflow_run)
    root = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    preview = client.post(root + '/cluster-preview', json=request(state['workflow_revision'], expected_decision_revision=state['decision_revision'], allow_partial=True))
    assert preview.status_code == 202, preview.text
    for revision in range(2):
        state = read(client, workflow_run)
        result = client.post(root + f'/pairs/{pid}/decision', json=request(
            state['workflow_revision'], expected_pair_revision=revision, decision='merge'))
        assert result.status_code == 200, result.text
    with client.session_factory() as db:
        assert db.query(WorkflowOperation).filter_by(run_id=workflow_run, operation_type='CLUSTER', status='WAITING').count() == 1
    run_one(client)  # Finish the preview without losing or replacing its task ownership.
    assert_refresh(client, workflow_run)
    run_one(client)
    assert read(client, workflow_run)['cluster_revision'] == 1


@pytest.mark.parametrize('termination', ['failed', 'cancel-queued', 'cancel-running'])
def test_preview_termination_does_not_strand_the_accepted_refresh(client, workflow_run, termination):
    from masterone.mdm.job_queue import claim_next, failed, recover, request_cancel
    from masterone.mdm.models import ResolutionTask
    state, pid = reviewable_pair(client, workflow_run)
    root = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    response = client.post(root + '/cluster-preview', json=request(state['workflow_revision'],
        expected_decision_revision=state['decision_revision'], allow_partial=True))
    assert response.status_code == 202, response.text
    state = read(client, workflow_run)
    response = client.post(root + f'/pairs/{pid}/decision', json=request(
        state['workflow_revision'], expected_pair_revision=0, decision='merge'))
    assert response.status_code == 200, response.text
    if termination == 'cancel-queued':
        request_cancel(client.session_factory, workflow_run)
    else:
        claim = claim_next(client.session_factory, 'preview-test', lease_seconds=300)
        if termination == 'failed':
            with client.session_factory.begin() as db:
                db.get(ResolutionTask, claim.task_id).max_attempts = 1
            failed(client.session_factory, claim, 'INJECTED_PREVIEW_FAILURE')
        else:
            request_cancel(client.session_factory, workflow_run)
            recover(client.session_factory)
    assert_refresh(client, workflow_run)
    run_one(client)
    assert read(client, workflow_run)['cluster_revision'] == 1


@pytest.mark.parametrize('bulk', [False, True])
def test_group_approval_queues_one_full_refresh_after_atomic_batch(client, workflow_run, bulk):
    state, pid = reviewable_pair(client, workflow_run)
    root = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    with client.session_factory.begin() as db:
        # A group approval can also confirm already accepted links.
        pair = db.get(WorkflowPair, pid)
        pair.decision = 'ACCEPTED'
        members = [pair.baseline['left_id'], pair.baseline['right_id']]
    body = request(state['workflow_revision'], expected_decision_revision=state['decision_revision'])
    group = {'record_ids': members, 'reason': 'Checked source customer records'}
    response = client.post(root + '/group-approvals' + ('/bulk' if bulk else ''),
        json={**body, **({'groups': [group, group]} if bulk else group)})
    assert response.status_code == 200, response.text
    assert_refresh(client, workflow_run)
    with client.session_factory() as db:
        assert db.query(WorkflowOperation).filter_by(run_id=workflow_run, operation_type='CLUSTER').count() == 1
    run_one(client)
    assert read(client, workflow_run)['cluster_revision'] == 1


def test_worker_rechecks_permissions_before_automatic_publication(client, workflow_run):
    from masterone.auth.models import User
    from masterone.mdm.models import ResolutionRun
    state, pid = reviewable_pair(client, workflow_run)
    root = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    result = client.post(root + f'/pairs/{pid}/decision', json=request(
        state['workflow_revision'], expected_pair_revision=0, decision='merge'))
    assert result.status_code == 200, result.text
    with client.session_factory.begin() as db:
        db.get(User, db.get(ResolutionRun, workflow_run).owner_user_id).role = 'VIEWER'
    with pytest.raises(ValueError, match='WORKFLOW_AUTHORIZATION_CHANGED'):
        run_one(client)
    assert read(client, workflow_run)['cluster_revision'] == 0


def test_next_automatic_full_refresh_retains_existing_manual_customer_choices(client, workflow_run):
    from masterone.mdm.workflow_models import WorkflowCluster, WorkflowGeneration
    state, pid = reviewable_pair(client, workflow_run)
    root = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    result = client.post(root + f'/pairs/{pid}/decision', json=request(
        state['workflow_revision'], expected_pair_revision=0, decision='merge'))
    assert result.status_code == 200, result.text
    run_one(client)
    with client.session_factory.begin() as db:
        generation = db.query(WorkflowGeneration).filter_by(run_id=workflow_run, revision=1).one()
        group = db.query(WorkflowCluster).filter_by(generation_id=generation.id).one()
        group.selections = {'name': {'selection_mode': 'MANUAL_OVERRIDE', 'value': 'Steward customer name'}}
        group.confirmed = True
    state = read(client, workflow_run)
    result = client.post(root + f'/pairs/{pid}/decision', json=request(
        state['workflow_revision'], expected_pair_revision=1, decision='merge'))
    assert result.status_code == 200, result.text
    assert_refresh(client, workflow_run)
    run_one(client)
    with client.session_factory() as db:
        generation = db.query(WorkflowGeneration).filter_by(run_id=workflow_run, revision=2).one()
        group = db.query(WorkflowCluster).filter_by(generation_id=generation.id).one()
        assert group.selections['name']['value'] == 'Steward customer name'
        assert len(set(group.payload['member_record_ids'])) == 2
