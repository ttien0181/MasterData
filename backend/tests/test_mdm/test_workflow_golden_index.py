from tests.test_mdm.test_workflow_golden import golden_request, ready_golden


def test_golden_index_filters_whole_generation_and_returns_lightweight_rows(client, workflow_run, monkeypatch):
    groups = ready_golden(client, workflow_run)
    base = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    cid = groups[0]['cluster_id']
    response = client.put(base + f'/clusters/{cid}/golden', json=golden_request(client, workflow_run,
        selections={'name': {'selection_mode': 'MANUAL_OVERRIDE', 'value': 'Nguyễn Minh An'}}))
    assert response.status_code == 200
    assert client.post(base + f'/clusters/{cid}/golden/confirm',
                       json=golden_request(client, workflow_run)).status_code == 200
    # List/summary reads must not hydrate all alternatives and member payloads.
    monkeypatch.setattr('masterone.mdm.golden_service.golden_read', lambda *a, **k: (_ for _ in ()).throw(AssertionError('hydrated detail')))
    first = client.get(base + '/golden/index?limit=1&status=all')
    assert first.status_code == 200, first.text
    result = first.json()
    assert result['summary'] == {'total': len(groups), 'confirmed': 1, 'needs_review': len(groups) - 1}
    assert len(result['items']) == 1
    assert 'alternatives' not in result['items'][0]
    assert 'members' not in result['items'][0]
    if result['next_cursor']:
        second = client.get(base + '/golden/index', params={'cursor': result['next_cursor'], 'limit': 1, 'status': 'all'}).json()
        assert second['items'][0]['cluster_id'] != result['items'][0]['cluster_id']
    reviewed = client.get(base + '/golden/index?status=confirmed&q=nguyen%20minh').json()
    assert reviewed['total'] == 1
    assert reviewed['items'][0]['display_name'] == 'Nguyễn Minh An'
    pending = client.get(base + '/golden/index?status=review').json()
    assert pending['total'] == len(groups) - 1
    assert all(row['status'] == 'DRAFT' for row in pending['items'])


def test_virtual_index_reads_only_visible_goldens_for_unsearched_pages(client, workflow_run, monkeypatch):
    from masterone.mdm import workflow_golden_index as index

    from tests.test_mdm.test_virtual_singletons import virtual_fixture
    virtual_fixture(client, workflow_run, count=100)
    original = index.selected_golden
    visited = []

    def selected(*args):
        visited.append(args[1].id)
        return original(*args)

    monkeypatch.setattr(index, 'selected_golden', selected)
    base = f'/api/v1/mdm/runs/{workflow_run}/workflow/golden/index'
    response = client.get(base + '?limit=2&status=review')
    assert response.status_code == 200, response.text
    assert response.json()['summary']['needs_review'] == 100
    assert len(response.json()['items']) == len(visited) == 2
    assert response.json()['next_cursor'] is not None
    visited.clear()
    result = client.get(base + '?limit=2&status=confirmed').json()
    assert result['items'] == []
    assert visited == []
