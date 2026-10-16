import pytest
from masterone.config import Settings, get_settings

from tests.test_mdm.test_workflow_contract import request
from tests.test_mdm.test_workflow_stages import read, run_one


def test_api_only_config_rejects_local_encoder():
    with pytest.raises(ValueError, match='API_ONLY_LOCAL_ENCODER_DENIED'):
        Settings(_env_file=None, ai_api_only=True, embedding_provider='e5_local')


def test_api_only_refuses_local_model_before_enqueuing(client, workflow_run, monkeypatch):
    monkeypatch.setattr(get_settings(), 'ai_api_only', True, raising=False)
    run_one(client)
    state = read(client, workflow_run)
    response = client.post(f'/api/v1/mdm/runs/{workflow_run}/workflow/process', json=request(
        state['workflow_revision'], model_scope='synthetic-benchmark'))
    assert response.status_code == 409
    assert response.json()['detail']['code'] == 'API_ONLY_LOCAL_MODEL_DENIED'
    assert read(client, workflow_run)['workflow_revision'] == state['workflow_revision']


def test_api_only_defends_worker_against_old_local_scope(monkeypatch):
    from masterone.mdm.workflow_assessment import compute_assessment
    monkeypatch.setattr(get_settings(), 'ai_api_only', True, raising=False)
    with pytest.raises(ValueError, match='API_ONLY_LOCAL_MODEL_DENIED'):
        compute_assessment([], [], {}, set(), model_scope='synthetic-benchmark')


