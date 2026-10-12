"""Effective reads, fenced reassessment and shared review/cluster policy."""
from copy import deepcopy

from masterone.mdm.models import ResolutionRun
from masterone.mdm.workflow_models import WorkflowPair
from masterone.mdm.workflow_pair_reads import pair_summary
from masterone.mdm.workflow_review import eligibility, pair_read, scoped_pairs


def baseline():
    return dict(left_id='a', right_id='b', decision='NOT_MERGEABLE', reason='SAME_TYPE_GOV_ID_CONFLICT',
                primary_group=None, subgroup=None, score=0, comparisons={}, field_evidence=[])


def test_assessment_reads_filters_counts_and_permissions_share_effective_result(client, workflow_run):
    original = baseline()
    assessed = {**original, 'decision': 'REVIEW', 'reason': 'UNVERIFIED_ID_CONFLICT',
                'primary_group': 'D', 'subgroup': 'D_UNVERIFIED_ID_CONFLICT', 'assessment_version': 'v1'}
    with client.session_factory.begin() as db:
        pair = WorkflowPair(id='p', run_id=workflow_run, baseline=deepcopy(original), assessment=assessed)
        db.add(pair)
        db.flush()
        assert pair_read(pair)['baseline'] == assessed
        assert pair_read(pair)['original_baseline'] == original
        assert eligibility(pair)['manual_allowed'] is True
        assert pair.baseline == original
        assert scoped_pairs(db, workflow_run, {'group': 'D'}).count() == 1
        assert scoped_pairs(db, workflow_run, {'group': 'UNCLASSIFIED'}).count() == 0
        summary = pair_summary(db, db.get(ResolutionRun, workflow_run))
        assert summary['blocked'] == 0 and summary['needs_review'] == 1
        assert summary['groups'][0]['key'] == 'D'
        assert summary['groups'][0]['pending'] == 1


def test_historical_pair_still_uses_baseline_and_saved_deny_blocks_assessed_accept():
    pair = WorkflowPair(id='p', run_id='r', baseline=baseline(), decision='UNDECIDED')
    assert pair_read(pair)['baseline'] == baseline()
    assert eligibility(pair)['manual_allowed'] is False
    pair.assessment = {**baseline(), 'decision': 'REVIEW', 'primary_group': 'C'}
    pair.decision = 'NON_MATCH'
    assert eligibility(pair)['manual_allowed'] is False


def test_new_decide_can_publish_assessment_without_automatic_accept(client, workflow_run, monkeypatch):
    from masterone.config import get_settings
    from tests.test_mdm.test_workflow_stages import ready_decisions
    monkeypatch.setattr(get_settings(), 'pair_assessment_auto_enabled', True)
    state = ready_decisions(client, workflow_run)
    assert state['assessment_id']
    with client.session_factory() as db:
        rows = db.query(WorkflowPair).filter_by(run_id=workflow_run).all()
        assert rows and all(p.assessment_id == state['assessment_id'] for p in rows)
        assert all(p.decision == 'UNDECIDED' for p in rows)
        assert all(p.assessment['decision'] != 'MERGEABLE' for p in rows)


def test_reassessment_is_idempotent_keeps_decisions_and_original_artifact(client, workflow_run):
    from tests.test_mdm.test_workflow_contract import request
    from tests.test_mdm.test_workflow_stages import read, ready_decisions, run_one
    state = ready_decisions(client, workflow_run)
    with client.session_factory.begin() as db:
        pairs = db.query(WorkflowPair).filter_by(run_id=workflow_run).all()
        originals = {p.id: deepcopy(p.baseline) for p in pairs}
        pairs[0].decision = 'ACCEPTED'
        accepted_id = pairs[0].id
    url = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    body = request(state['workflow_revision'])
    response = client.post(url + '/reassess', json=body)
    assert response.status_code == 202, response.text
    assert client.post(url + '/reassess', json=body).json() == response.json()
    run_one(client)
    state = read(client, workflow_run)
    assert state['assessment_id']
    with client.session_factory() as db:
        for pair in db.query(WorkflowPair).filter_by(run_id=workflow_run):
            assert pair.baseline == originals[pair.id]
            assert pair.assessment['primary_group']
            assert pair.assessment_id == state['assessment_id']
        assert db.get(WorkflowPair, accepted_id).decision == 'ACCEPTED'
    summary = client.get(url + '/pairs/summary').json()
    assert summary['assessment_id'] == state['assessment_id']
    assert 'UNCLASSIFIED' not in {g['key'] for g in summary['groups']}


def test_finalize_pins_the_effective_assessment_and_cluster_policy(client, workflow_run):
    from masterone.mdm.workflow_models import WorkflowArtifact
    from tests.test_mdm.test_workflow_contract import request
    from tests.test_mdm.test_workflow_stages import read, ready_decisions, run_one
    state = ready_decisions(client, workflow_run)
    url = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    assert client.post(url + '/reassess', json=request(state['workflow_revision'])).status_code == 202
    run_one(client)
    state = read(client, workflow_run)
    with client.session_factory.begin() as db:
        for pair in db.query(WorkflowPair).filter_by(run_id=workflow_run):
            pair.decision = 'ACCEPTED' if pair.assessment['primary_group'] == 'A' else 'REJECTED'
    response = client.post(url + '/finalize-decisions', json=request(state['workflow_revision'],
        expected_decision_revision=state['decision_revision']))
    assert response.status_code == 202, response.text
    with client.session_factory() as db:
        artifact = db.query(WorkflowArtifact).filter_by(run_id=workflow_run, phase='DECISION_SNAPSHOT').one()
        assert artifact.payload.get('assessment_id') == state['assessment_id']
        assert artifact.payload['profile']['decision_semantics_v2_enabled'] is True
    run_one(client)
    assert read(client, workflow_run)['workflow_state'] == 'GOLDEN_REVIEW'
