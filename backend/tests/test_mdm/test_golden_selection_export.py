import csv
from io import StringIO
from tests.test_mdm.test_workflow_golden import golden_request, ready_golden
from tests.test_mdm.test_workflow_stages import read


def rows(response):
    assert response.status_code == 200, response.text
    assert response.content.startswith(b'\xef\xbb\xbf')
    return list(csv.DictReader(StringIO(response.content.decode('utf-8-sig'))))


def test_exports_selected_saved_values_without_confirming_or_finalizing(client, workflow_run):
    groups = ready_golden(client, workflow_run)
    base = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    cid = groups[0]['cluster_id']
    assert client.put(base + f'/clusters/{cid}/golden', json=golden_request(client, workflow_run,
        selections={'name': {'selection_mode': 'MANUAL_OVERRIDE', 'value': 'Nguyễn, Minh An'}})).status_code == 200
    before = read(client, workflow_run)
    exported = rows(client.post(base + '/golden/export', json=golden_request(client, workflow_run, cluster_ids=[cid])))
    assert len(exported) == 1
    assert exported[0]['Họ tên'] == 'Nguyễn, Minh An'
    assert exported[0]['Mã Golden record'] == cid
    after = read(client, workflow_run)
    assert (after['workflow_state'], after['golden_revision']) == (before['workflow_state'], before['golden_revision'])


def test_exports_all_pages_except_excluded_and_rejects_stale_or_foreign_selection(client, workflow_run):
    groups = ready_golden(client, workflow_run)
    base = f'/api/v1/mdm/runs/{workflow_run}/workflow/golden/export'
    all_rows = rows(client.post(base, json=golden_request(client, workflow_run)))
    assert len(all_rows) == len(groups)
    cid = groups[0]['cluster_id']
    selected = rows(client.post(base, json=golden_request(client, workflow_run, excluded_cluster_ids=[cid])))
    assert len(selected) == len(groups) - 1
    assert all(r['Mã Golden record'] != cid for r in selected)
    assert client.post(base, json=golden_request(client, workflow_run, cluster_ids=['other-run-id'])).status_code == 404
    body = golden_request(client, workflow_run)
    body['expected_golden_revision'] += 1
    assert client.post(base, json=body).status_code == 409


def test_csv_formula_cells_are_safe_and_virtual_all_is_complete(client, workflow_run):
    from tests.test_mdm.test_virtual_singletons import virtual_fixture
    virtual_fixture(client, workflow_run, count=30)
    base = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    page = client.get(base + '/golden/index?limit=1').json()
    cid = page['items'][0]['cluster_id']
    assert client.put(base + f'/clusters/{cid}/golden', json=golden_request(client, workflow_run,
        selections={'name': {'selection_mode': 'MANUAL_OVERRIDE', 'value': '=1+1'}})).status_code == 200
    exported = rows(client.post(base + '/golden/export', json=golden_request(client, workflow_run)))
    assert len(exported) == 30
    assert next(r for r in exported if r['Mã Golden record'] == cid)['Họ tên'] == "'=1+1"

def test_export_checks_permission_ownership_and_csrf(client, workflow_run):
    from masterone.auth.models import User
    from masterone.mdm.models import ResolutionRun
    ready_golden(client, workflow_run)
    base = f'/api/v1/mdm/runs/{workflow_run}/workflow/golden/export'
    body = golden_request(client, workflow_run)
    token = client.headers.pop('X-CSRF-Token')
    assert client.post(base, json=body).status_code == 403
    client.headers['X-CSRF-Token'] = token
    with client.session_factory.begin() as db:
        db.query(User).filter_by(email_normalized='steward@example.com').one().role = 'VIEWER'
    assert client.post(base, json=body).status_code == 403
    with client.session_factory.begin() as db:
        db.query(User).filter_by(email_normalized='steward@example.com').one().role = 'STEWARD'
        db.get(ResolutionRun, workflow_run).owner_user_id = 'another-owner'
    assert client.post(base, json=body).status_code == 404
