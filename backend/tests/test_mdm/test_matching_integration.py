"""Approved M2-B config and canonical workflow integration."""
from pathlib import Path

import pytest
from masterone.config import get_settings

TRIAL = Path(__file__).resolve().parents[3] / 'artifacts/advanced-m2a-20261014/e5-trial-01'


@pytest.fixture(autouse=True)
def historical_e5_baseline(monkeypatch):
    monkeypatch.setattr(get_settings(), 'embedding_provider', 'e5_local')


@pytest.fixture
def fake_encoder(monkeypatch):
    """Protocol-only vectors for lifecycle tests; never quality evidence."""
    class Runtime:
        peak_rss = calls = retry_count = batch_attempts = 0
        usage = {}
        def __init__(self, *args):
            self.dimension = args[0]['dimension']
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def encode(self, texts, field):
            self.calls += 1
            return [[1.] + [0.] * (self.dimension - 1) for _ in texts]
    monkeypatch.setattr('masterone.mdm.matching_agents.workflow_agents.EncoderRuntime', Runtime)


def test_integration_profile_pins_approved_parameters(monkeypatch):
    from masterone.mdm.matching_agents.integration_profile import integration_config
    from masterone.mdm.matching_foundation_service import baseline_config
    monkeypatch.setattr(get_settings(), 'embedding_runtime_root', str(TRIAL))
    base = baseline_config()
    result = integration_config(base, 'E3')
    assert base['kind'] == 'RULE_ONLY'
    assert result['score_unit'] == 'p119_rule_v1'
    assert result['retrieval']['embedding'] == {'top_k': 10, 'min_similarity': .85}
    assert result['retrieval']['tfidf'] == {'top_k': 10, 'min_similarity': .30}
    assert result['coordinator'] == {'version': 'r1_v1', 'high': .85, 'low': .50}
    assert result['execution_budget']['unique_pairs'] == 200000
    assert result['execution_budget']['warning_pairs'] == 150000
    assert result['execution_budget']['similarity_cells'] == 100000000
    assert result['execution_budget']['disk_bytes'] == 12 * 1024**3
    assert result['resource_budget']['worker_rss_bytes'] == 8 * 1024**3
    assert not get_settings().advanced_matching_integration_enabled
    assert integration_config(base, 'E3') == result
    with pytest.raises(ValueError, match='EXPERIMENT'):
        integration_config(base, 'E4')


def test_operational_budget_is_explicitly_pinned_without_changing_matching(monkeypatch):
    from masterone.mdm.matching_agents.integration_profile import integration_config
    from masterone.mdm.matching_foundation_service import baseline_config
    settings = get_settings()
    monkeypatch.setattr(settings, 'embedding_runtime_root', str(TRIAL))
    first = integration_config(baseline_config())
    monkeypatch.setattr(settings, 'advanced_matching_candidate_cap', 500000)
    monkeypatch.setattr(settings, 'advanced_matching_rule_workers', 4)
    second = integration_config(baseline_config())
    assert second['execution_budget']['unique_pairs'] == 500000
    assert second['execution_budget']['rule_workers'] == 4
    assert second['retrieval'] == first['retrieval']
    assert second['coordinator'] == first['coordinator']


def test_profile_opt_in_and_validation_preserve_legacy(client, monkeypatch):
    from tests.test_mdm.test_matching_foundation import REGISTRY, flags
    flags(monkeypatch)
    assert client.post(REGISTRY + '/profiles', json={'version': 1, 'integration': True}).status_code == 404
    monkeypatch.setattr(get_settings(), 'advanced_matching_integration_enabled', True)
    monkeypatch.setattr(get_settings(), 'advanced_matching_agents_enabled', True)
    monkeypatch.setattr(get_settings(), 'embedding_runtime_root', str(TRIAL))
    response = client.post(REGISTRY + '/profiles', json={'version': 1, 'integration': True})
    assert response.status_code == 201, response.text
    profile = response.json()
    assert profile['config']['kind'] == 'RULE_HYBRID_DETERMINISTIC'
    assert client.post(REGISTRY + f"/profiles/{profile['id']}/validate", json={'expected_revision': 1}).status_code == 200
    legacy = client.post(REGISTRY + '/profiles', json={'version': 2}).json()
    assert legacy['config']['kind'] == 'RULE_ONLY'
    assert legacy['config']['encoder_identity'] is None
    assert client.post(REGISTRY + '/profiles', json={'version': 3, 'integration': True,
                                                   'advanced_agents': True}).status_code == 422


def hybrid_profile(client, monkeypatch):
    from tests.test_mdm.test_matching_foundation import REGISTRY, flags
    flags(monkeypatch)
    monkeypatch.setattr(get_settings(), 'advanced_matching_integration_enabled', True)
    monkeypatch.setattr(get_settings(), 'advanced_matching_agents_enabled', True)
    monkeypatch.setattr(get_settings(), 'embedding_runtime_root', str(TRIAL))
    response = client.post(REGISTRY + '/profiles', json={'version': 1, 'integration': True})
    assert response.status_code == 201, response.text
    row = response.json()
    assert client.post(REGISTRY + f"/profiles/{row['id']}/validate", json={'expected_revision': 1}).status_code == 200
    assert client.put(REGISTRY + '/defaults/owner-private', json={'profile_id': row['id'], 'expected_revision': 0}).status_code == 200
    return row


def test_coordinator_streams_sealed_evidence_without_loading_full_report(client, monkeypatch, fake_encoder):
    from masterone.mdm.matching_agents import workflow_agents

    from tests.test_mdm.test_matching_foundation import new_run
    from tests.test_mdm.test_workflow_stages import ready_decisions
    original = workflow_agents.load

    def bounded_load(db, artifact_id, run_id, phase):
        assert phase != 'MATCHING_AGENTS', 'Coordinator must read verified partitions instead of the full report'
        return original(db, artifact_id, run_id, phase)

    monkeypatch.setattr(workflow_agents, 'load', bounded_load)
    hybrid_profile(client, monkeypatch)
    state = ready_decisions(client, new_run(client))
    assert state['matching_agents']['status'] == 'OK'


def test_coordinator_in_real_workflow_before_review_and_scoring(client, monkeypatch):
    from masterone.mdm.workflow_artifacts import load
    from masterone.mdm.workflow_models import WorkflowOperation, WorkflowPair

    from tests.test_mdm.test_matching_foundation import new_run
    from tests.test_mdm.test_workflow_stages import ready_decisions
    profile = hybrid_profile(client, monkeypatch)
    run = new_run(client)
    state = ready_decisions(client, run)
    assert state['matching_agents'] and state['matching_agents']['profile_digest'] == profile['digest']
    with client.session_factory() as db:
        op = db.query(WorkflowOperation).filter_by(run_id=run, operation_type='DECIDE').one()
        payload = load(db, op.artifact_id, run, 'DECIDE')
        assert payload['candidate_metrics']['contract_version'] == 'candidate_union_v1'
        assert payload['scoring_metrics']['scored_pairs'] == payload['candidate_count']
        rows = db.query(WorkflowPair).filter_by(run_id=run).all()
        assert rows and all(p.assessment['coordinator_version'] == 'r1_v1' for p in rows)
        assert all(p.decision == 'UNDECIDED' for p in rows)
    pairs = client.get(f'/api/v1/mdm/runs/{run}/workflow/pairs?limit=100').json()['items']
    assert pairs
    assert all(not p['eligibility']['auto_allowed'] for p in pairs)
    for pair in pairs:
        if pair['baseline']['decision'] == 'INSUFFICIENT_EVIDENCE':
            assert not pair['eligibility']['manual_allowed']


def test_reassessment_keeps_coordinator_universe_and_accepted_history(client, monkeypatch, fake_encoder):
    from copy import deepcopy

    from masterone.mdm.workflow_models import WorkflowPair

    from tests.test_mdm.test_matching_foundation import new_run, review
    from tests.test_mdm.test_workflow_contract import request
    from tests.test_mdm.test_workflow_stages import read, ready_decisions, run_one
    hybrid_profile(client, monkeypatch)
    run = new_run(client)
    ready_decisions(client, run)
    response, _, _, pair = review(client, run, 'merge')
    assert response.status_code == 200, response.text
    with client.session_factory() as db:
        originals = {p.id: deepcopy(p.baseline) for p in db.query(WorkflowPair).filter_by(run_id=run)}
    state = read(client, run)
    response = client.post(f'/api/v1/mdm/runs/{run}/workflow/reassess', json=request(state['workflow_revision']))
    assert response.status_code == 202, response.text
    run_one(client)
    with client.session_factory() as db:
        rows = db.query(WorkflowPair).filter_by(run_id=run).all()
        assert {p.id: p.baseline for p in rows} == originals
        assert all(p.assessment.get('coordinator_version') == 'r1_v1' for p in rows)
        assert db.get(WorkflowPair, pair['pair_id']).decision == 'ACCEPTED'


def test_summary_eligibility_matches_advanced_pair_permissions(client, monkeypatch, fake_encoder):
    from tests.test_mdm.test_matching_foundation import new_run
    from tests.test_mdm.test_workflow_stages import ready_decisions
    hybrid_profile(client, monkeypatch)
    run = new_run(client)
    ready_decisions(client, run)
    root = f'/api/v1/mdm/runs/{run}/workflow'
    pairs = client.get(root + '/pairs?limit=100').json()['items']
    summary = client.get(root + '/pairs/summary').json()
    assert summary['manual_available'] == sum(p['eligibility']['manual_allowed'] for p in pairs)
    assert summary['auto_available'] == 0
    assert all('embedding_evidence' in p['baseline'] for p in pairs)
