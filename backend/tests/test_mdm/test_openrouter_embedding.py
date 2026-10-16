"""API migration contracts use HTTP protocol doubles, never paid API calls."""
import json
from copy import deepcopy

import httpx
import pytest
from masterone.config import get_settings
from masterone.mdm.matching_agents.contracts import EncoderIntegrityError
from masterone.mdm.matching_agents.encoder_accounting import encode_accounted
from masterone.mdm.matching_agents.openrouter_embedding import OpenRouterEmbeddingRuntime, runtime_spec


@pytest.fixture
def api_settings(monkeypatch):
    settings = get_settings()
    for name, value in {'embedding_provider': 'openrouter', 'embedding_openrouter_api_key': 'embedding-test-only',
                        'embedding_dimensions': 3, 'embedding_max_retries': 0}.items():
        monkeypatch.setattr(settings, name, value)
    return settings


def reply(count=2, **changes):
    return {'model': 'openai/text-embedding-3-small',
            'data': [{'index': i, 'embedding': [1., float(i), 0.]} for i in reversed(range(count))],
            'usage': {'prompt_tokens': 8, 'total_tokens': 8, 'cost': .000001}, **changes}


def runtime(handler):
    identity, budget = runtime_spec()
    return OpenRouterEmbeddingRuntime(identity, budget, transport=httpx.MockTransport(handler))


@pytest.mark.parametrize('response_model', ['openai/text-embedding-3-small', 'text-embedding-3-small'])
def test_api_contract_order_and_separate_credentials(api_settings, response_model):
    def handler(request):
        assert request.headers['Authorization'] == 'Bearer embedding-test-only'
        assert request.url.path == '/api/v1/embeddings'
        body = json.loads(request.content)
        assert body['input'] == ['An', 'Binh']
        assert body['dimensions'] == 3
        assert body['provider'] == {'allow_fallbacks': False}
        return httpx.Response(200, json=reply(model=response_model))
    receipts = []
    with runtime(handler) as encoder:
        result = encode_accounted(encoder, ['An', 'Binh'], 'name', receipts.append)
        assert result == [[1., 0., 0.], [1., 1., 0.]]
        assert encoder.usage['name']['input_tokens'] == 8
    response = next(row for row in receipts if row['event'] == 'HTTP_RESPONSE')
    assert response['api_cost_usd'] == .000001
    assert 'An' not in json.dumps(receipts)
    assert 'embedding-test-only' not in json.dumps(receipts)


@pytest.mark.parametrize('change', [
    {'model': 'wrong-model'}, {'data': [{'index': 0, 'embedding': [1., 0., 0.]}] * 2},
    {'data': [{'index': 0, 'embedding': [0., 0., 0.]}, {'index': 1, 'embedding': [1., 0.]}]},
    {'data': [{'index': 0, 'embedding': [True, 0., 0.]}, {'index': 1, 'embedding': [1., 0., 0.]}]},
])
def test_rejects_model_index_and_vector_corruption_after_accounting(api_settings, change):
    receipts = []
    with runtime(lambda _: httpx.Response(200, json=reply(**change))) as encoder:
        with pytest.raises(EncoderIntegrityError):
            encode_accounted(encoder, ['a', 'b'], 'name', receipts.append)
    assert any(row.get('input_tokens') == 8 for row in receipts)
    assert receipts[-1]['event'] == 'FAILED'


def test_retry_keeps_failed_response_tokens_and_cost(api_settings, monkeypatch):
    monkeypatch.setattr(api_settings, 'embedding_max_retries', 1)
    monkeypatch.setattr('masterone.mdm.matching_agents.openrouter_embedding.time.sleep', lambda _: None)
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(503, json={'error': {'message': 'sensitive body'},
            'usage': {'prompt_tokens': 3, 'cost': .000002}}) if len(calls) == 1 else httpx.Response(200, json=reply(1))
    receipts = []
    with runtime(handler) as encoder:
        encode_accounted(encoder, ['a'], 'name', receipts.append)
        assert encoder.batch_attempts == 2 and encoder.retry_count == 1
        assert encoder.usage['name']['input_tokens'] == 11
        assert encoder.usage['name']['reported_cost_usd'] == pytest.approx(.000003)
    assert len([r for r in receipts if r['event'] == 'HTTP_INTENT']) == 2
    assert 'sensitive body' not in json.dumps(receipts)


def test_restart_budget_counts_unknown_timeout_before_sending_again(api_settings, monkeypatch):
    monkeypatch.setattr(api_settings, 'embedding_max_requests_per_run', 1)
    def timeout(_):
        raise httpx.ReadTimeout('do not expose request details')
    receipts = []
    with runtime(timeout) as encoder:
        with pytest.raises(ValueError, match='TRANSPORT_ERROR'):
            encode_accounted(encoder, ['a'], 'name', receipts.append)
    assert next(r for r in receipts if r['event'] == 'HTTP_FAILED')['input_tokens'] is None
    with runtime(lambda _: pytest.fail('budget exhaustion must not dispatch')) as encoder:
        encoder.restore_budget(receipts)
        with pytest.raises(ValueError, match='BUDGET_EXCEEDED'):
            encode_accounted(encoder, ['a'], 'name', receipts.append)


@pytest.mark.parametrize('setting,value,code,text', [
    ('embedding_max_tokens_per_run', 1, 'BUDGET_EXCEEDED', 'hello'),
    ('embedding_budget_usd', .000000001, 'BUDGET_EXCEEDED', 'hello'),
    ('embedding_budget_usd', .20, 'INPUT_LIMIT_EXCEEDED', 'a' * 8193),
])
def test_explicit_limits_never_truncate_or_dispatch(api_settings, monkeypatch, setting, value, code, text):
    monkeypatch.setattr(api_settings, setting, value)
    with runtime(lambda _: pytest.fail('limit must prevent HTTP dispatch')) as encoder:
        with pytest.raises(ValueError, match=code):
            encoder.encode([text], 'name')


def test_missing_embedding_key_never_uses_gemini_or_chat_key(api_settings, monkeypatch):
    monkeypatch.setattr(api_settings, 'embedding_openrouter_api_key', '')
    monkeypatch.setattr(api_settings, 'gemini_api_key', 'gemini-test-only')
    monkeypatch.setattr(api_settings, 'openrouter_api_key', 'chat-test-only')
    with pytest.raises(ValueError, match='API_KEY_MISSING'):
        with runtime(lambda _: pytest.fail('no embedding key')):
            pass


def test_new_profile_identity_and_cache_deduplication(api_settings):
    from masterone.mdm.matching_agents.embedding_agent import EmbeddingAgent
    from masterone.mdm.matching_agents.integration_profile import integration_config
    from masterone.mdm.matching_foundation_service import baseline_config
    config = integration_config(baseline_config())
    assert config['encoder_identity']['provider'] == 'openrouter'
    assert config['resource_budget']['batch_size'] == 32
    assert config['resource_budget']['concurrency'] == 1
    assert 'UNCALIBRATED' in config['validation_scope']
    assert config['preprocessing']['prefix'] == ''
    records = [{'id': str(i), 'normalized_fields': {'name_unicode': 'same', 'address': 'same address',
                '_field_quality': {'name': 'VALID', 'address': 'VALID'}}} for i in range(3)]
    calls, cache = [], {}
    def encode(texts, field):
        calls.append((texts, field))
        return [[1., 0., 0.] for _ in texts]
    agent = EmbeddingAgent(encode, encoder_digest=config['encoder_identity']['bundle_digest'],
        preprocessing_digest='api-preprocessor-v1', dimension=3, cache=cache, cache_version='text_vectors_v2')
    agent.evaluate(records, [], owner_id='owner', run_id='run')
    assert len(calls) == 2 and len(cache) == 2
    before = deepcopy(cache)
    agent.evaluate(records, [], owner_id='owner', run_id='run')
    assert len(calls) == 2 and cache == before
    agent.encoder_digest = 'different-model'
    agent.evaluate(records, [], owner_id='owner', run_id='run')
    assert len(calls) == 4


def test_api_workflow_reuses_retrieval_cache_for_evidence_and_preserves_review(client, api_settings, monkeypatch):
    from masterone.mdm.matching_agents import openrouter_embedding
    from masterone.mdm.workflow_artifacts import load
    from masterone.mdm.workflow_models import WorkflowArtifact, WorkflowPair

    from tests.test_mdm.test_matching_foundation import REGISTRY, flags, new_run, review
    from tests.test_mdm.test_workflow_contract import request
    from tests.test_mdm.test_workflow_stages import read, ready_decisions, run_one
    flags(monkeypatch)
    monkeypatch.setattr(api_settings, 'advanced_matching_agents_enabled', True)
    monkeypatch.setattr(api_settings, 'advanced_matching_integration_enabled', True)
    calls = []
    def handler(req):
        body = json.loads(req.content)
        calls.append(body)
        return httpx.Response(200, json=reply(len(body['input'])))
    real_client = httpx.Client
    monkeypatch.setattr(openrouter_embedding.httpx, 'Client',
                        lambda **kw: real_client(**{**kw, 'transport': httpx.MockTransport(handler)}))
    response = client.post(REGISTRY + '/profiles', json={'version': 1, 'integration': True})
    assert response.status_code == 201, response.text
    profile = response.json()
    assert client.post(REGISTRY + f"/profiles/{profile['id']}/validate", json={'expected_revision': 1}).status_code == 200
    assert client.put(REGISTRY + '/defaults/owner-private', json={'profile_id': profile['id'], 'expected_revision': 0}).status_code == 200
    run = new_run(client)
    state = ready_decisions(client, run)
    assert state['matching_agents']['status'] == 'OK'
    assert len(calls) == 2, 'One deduplicated call per field; evidence must reuse retrieval vectors'
    with client.session_factory() as db:
        rows = db.query(WorkflowPair).filter_by(run_id=run).all()
        assert rows and all(p.decision == 'UNDECIDED' for p in rows)
        receipts = [load(db, a.id, run, 'AGENT_ENCODER_USAGE') for a in
                    db.query(WorkflowArtifact).filter_by(run_id=run, phase='AGENT_ENCODER_USAGE')]
        assert len([r for r in receipts if r['event'] == 'HTTP_INTENT']) == 2
    response, _, _, pair = review(client, run, 'deny')
    assert response.status_code == 200, response.text
    state = read(client, run)
    assert client.post(f'/api/v1/mdm/runs/{run}/workflow/reassess', json=request(state['workflow_revision'])).status_code == 202
    run_one(client)
    assert len(calls) == 2
    with client.session_factory() as db:
        assert db.get(WorkflowPair, pair['pair_id']).decision == 'NON_MATCH'
    pairs = client.get(f'/api/v1/mdm/runs/{run}/workflow/pairs?limit=100').json()['items']
    assert all(not p['eligibility']['auto_allowed'] for p in pairs)
