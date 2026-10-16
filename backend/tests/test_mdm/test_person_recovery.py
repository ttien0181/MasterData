"""Order-independent PERSON recovery; candidate evidence never authorizes merge."""
from copy import deepcopy

import pytest
from masterone.mdm.matching_agents.candidate_union import build_union

from tests.test_mdm.test_matching_candidates import config


def row(rid, name, dob, kind='PERSON', quality='VALID'):
    return {'id': rid, 'entity_type': kind, 'normalized_fields': {
        'name': name, 'dob': dob, '_field_quality': {'name': quality, 'dob': 'VALID'}}}


def settings(enabled=True):
    cfg = config()
    cfg['experiment'] = 'E0'
    cfg['retrieval']['person_recovery'] = enabled
    return cfg


def test_recovery_finds_reordered_shortened_names_and_year_corruption_without_truth():
    rows = [row('a', 'Nguyễn Văn Bình', '1982-02-03'),
            row('b', 'binh nguyen', '1982-02-03'), row('c', 'nguyen binh', '1983-02-03')]
    baseline = build_union(rows, settings(False), None, [], lambda *_: None)
    result = build_union(rows, settings(), None, [], lambda *_: None)
    assert set(baseline.pairs) <= set(result.pairs)
    assert 'person_token_full_dob' in result.pairs['a', 'b']
    assert 'person_tokens_month_day' in result.pairs['a', 'c']
    assert sum(p['new_unique'] for p in result.metrics['passes'].values()) == len(result.pairs)
    assert result == build_union(list(reversed(rows)), settings(), None, [], lambda *_: None)
    assert all('Nguyễn' not in str(o) for o in result.metrics['provenance'])


@pytest.mark.parametrize('bad', [row('b', 'nguyen binh', '1982-02-03', 'ORGANIZATION'),
    row('b', 'nguyen binh', '1982-02-03', quality='PLACEHOLDER'),
    row('b', 'nguyen binh', '1982'), row('b', 'nguyen binh', '1982-02-31')])
def test_recovery_masks_invalid_and_non_person_records(bad):
    result = build_union([row('a', 'binh nguyen', '1982-02-03'), bad], settings(), None, [], lambda *_: None)
    assert not any(o['pass'].startswith('person_') for o in result.metrics['provenance'])


def test_recovery_oversized_blocks_are_logged_and_budget_fails_closed():
    rows = [row(str(i), 'binh nguyen', '1982-02-03') for i in range(31)]
    result = build_union(rows, settings(), None, [], lambda *_: None)
    assert result.metrics['person_recovery']['oversized_blocks'] == 3
    assert not any(o['pass'].startswith('person_') for o in result.metrics['provenance'])
    cfg = settings()
    cfg['execution_budget']['unique_pairs'] = 1
    with pytest.raises(ValueError, match='CANDIDATE_BUDGET'):
        build_union([row('a', 'nguyen van binh', '1982-02-03'),
            row('b', 'binh nguyen', '1983-02-03'), row('c', 'nguyen binh', '1984-02-03')], cfg, None, [], lambda *_: None)


def test_recovery_does_not_mutate_input():
    rows = [row('a', 'binh nguyen', '1982-02-03'), row('b', 'van binh nguyen', '1982-02-03')]
    before = deepcopy(rows)
    build_union(rows, settings(), None, [], lambda *_: None)
    assert rows == before


def test_recovery_profile_is_opt_in_pinned_and_rejects_irrelevant_modes(client, monkeypatch):
    from masterone.config import get_settings

    from tests.test_mdm.test_matching_foundation import REGISTRY, flags
    flags(monkeypatch)
    for flag in ('advanced_matching_integration_enabled', 'advanced_matching_agents_enabled'):
        monkeypatch.setattr(get_settings(), flag, True)
    request = {'version': 1, 'integration': True, 'integration_experiment': 'E0', 'person_recovery': True}
    response = client.post(REGISTRY + '/profiles', json=request)
    assert response.status_code == 201, response.text
    profile = response.json()
    assert profile['config']['experiment'] == 'E0'
    assert profile['config']['retrieval']['person_recovery'] is True
    assert profile['config']['retrieval']['version'].endswith('_person_name_dob_v1')
    validated = client.post(REGISTRY + f"/profiles/{profile['id']}/validate", json={'expected_revision': 1})
    assert validated.status_code == 200, validated.text
    for invalid in ({'version': 2, 'person_recovery': True},
                    {**request, 'integration_experiment': 'E2'},
                    {'version': 2, 'integration_experiment': 'E0'}):
        assert client.post(REGISTRY + '/profiles', json=invalid).status_code == 422
    default = client.post(REGISTRY + '/profiles', json={'version': 3, 'integration': True}).json()['config']
    assert default['experiment'] == 'E3'
    assert 'person_recovery' not in default['retrieval']


def test_legacy_engine_receives_union_budget_before_allocating_provenance(monkeypatch):
    import masterone.mdm.matching_agents.candidate_union as module
    original = module.CandidateEngine
    observed = []
    class Measured(original):
        def __init__(self, cfg=None):
            super().__init__(cfg)
            observed.append(self.config.max_unique_pairs)
    monkeypatch.setattr(module, 'CandidateEngine', Measured)
    cfg = settings(False)
    cfg['execution_budget']['unique_pairs'] = 17
    build_union([row('a', 'binh nguyen', '1982-02-03')], cfg, None, [], lambda *_: None)
    assert observed == [17]


def test_recovery_does_not_override_verified_dob_or_negative_decisions():
    from masterone.mdm.pipeline.decision_semantics import person_constraints
    from masterone.mdm.pipeline.pair_assessment import assess_pair
    rows = [row('a', 'binh nguyen', '1982-02-03'), row('b', 'nguyen van binh', '1983-02-03')]
    for record in rows:
        record['normalized_fields'].update(dob_verification_status='verified', _dob_verified=True)
    result = build_union(rows, settings(), None, [], lambda *_: None)
    assert 'person_tokens_month_day' in result.pairs['a', 'b']
    assert 'VERIFIED_DOB_CONFLICT' in person_constraints(*rows).violations
    assert assess_pair(*rows)['decision'] == 'NOT_MERGEABLE'
    rows[1]['normalized_fields']['dob'] = rows[0]['normalized_fields']['dob']
    assert assess_pair(*rows, negative_pairs=frozenset({('a', 'b')}))['decision'] == 'NOT_MERGEABLE'


def test_recovery_profile_runs_real_api_worker_without_encoder_or_automatic_merge(client, monkeypatch):
    from masterone.config import get_settings
    from masterone.mdm.matching_agents import workflow_agents
    from masterone.mdm.workflow_artifacts import load
    from masterone.mdm.workflow_models import WorkflowOperation

    from tests.test_mdm.test_matching_foundation import REGISTRY, flags
    from tests.test_mdm.test_workflow_stages import ready_decisions
    flags(monkeypatch)
    for flag in ('advanced_matching_integration_enabled', 'advanced_matching_agents_enabled'):
        monkeypatch.setattr(get_settings(), flag, True)
    def unexpected_encoder(*args, **kwargs):
        pytest.fail('E0 recovery must not call embedding provider')
    monkeypatch.setattr(workflow_agents, 'EncoderRuntime', unexpected_encoder)
    request = {'version': 1, 'integration': True, 'integration_experiment': 'E0', 'person_recovery': True}
    response = client.post(REGISTRY + '/profiles', json=request)
    assert response.status_code == 201, response.text
    pid = response.json()['id']
    assert client.post(REGISTRY + f'/profiles/{pid}/validate', json={'expected_revision': 1}).status_code == 200
    assert client.put(REGISTRY + '/defaults/owner-private', json={'profile_id': pid, 'expected_revision': 0}).status_code == 200
    csv = ('source_record_id,source_system,entity_type,name,dob\n'
           '1,CRM,PERSON,Nguyen Van Binh,1982-02-03\n'
           '2,ERP,PERSON,Binh Nguyen,1983-02-03\n')
    response = client.post('/api/v1/mdm/runs', data={'workflow_version': '2'},
        files={'files': ('recovery.csv', csv.encode(), 'text/csv')})
    assert response.status_code == 201, response.text
    run = response.json()['run_id']
    state = ready_decisions(client, run)
    assert 'automation-apply' not in state['allowed_actions']
    with client.session_factory() as db:
        op = db.query(WorkflowOperation).filter_by(run_id=run, operation_type='DECIDE').one()
        payload = load(db, op.artifact_id, run, 'DECIDE')
        assert payload['candidate_metrics']['person_recovery']['version'] == 'person_name_dob_v1'
        provenance = load(db, payload['candidate_metrics']['provenance_artifact_id'], run, 'CANDIDATE_PROVENANCE')
        assert any(o['pass'] == 'person_tokens_month_day' for o in provenance['observations'])
    # The exact immutable profile used by DECIDE must also support a no-provider rebuild.
    from masterone.mdm.generation_staging import digest
    from masterone.mdm.matching_foundation_models import MatchingProfile

    from tests.test_mdm.test_matching_rebuild import rebuild_body
    from tests.test_mdm.test_workflow_stages import run_one
    with client.session_factory() as db:
        pinned = db.get(MatchingProfile, pid)
        profile = {'id': pid, 'digest': pinned.config_digest, 'config': pinned.config_json}
    body = rebuild_body(client, run, profile)
    assert body['workload_plan_hash'] == digest({'profile_digest': profile['digest'], 'budget': profile['config']['execution_budget']})
    response = client.post(REGISTRY + f'/runs/{run}/rebuild-candidates', json=body)
    assert response.status_code == 202, response.text
    run_one(client)
    with client.session_factory() as db:
        op = db.get(WorkflowOperation, response.json()['operation_id'])
        assert op.status == 'COMPLETED'
        assert op.result['matching_agents'] is None


def test_recovery_reserves_workspace_even_when_no_pairs_are_emitted():
    cfg = settings()
    cfg['execution_budget']['workspace_bytes'] = 1
    with pytest.raises(ValueError, match='RETRIEVAL_BUDGET'):
        build_union([row('a', 'binh nguyen', '1982-02-03')], cfg, None, [], lambda *_: None)
