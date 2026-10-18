"""Step 2 presentation and processing contracts, preserving operative safeguards."""
from copy import deepcopy

import pytest
from masterone.config import get_settings
from masterone.mdm.workflow_models import WorkflowOperation, WorkflowPair
from masterone.mdm.review_taxonomy import mvp_taxonomy, mvp_columns, taxonomy_columns
from masterone.mdm.matching_agents.decision_coordinator import coordinate_pair
from tests.test_mdm.test_pair_review_redesign import seed_pairs
from tests.test_mdm.test_workflow_contract import request
from tests.test_mdm.test_workflow_stages import read, run_one


def test_mvp_scopes_summary_detail_bulk_and_d_reason_gate(client, workflow_run):
    url = seed_pairs(client, workflow_run, 7)
    changes = [dict(primary_group='A', subgroup='A3_NAME_GOV_ID'),
               dict(primary_group='A', subgroup='A1_NAME_PHONE'),
               dict(primary_group='C', subgroup='C_REVIEW_NAME_FULL_DOB'),
               dict(primary_group='D', subgroup='D_MULTI'),
               dict(primary_group='A', agent_disagreement=True),
               dict(primary_group='C', subgroup='C_INSUFFICIENT_DATA', decision='INSUFFICIENT_EVIDENCE')]
    with client.session_factory.begin() as db:
        for i, change in enumerate(changes):
            pair = db.get(WorkflowPair, f'pair-{i:04}')
            pair.baseline = {**pair.baseline, **change}
    summary = client.get(url + '/pairs/summary?taxonomy=mvp_v1&visibility=review').json()
    assert summary['total'] == 5
    assert {g['key']: g['total'] for g in summary['groups']} == dict(A_STRONG=1, A_CONTACT=1, B_SUPPORT=1, B_CONFLICT=2)
    page = client.get(url + '/pairs?taxonomy=mvp_v1').json()
    assert page['total'] == 5
    assert client.get(url + '/pairs').json()['total'] == 7
    for item in page['items']:
        assert item['mvp_group'] in ('A_STRONG', 'A_CONTACT', 'B_SUPPORT', 'B_CONFLICT')
        assert client.get(url + '/pairs/' + item['pair_id']).json()['mvp_group'] == item['mvp_group']
    assert client.get(url + '/pairs?taxonomy=mvp_v1&group=B_CONFLICT').json()['total'] == 2
    state = read(client, workflow_run)
    body = request(state['workflow_revision'], expected_decision_revision=state['decision_revision'],
                   decision='merge', scope={'filter': {'visibility': 'review', 'taxonomy': 'mvp_v1', 'group': 'B_CONFLICT'}})
    preview = client.post(url + '/bulk-decisions/preview', json=body)
    assert preview.status_code == 200, preview.text
    assert preview.json()['total'] == 2
    assert preview.json()['reason_required'] == 1
    assert preview.json()['group_review_required'] is True
    with client.session_factory() as db:
        assert all(p.decision == 'UNDECIDED' for p in db.query(WorkflowPair).filter_by(run_id=workflow_run))


def test_rule_only_process_rejected_truthfully(client, workflow_run):
    run_one(client)
    state = read(client, workflow_run)
    result = client.post(f'/api/v1/mdm/runs/{workflow_run}/workflow/process', json=request(state['workflow_revision']))
    assert result.status_code == 409
    assert 'MVP_EMBEDDING_PROFILE_REQUIRED' in result.text


@pytest.mark.parametrize('constraints,decision,review,expected', [
    ({'codes': ['ID_CONFLICT']}, 'REVIEW', 'UNDECIDED', False),
    ({'cannot_link': True}, 'REVIEW', 'UNDECIDED', False),
    ({}, 'INSUFFICIENT_EVIDENCE', 'UNDECIDED', False),
    ({}, 'REVIEW', 'NON_MATCH', False),
    ({}, 'REVIEW', 'REJECTED', False),
])
def test_coordinator_never_bypasses_floor_hard_constraints_or_veto(constraints, decision, review, expected):
    rule = dict(primary_group='A', subgroup='A1_NAME_PHONE', decision=decision,
                comparisons={'name': {'state': 'EX'}})
    original = deepcopy(rule)
    output = coordinate_pair(rule, {'name': {'status': 'OK', 'cosine': 0.1}}, review, constraints,
                             {'coordinator': {'version': 'r1_v1', 'high': .85, 'low': .5}})
    assert output['eligibility']['manual_allowed'] is expected
    assert output['eligibility']['auto_allowed'] is False
    assert rule == original


def test_mvp_sql_python_agree():
    from sqlalchemy import Boolean, Column, MetaData, String, Table, create_engine, select
    table = Table('pairs', MetaData(), *(Column(n, String) for n in ('group', 'subgroup', 'decision', 'triage', 'reason')), Column('disagreement', Boolean))
    engine = create_engine('sqlite://')
    table.metadata.create_all(engine)
    rows = [dict(group=g, subgroup=s, decision=d, triage=None, reason=None, disagreement=a)
            for g in (None, 'A', 'C', 'D', 'BLOCKED')
            for s in (None, 'A1_NAME_PHONE', 'A3_NAME_GOV_ID', 'C_REVIEW_NAME_FULL_DOB')
            for d in ('REVIEW', 'INSUFFICIENT_EVIDENCE', 'NOT_MERGEABLE') for a in (False, True)]
    columns = mvp_columns(*taxonomy_columns(*(table.c[n] for n in ('group', 'subgroup', 'decision', 'triage', 'reason', 'disagreement'))))
    with engine.begin() as connection:
        connection.execute(table.insert(), rows)
        for row in connection.execute(select(*table.c, *columns)):
            pair = dict(zip(('primary_group', 'subgroup', 'decision', 'triage_status', 'reason', 'agent_disagreement'), row[:6]))
            assert tuple(row[6:]) == tuple(mvp_taxonomy(pair).values())
    engine.dispose()


def test_mvp_evidence_profile_full_process_and_explicit_reassessment(client, monkeypatch):
    from tests.test_mdm.test_matching_agents_workflow import advanced
    from tests.test_mdm.test_matching_foundation import new_run
    monkeypatch.setattr(get_settings(), 'embedding_provider', 'e5_local')
    monkeypatch.setenv('EMBEDDING_PROVIDER', 'e5_local')
    advanced(client, monkeypatch)
    monkeypatch.setattr(get_settings(), 'demo_fast_mode', True)
    run = new_run(client)
    run_one(client)
    url = f'/api/v1/mdm/runs/{run}/workflow'
    body = request(read(client, run)['workflow_revision'])
    response = client.post(url + '/process', json=body)
    assert response.status_code == 202, response.text
    run_one(client)
    state = read(client, run)
    assert state['active_operation'] is None
    assert state['workflow_state'] == 'DECIDE_REVIEW'
    assert state['matching_agents']['status'] == 'OK'
    summary = client.get(url + '/pairs/summary?taxonomy=mvp_v1').json()
    assert summary['assessment']['mvp_step2'] is True
    # Once accepted, request replay survives a feature toggle and ignores new defaults.
    monkeypatch.setattr(get_settings(), 'advanced_matching_agents_enabled', False)
    monkeypatch.setattr(get_settings(), 'demo_fast_mode', False)
    replay = client.post(url + '/process', json=body)
    assert replay.status_code == 202, replay.text
    assert replay.json()['operation_id'] == response.json()['operation_id']
    changed = client.post(url + '/process', json={**body, 'include_advice': False})
    assert changed.status_code == 409
    monkeypatch.setattr(get_settings(), 'advanced_matching_agents_enabled', True)
    assert summary['assessment']['coordinator'] == {'version': 'r1_v1', 'high': .85, 'low': .5}
    with client.session_factory() as db:
        pairs = db.query(WorkflowPair).filter_by(run_id=run).all()
        assert pairs
        originals = {p.id: deepcopy(p.baseline) for p in pairs}
        assert all(p.assessment['coordinator_version'] == 'r1_v1' for p in pairs)
        assert any(p.assessment['embedding_evidence']['name']['status'] == 'OK' for p in pairs)
        assert all(p.decision == 'UNDECIDED' and not p.assessment['eligibility']['auto_allowed'] for p in pairs)
    response = client.post(url + '/reassess', json=request(state['workflow_revision']))
    assert response.status_code == 202, response.text
    run_one(client)
    with client.session_factory() as db:
        operation = db.get(WorkflowOperation, response.json()['operation_id'])
        assert operation.payload['mvp_step2'] is True
        assert all(p.assessment['coordinator_version'] == 'r1_v1' and p.baseline == originals[p.id]
                   for p in db.query(WorkflowPair).filter_by(run_id=run))
