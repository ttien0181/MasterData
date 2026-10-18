"""Persisted legacy processing jobs retain their durable continuation semantics."""
import pytest
from masterone.config import get_settings
from masterone.mdm.workflow_models import WorkflowOperation, WorkflowPair

from tests.test_mdm.test_workflow_contract import request
from tests.test_mdm.test_workflow_stages import read, run_one


@pytest.fixture(autouse=True)
def legacy_processing_payload(monkeypatch):
    """Exercise operations persisted before the opt-in MVP contract was added."""
    from masterone.mdm import workflow_processing
    from masterone.mdm.workflow_service import begin_operation
    def legacy(db, run_id, body, actor):
        payload = {**body.model_dump(), 'processing': True, 'demo_fast_mode': get_settings().demo_fast_mode}
        return begin_operation(db, run_id, 'DECIDE', body.request_id, payload,
                               body.expected_workflow_revision, actor)
    monkeypatch.setattr(workflow_processing, 'begin_processing', legacy)


def test_processing_chains_to_preview_and_replays_without_decisions(client, workflow_run, monkeypatch):
    monkeypatch.setattr(get_settings(), 'llm_enabled', False)
    monkeypatch.setattr(get_settings(), 'pair_assessment_auto_enabled', True)
    run_one(client)
    url = f'/api/v1/mdm/runs/{workflow_run}/workflow/process'
    body = request(read(client, workflow_run)['workflow_revision'])
    response = client.post(url, json=body)
    assert response.status_code == 202, response.text
    assert client.post(url, json=body).json() == response.json()
    run_one(client)  # DECIDE publishes and queues the preview atomically.
    state = read(client, workflow_run)
    assert state['active_operation']['operation_type'] == 'CLUSTER_PREVIEW'
    with client.session_factory() as db:
        parent = db.get(WorkflowOperation, response.json()['operation_id'])
        assert parent.result['processing']['next_operation_id'] == state['active_operation']['operation_id']
        assert parent.progress['processed'] == parent.progress['total']
    run_one(client)
    state = read(client, workflow_run)
    assert state['active_operation'] is None
    assert state['workflow_state'] == 'DECIDE_REVIEW'
    assert state['cluster_revision'] == 0
    with client.session_factory() as db:
        assert all(p.decision == 'UNDECIDED' for p in db.query(WorkflowPair).filter_by(run_id=workflow_run))
        assert db.query(WorkflowOperation).filter_by(run_id=workflow_run, operation_type='CLUSTER_PREVIEW').count() == 1


def test_processing_explicit_scope_runs_splink_in_initial_assessment(client, workflow_run, monkeypatch):
    monkeypatch.setattr(get_settings(), 'pair_assessment_auto_enabled', False)
    monkeypatch.setattr(get_settings(), 'llm_enabled', False)
    monkeypatch.setattr(get_settings(), 'pair_review_model_path', 'data/models/pair-review-v2/manifest.json')
    run_one(client)
    response = client.post(f'/api/v1/mdm/runs/{workflow_run}/workflow/process', json=request(
        read(client, workflow_run)['workflow_revision'], model_scope='synthetic-benchmark'))
    assert response.status_code == 202, response.text
    run_one(client)
    with client.session_factory() as db:
        operation = db.get(WorkflowOperation, response.json()['operation_id'])
        assert operation.result['model']['status'] == 'EVALUATED'
        pairs = db.query(WorkflowPair).filter_by(run_id=workflow_run).all()
        assert pairs and all(p.assessment['soft_evaluation']['status'] == 'EVALUATED' for p in pairs)


def test_processing_advice_job_with_no_eligible_pairs_makes_no_api_calls(client, workflow_run, monkeypatch):
    from masterone.mdm.models import LLMRequest
    monkeypatch.setattr(get_settings(), 'llm_enabled', True)
    monkeypatch.setattr(get_settings(), 'llm_pair_assessment_enabled', True)
    monkeypatch.setattr(get_settings(), 'pair_assessment_auto_enabled', True)
    run_one(client)
    response = client.post(f'/api/v1/mdm/runs/{workflow_run}/workflow/process', json=request(
        read(client, workflow_run)['workflow_revision'], advice_limit=3))
    assert response.status_code == 202, response.text
    run_one(client)
    assert read(client, workflow_run)['active_operation']['operation_type'] == 'PAIR_ADVICE'
    run_one(client)
    assert read(client, workflow_run)['active_operation']['operation_type'] == 'CLUSTER_PREVIEW'
    with client.session_factory() as db:
        assert db.query(LLMRequest).count() == 0
    run_one(client)


def test_processing_rejects_stale_revision_and_non_owner(client, workflow_run):
    run_one(client)
    url = f'/api/v1/mdm/runs/{workflow_run}/workflow/process'
    response = client.post(url, json=request(999))
    assert response.status_code == 409
    assert client.post('/api/v1/mdm/runs/missing/workflow/process', json=request()).status_code == 404


def test_processing_stops_if_actor_permission_is_revoked(client, workflow_run, monkeypatch):
    from masterone.auth.models import User
    monkeypatch.setattr(get_settings(), 'llm_enabled', False)
    run_one(client)
    response = client.post(f'/api/v1/mdm/runs/{workflow_run}/workflow/process', json=request(
        read(client, workflow_run)['workflow_revision']))
    assert response.status_code == 202, response.text
    with client.session_factory.begin() as db:
        db.query(User).filter_by(email_normalized='steward@example.com').one().role = 'VIEWER'
    with pytest.raises(ValueError, match='WORKFLOW_AUTHORIZATION_CHANGED'):
        run_one(client)
    with client.session_factory() as db:
        assert db.query(WorkflowOperation).filter_by(run_id=workflow_run, operation_type='CLUSTER_PREVIEW').count() == 0
