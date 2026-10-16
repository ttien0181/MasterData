from unittest.mock import Mock

from masterone.mdm import workflow_pair_reads as reads
from masterone.mdm.models import ResolutionRun
from masterone.mdm.workflow_contracts import BulkRequest
from masterone.mdm.workflow_models import WorkflowPair
from tests.test_mdm.test_pair_review_redesign import seed_pairs
from tests.test_mdm.test_workflow_contract import request
from tests.test_mdm.test_workflow_stages import read


def body_for(client, run, group):
    state = read(client, run)
    return request(state['workflow_revision'], expected_decision_revision=state['decision_revision'],
                   expected_assessment_id=state.get('assessment_id'), decision='merge',
                   scope={'filter': {'group': group, 'visibility': 'review', 'taxonomy': 'actions_v1'}})


def test_zero_eligible_scope_reuses_summary_without_scanning_or_hashing_pairs(client, workflow_run, monkeypatch):
    url = seed_pairs(client, workflow_run)
    with client.session_factory.begin() as db:
        for pair in db.query(WorkflowPair).filter_by(run_id=workflow_run):
            pair.baseline = {**pair.baseline, 'primary_group': 'C', 'subgroup': 'C_NO_VALID_IDENTITY_ANCHOR',
                             'decision': 'INSUFFICIENT_EVIDENCE', 'coordinator_version': 'r1_v1',
                             'eligibility': {'manual_allowed': False, 'auto_allowed': False, 'blocked_reasons': ['NO_VALID_IDENTITY_ANCHOR']}}
    summary = client.get(url + '/pairs/summary?visibility=review&taxonomy=actions_v1').json()
    group = next(g for g in summary['groups'] if g['key'] == 'C')
    assert group['manual_available'] == 0
    assert group['unresolved'] == 13
    assert group['subgroups'][0]['manual_available'] == 0
    aggregate = Mock(side_effect=AssertionError('must reuse cached summary'))
    token = Mock(side_effect=AssertionError('zero eligibility needs no receipt'))
    monkeypatch.setattr(reads, 'aggregate', aggregate)
    monkeypatch.setattr(reads, 'bulk_preview_token', token)
    body = body_for(client, workflow_run, 'C')
    response = client.post(url + '/bulk-decisions/preview', json=body)
    assert response.status_code == 200, response.text
    assert response.json()['blocked'] == 13
    assert response.json()['applicable'] == 0
    assert 'preview_token' not in response.json()
    again = client.post(url + '/bulk-decisions/preview', json={**body, 'request_id': 'reopen-same-scope'})
    assert again.json() == response.json()
    aggregate.assert_not_called()
    token.assert_not_called()


def test_positive_group_a_reuses_counts_but_keeps_full_evidence_receipt(client, workflow_run, monkeypatch):
    url = seed_pairs(client, workflow_run)
    client.get(url + '/pairs/summary?visibility=review&taxonomy=actions_v1')
    body = body_for(client, workflow_run, 'A')
    monkeypatch.setattr(reads, 'aggregate', Mock(side_effect=AssertionError('duplicate aggregate')))
    response = client.post(url + '/bulk-decisions/preview', json=body)
    assert response.status_code == 200, response.text
    assert response.json()['applicable'] == 12
    with client.session_factory() as db:
        run = db.get(ResolutionRun, workflow_run)
        reads.validate_bulk_preview(db, run, {**BulkRequest(**body).model_dump(), 'preview_token': response.json()['preview_token']})


def test_empty_legacy_preview_skips_receipt_after_counts(client, workflow_run, monkeypatch):
    url = seed_pairs(client, workflow_run)
    state = read(client, workflow_run)
    body = request(state['workflow_revision'], expected_decision_revision=state['decision_revision'],
                   decision='merge', scope={'filter': {'group': 'MISSING'}})
    monkeypatch.setattr(reads, 'bulk_preview_token', Mock(side_effect=AssertionError('empty scope')))
    result = client.post(url + '/bulk-decisions/preview', json=body)
    assert result.status_code == 200
    assert result.json()['total'] == 0
    assert 'preview_token' not in result.json()


def test_preview_remains_read_only_after_entering_golden_review(client, workflow_run):
    url = seed_pairs(client, workflow_run)
    with client.session_factory.begin() as db:
        run = db.get(ResolutionRun, workflow_run)
        run.workflow_state = 'GOLDEN_REVIEW'
        run.workflow_revision += 1
    body = body_for(client, workflow_run, 'A')
    result = client.post(url + '/bulk-decisions/preview', json=body)
    assert result.status_code == 200, result.text
    assert result.json()['applicable'] == 12
    with client.session_factory() as db:
        assert db.get(ResolutionRun, workflow_run).workflow_state == 'GOLDEN_REVIEW'
