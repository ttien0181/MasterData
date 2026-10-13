import csv
from io import StringIO

from tests.test_mdm.test_workflow_contract import request
from tests.test_mdm.test_workflow_finalize import complete_reviews, finalize
from tests.test_mdm.test_workflow_stages import read, ready_decisions, run_one


def ready_golden(client, run_id):
    ready_decisions(client, run_id)
    complete_reviews(client, run_id)
    assert finalize(client, run_id).status_code == 202
    run_one(client)
    return client.get(f'/api/v1/mdm/runs/{run_id}/workflow/clusters').json()['items']


def golden_request(client, run_id, **extra):
    state = read(client, run_id)
    return request(state['workflow_revision'], expected_cluster_revision=state['cluster_revision'],
                   expected_golden_revision=state['golden_revision'], **extra)


def test_workflow_golden_choices_confirm_export_reopen(client, workflow_run):
    clusters = ready_golden(client, workflow_run)
    base = f'/api/v1/mdm/runs/{workflow_run}'
    url = base + '/workflow'
    cluster = next(c for c in clusters if c['member_count'] > 1)
    cid = cluster['cluster_id']
    assert client.post(base + '/previews', json={'choices': {}, 'acknowledge_unreviewed': False}).status_code == 409
    body = golden_request(client, workflow_run, selections={'name': {'selection_mode': 'MANUAL_OVERRIDE', 'value': 'Chosen Server Name'}})
    first = client.put(url + f'/clusters/{cid}/golden', json=body)
    assert first.status_code == 200, first.text
    assert client.put(url + f'/clusters/{cid}/golden', json=body).json() == first.json()
    assert client.put(url + f'/clusters/{cid}/golden', json={**body, 'request_id': 'stale'}).status_code == 409
    auto = client.post(url + '/golden/auto-select', json=golden_request(client, workflow_run, cluster_ids=[cid]))
    assert auto.status_code == 200, auto.text
    golden = client.get(url + f'/clusters/{cid}/golden').json()
    assert golden['fields']['name']['value'] == 'Chosen Server Name'
    assert golden['selections']['name']['selection_mode'] == 'MANUAL_OVERRIDE'
    bad = client.put(url + f'/clusters/{cid}/golden', json=golden_request(client, workflow_run,
        selections={'name': {'selection_mode': 'SOURCE_CHOICE', 'source_record_id': 'outside'}}))
    assert bad.status_code == 422
    for c in clusters:
        response = client.post(url + f"/clusters/{c['cluster_id']}/golden/confirm", json=golden_request(client, workflow_run))
        assert response.status_code == 200, response.text
    state = read(client, workflow_run)
    final_body = request(state['workflow_revision'], expected_golden_revision=state['golden_revision'])
    response = client.post(url + '/finalize-golden', json=final_body)
    assert response.status_code == 200, response.text
    assert client.post(url + '/finalize-golden', json=final_body).json() == response.json()
    preview = client.post(base + '/previews', json={'choices': {cid: {'name': 'browser'}}, 'acknowledge_unreviewed': False})
    assert preview.status_code == 422
    preview = client.post(base + '/previews', json={'choices': {}, 'acknowledge_unreviewed': False})
    assert preview.status_code == 200, preview.text
    exported = client.post(base + '/previews/' + preview.json()['preview_id'] + '/execute', json={'execute_request_id': 'export-test'})
    assert exported.status_code == 200, exported.text
    rows = list(csv.DictReader(StringIO(exported.text)))
    assert any(r['full_name'] == 'Chosen Server Name' for r in rows)
    assert sum(int(r['record_count']) for r in rows) == 3
    exports = client.get(base + '/exports').json()
    export_id = exports['items'][0]['id'] if isinstance(exports, dict) else exports[0]['id']
    history = client.get(base + f'/exports/{export_id}/download').content
    state = read(client, workflow_run)
    reopened = client.post(url + '/reopen-decisions', json=request(state['workflow_revision'], reason='Review again'))
    assert reopened.status_code == 200, reopened.text
    assert read(client, workflow_run)['workflow_state'] == 'DECIDE_REVIEW'
    previous = client.get(url + '/clusters')
    assert previous.status_code == 200
    assert previous.json()['generation']['stale'] is True
    assert previous.json()['generation']['read_only'] is True
    assert client.post(base + '/previews', json={'choices': {}, 'acknowledge_unreviewed': False}).status_code == 409
    assert client.get(base + f'/exports/{export_id}/download').content == history
    assert finalize(client, workflow_run).status_code == 202
    run_one(client)
    cluster = next(c for c in client.get(url + '/clusters').json()['items'] if c['member_count'] > 1)
    restored = client.get(url + f"/clusters/{cluster['cluster_id']}/golden").json()
    assert restored['fields']['name']['value'] == 'Chosen Server Name'
    assert restored['status'] == 'DRAFT'
