from masterone.mdm.workflow_models import WorkflowPair
from tests.test_mdm.test_pair_review_redesign import seed_pairs
from tests.test_mdm.test_workflow_contract import request
from tests.test_mdm.test_workflow_stages import read


def test_review_visibility_filters_page_summary_and_bulk_without_deleting_pairs(client, workflow_run):
    url = seed_pairs(client, workflow_run)
    with client.session_factory.begin() as db:
        pair = db.query(WorkflowPair).filter_by(run_id=workflow_run, id='pair-0011').one()
        pair.baseline = {**pair.baseline, 'primary_group': 'BLOCKED', 'decision': 'BLOCKED'}
    page = client.get(url + '/pairs?visibility=review&offset=10').json()
    assert page['total'] == 11
    assert [p['pair_id'] for p in page['items']] == ['pair-0010']
    summary = client.get(url + '/pairs/summary?visibility=review').json()
    assert summary['total'] == 11
    assert summary['blocked'] == 0
    assert all(g['key'] != 'BLOCKED' for g in summary['groups'])
    state = read(client, workflow_run)
    body = request(state['workflow_revision'], expected_decision_revision=state['decision_revision'],
                   decision='deny', scope={'filter': {'visibility': 'review'}})
    preview = client.post(url + '/bulk-decisions/preview', json=body)
    assert preview.status_code == 200, preview.text
    assert preview.json()['total'] == 11
    assert client.get(url + '/pairs').json()['total'] == 13
    assert client.get(url + '/pairs?visibility=review&group=BLOCKED').json()['total'] == 0


def test_invalid_visibility_is_rejected(client, workflow_run):
    url = seed_pairs(client, workflow_run)
    assert client.get(url + '/pairs?visibility=invalid').status_code == 422
    assert client.get(url + '/pairs/summary?visibility=invalid').status_code == 422
