from datetime import UTC, datetime, timedelta

from masterone.mdm.job_queue import claim_next, failed, recover
from masterone.mdm.models import ResolutionJobAttempt, ResolutionTask
from masterone.mdm.workflow_models import WorkflowArtifact, WorkflowOperation

from tests.test_mdm.test_workflow_contract import request
from tests.test_mdm.test_workflow_stages import read, run_one


def test_workflow_worker_retry_recovers_expired_not_live(client, workflow_run):
    claim = claim_next(client.session_factory, 'old-worker', lease_seconds=300)
    recover(client.session_factory)
    with client.session_factory() as db:
        task = db.get(ResolutionTask, claim.task_id)
        assert task.status == 'RUNNING'
        op_id = task.operation_id
    with client.session_factory.begin() as db:
        db.get(ResolutionJobAttempt, claim.attempt_id).lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    recover(client.session_factory)
    run_one(client)
    with client.session_factory() as db:
        assert db.get(WorkflowOperation, op_id).status == 'COMPLETED'
        assert db.query(ResolutionJobAttempt).filter_by(run_id=workflow_run).count() == 2
        assert db.query(WorkflowArtifact).filter_by(run_id=workflow_run, phase='PREPARE').count() == 1
    assert read(client, workflow_run)['workflow_state'] == 'DATA_READY'


def test_workflow_explicit_retry_keeps_operation_and_snapshot(client, workflow_run):
    claim = claim_next(client.session_factory, 'failed-worker', lease_seconds=300)
    with client.session_factory.begin() as db:
        db.get(ResolutionTask, claim.task_id).max_attempts = 1
        op_id = db.get(ResolutionTask, claim.task_id).operation_id
    failed(client.session_factory, claim, 'TEST_SYNTHETIC_FAILURE')
    state = read(client, workflow_run)
    body = request(state['workflow_revision'])
    url = f'/api/v1/mdm/runs/{workflow_run}/workflow/operations/{op_id}/retry'
    response = client.post(url, json=body)
    assert response.status_code == 202, response.text
    assert response.json()['operation_id'] == op_id
    assert client.post(url, json=body).json() == response.json()
    run_one(client)
    assert read(client, workflow_run)['workflow_state'] == 'DATA_READY'


def test_workflow_cancel_queued_can_retry(client, workflow_run):
    from masterone.mdm.job_queue import request_cancel
    with client.session_factory() as db:
        op_id = db.query(ResolutionTask).filter_by(run_id=workflow_run).one().operation_id
    request_cancel(client.session_factory, workflow_run)
    with client.session_factory() as db:
        assert db.get(WorkflowOperation, op_id).status == 'CANCELLED'
    state = read(client, workflow_run)
    assert state['active_operation'] is None
    assert state['retryable_operation']['operation_id'] == op_id
    assert 'retry-operation' in state['allowed_actions']
    url = f'/api/v1/mdm/runs/{workflow_run}/workflow/operations/{op_id}/retry'
    assert client.post(url, json=request(state['workflow_revision'])).status_code == 202
    run_one(client)
    assert read(client, workflow_run)['workflow_state'] == 'DATA_READY'
