import json

import pytest

from masterone.auth.models import User
from masterone.config import get_settings
from masterone.mdm.models import LLMPolicyControl
from masterone.mdm.workflow_models import WorkflowPair
from tests.test_mdm.test_workflow_contract import request
from tests.test_mdm.test_workflow_stages import read, ready_decisions


def setup_stream(client, workflow_run, monkeypatch):
    ready_decisions(client, workflow_run)
    url = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    pair_id = client.get(url + '/pairs').json()['items'][0]['pair_id']
    monkeypatch.setattr(get_settings(), 'llm_enabled', True)
    monkeypatch.setattr(get_settings(), 'llm_explanation_enabled', True)
    with client.session_factory.begin() as db:
        db.merge(LLMPolicyControl(id='global', enabled=True, revision=1))
    return url, pair_id


def events(response):
    return [(block.splitlines()[0][7:], json.loads(block.splitlines()[1][6:]))
            for block in response.text.split('\n\n') if block.strip()]


def test_stream_emits_named_deltas_and_result_with_aggregate_only_input(client, workflow_run, monkeypatch):
    url, pair_id = setup_stream(client, workflow_run, monkeypatch)
    captured = []
    async def generate(payload, settings, **kwargs):
        captured.append(payload)
        yield 'delta', 'Safe summary'
        yield 'result', {'output': {'summary_vi': 'Safe summary', 'attention_fields': [], 'limitations': []},
                         'model_version': 'test', 'provider': 'gemini', 'request_id': 'test-request',
                         'usage': {'input': None, 'output': 3}, 'attempts': 1}
    monkeypatch.setattr('masterone.mdm.workflow_explanation_stream.generate_explanation', generate)
    response = client.post(url + f'/pairs/{pair_id}/explanation/stream',
                           json=request(read(client, workflow_run)['workflow_revision']))
    assert response.status_code == 200
    assert response.headers['content-type'].startswith('text/event-stream')
    parsed = events(response)
    assert parsed[0] == ('delta', {'text': 'Safe summary'})
    assert parsed[-1][0] == 'result' and parsed[-1][1]['status'] == 'READY'
    assert parsed[-1][1]['usage'] == {'dispatch_attempts': 1, 'output': 3}
    assert set(captured[0]) == {'pair_count', 'unique_record_count', 'field_signal_counts',
                                'group', 'subgroup', 'score_is_probability'}
    assert all(value not in json.dumps(captured) for value in ['Nguyen Van An', '0901234567', 'a@example.test'])


@pytest.mark.parametrize('change', ['revision', 'delete'])
def test_pair_changed_or_deleted_during_stream_discards_final_cache(client, workflow_run, monkeypatch, change):
    url, pair_id = setup_stream(client, workflow_run, monkeypatch)
    discarded = []
    async def generate(*args, **kwargs):
        yield 'delta', 'Provisional'
        with client.session_factory.begin() as db:
            pair = db.get(WorkflowPair, pair_id)
            if change == 'delete':
                db.delete(pair)
            else:
                pair.revision += 1
        yield 'result', {'output': {'summary_vi': 'Provisional'}, 'model_version': 'test',
                         'provider': 'gemini', 'request_id': 'test-request', 'usage': {}, 'attempts': 1}
    monkeypatch.setattr('masterone.mdm.workflow_explanation_stream.generate_explanation', generate)
    monkeypatch.setattr('masterone.llm.policy.PolicyEnforcer.discard', lambda self, rid: discarded.append(rid))
    response = client.post(url + f'/pairs/{pair_id}/explanation/stream',
                           json=request(read(client, workflow_run)['workflow_revision']))
    final = events(response)[-1][1]
    assert final['status'] == 'STALE' and final['explanation'] is None
    assert discarded == ['test-request']


def test_stream_preflight_rejects_revision_csrf_and_capability_without_dispatch(client, workflow_run, monkeypatch):
    url, pair_id = setup_stream(client, workflow_run, monkeypatch)
    calls = []
    async def generate(*args, **kwargs):
        calls.append(1)
        yield 'delta', 'unexpected'
    monkeypatch.setattr('masterone.mdm.workflow_explanation_stream.generate_explanation', generate)
    path = url + f'/pairs/{pair_id}/explanation/stream'
    revision = read(client, workflow_run)['workflow_revision']
    assert client.post(path, json=request(revision + 1)).status_code == 409
    assert client.post(path, json=request(revision), headers={'X-CSRF-Token': 'wrong'}).status_code == 403
    with client.session_factory.begin() as db:
        db.query(User).filter_by(email_normalized='steward@example.com').one().role = 'VIEWER'
    assert client.post(path, json=request(revision)).status_code == 403
    assert calls == []
