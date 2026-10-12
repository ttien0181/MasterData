from uuid import uuid4

import pytest
from masterone.mdm.models import ResolutionRun, ResolutionTask


def request(revision=0, **extra):
    return {'request_id': str(uuid4()), 'expected_workflow_revision': revision, **extra}


def test_workflow_creation_legacy_and_get_are_read_only(client, workflow_run):
    url = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    for _ in range(3):
        response = client.get(url)
        assert response.status_code == 200
        assert response.json()['workflow_version'] == 2
        assert response.json()['workflow_state'] == 'DATA_PREPARING'
    with client.session_factory() as db:
        assert db.query(ResolutionTask).filter_by(run_id=workflow_run).count() == 1
        legacy = ResolutionRun(owner_user_id=db.get(ResolutionRun, workflow_run).owner_user_id)
        db.add(legacy)
        db.commit()
        legacy_id = legacy.id
    legacy = client.get(f'/api/v1/mdm/runs/{legacy_id}/workflow').json()
    assert legacy['workflow_state'] == 'LEGACY_READ_ONLY'
    assert legacy['allowed_actions'] == []


def test_workflow_replay_stale_and_legacy_guards(client, workflow_run):
    from masterone.mdm.workflow_models import WorkflowOperation
    from masterone.mdm.workflow_service import begin_operation
    with client.session_factory() as db:
        run = db.get(ResolutionRun, workflow_run)
        task = db.query(ResolutionTask).filter_by(run_id=workflow_run).one()
        task.status = 'COMPLETED'
        db.query(WorkflowOperation).filter_by(run_id=workflow_run).one().status = 'COMPLETED'
        run.workflow_state = 'DATA_READY'
        db.commit()
        body = request()
        first = begin_operation(db, workflow_run, 'DECIDE', body['request_id'], body, 0, run.owner_user_id)
        replay = begin_operation(db, workflow_run, 'DECIDE', body['request_id'], body, 0, run.owner_user_id)
        assert replay == first
    url = f'/api/v1/mdm/runs/{workflow_run}'
    assert client.post(url + '/start').status_code == 409
    assert client.post(url + '/workflow/start-decide', json=request()).status_code == 409
    assert client.post(url + '/workflow/start-decide', json={**body, 'expected_workflow_revision': 99}).status_code == 409
    assert client.get('/api/v1/mdm/runs/missing/workflow').status_code == 404
    assert client.get(url + '/workflow/operations/missing').status_code == 404


def test_workflow_state_machine():
    from masterone.mdm.workflow_contracts import transition
    assert transition('DATA_READY', 'DECIDE') == 'DECIDE_RUNNING'
    assert transition('DECIDE_REVIEW', 'CLUSTER') == 'CLUSTER_QUEUED'
    with pytest.raises(ValueError):
        transition('DATA_READY', 'CLUSTER')


def test_workflow_old_review_cannot_bypass(client, workflow_run):
    url = f'/api/v1/mdm/runs/{workflow_run}'
    response = client.post(url + '/review-pairs/missing/decision', json={'decision': 'merge', 'revision': 1})
    assert response.status_code == 409
    assert response.json()['detail']['code'] == 'USE_WORKFLOW_API'
    assert client.put(url + '/proposals/missing/golden-record', json={'fields': {'name': 'x'}}).status_code == 409
