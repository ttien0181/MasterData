from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from masterone.config import get_settings
from masterone.mdm.workflow_models import WorkflowPair

from tests.test_mdm.test_workflow_contract import request
from tests.test_mdm.test_workflow_stages import read, run_one


def test_fast_processing_opens_real_pairs_without_optional_jobs(client, workflow_run, monkeypatch):
    monkeypatch.setattr(get_settings(), 'demo_fast_mode', True)
    run_one(client)
    root = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    body = request(read(client, workflow_run)['workflow_revision'])
    response = client.post(root + '/process', json=body)
    assert response.status_code == 202
    run_one(client)
    state = read(client, workflow_run)
    assert state['workflow_state'] == 'DECIDE_REVIEW'
    assert state['active_operation'] is None
    assert client.get(root + '/pairs').json()['items']
    with client.session_factory() as db:
        assert all(p.decision == 'UNDECIDED' for p in db.query(WorkflowPair).filter_by(run_id=workflow_run))
    assert client.post(root + '/process', json=body).json()['operation_id'] == response.json()['operation_id']


def test_explicit_skip_is_advisory_only_and_has_no_fabricated_vectors(monkeypatch):
    from masterone.mdm import workflow_jobs
    from masterone.mdm.matching_agents import workflow_agents as agents
    config = {'kind': 'RULE_WITH_EMBEDDING_EVIDENCE', 'encoder_identity': {'bundle_digest': 'enc'},
              'preprocessing': {}}
    run = SimpleNamespace(id='run', owner_user_id='owner')
    binding = SimpleNamespace(profile_id='p', profile_digest='d', id='b', revision=1)
    @contextmanager
    def begin():
        yield None
    monkeypatch.setattr(workflow_jobs, 'job_identity', lambda *a: (run, None, None))
    monkeypatch.setattr(agents, 'config_for_operation', lambda *a: config)
    monkeypatch.setattr(agents, 'verify_operation_binding', lambda *a: binding)
    saved = []
    monkeypatch.setattr(agents, 'seal_agent', lambda *a: saved.append(a[-1]) or 'artifact')
    def forbidden(*a, **k):
        raise AssertionError('encoder must not run')
    monkeypatch.setattr(agents, 'EncoderRuntime', forbidden)
    output = agents.run_agents(SimpleNamespace(begin=begin), 'run', 'op', [], [], {}, None,
                               source_artifact_id='source', skip_advisory=True)
    assert output['status'] == 'SKIPPED'
    assert output['automation_allowed'] is False
    assert saved[0]['pairs'] == [] and saved[0]['coverage']['embedding_pairs'] == 0
    assert saved[0]['error_code'] == 'DEMO_ADVISORY_EMBEDDING_SKIPPED'


def test_demo_does_not_skip_required_hybrid_embedding(monkeypatch):
    from masterone.mdm import workflow_jobs
    from masterone.mdm.matching_agents import workflow_agents as agents
    from masterone.mdm.pipeline import decision_semantics
    class DB:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def query(self, *args):
            return self
        def filter_by(self, **kwargs):
            return []
    class Factory:
        def begin(self):
            return DB()
        def __call__(self):
            return DB()
    monkeypatch.setattr(get_settings(), 'advanced_matching_agents_enabled', True)
    monkeypatch.setattr(workflow_jobs, 'job_identity', lambda *a: (SimpleNamespace(owner_user_id='owner'), None, None))
    monkeypatch.setattr(agents, 'config_for_operation', lambda *a: {
        'kind': 'RULE_HYBRID_DETERMINISTIC', 'resource_budget': {}, 'fallback_mode': 'fail_operation',
        'encoder_identity': {}, 'preprocessing': {}})
    monkeypatch.setattr(agents, 'verify_operation_binding', lambda *a: SimpleNamespace(
        profile_id='p', profile_digest='d', id='b', revision=1))
    monkeypatch.setattr(decision_semantics, 'load_cannot_links', lambda *a: set())
    def required(*args):
        raise ValueError('EMBEDDING_REQUIRED_TEST')
    monkeypatch.setattr(agents, 'EncoderRuntime', required)
    with pytest.raises(ValueError, match='EMBEDDING_REQUIRED_TEST'):
        agents.run_agents(Factory(), 'run', 'op', [], [], {}, None,
                          source_artifact_id='source', skip_advisory=True)
