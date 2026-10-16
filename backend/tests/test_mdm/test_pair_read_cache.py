from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from masterone.mdm import pair_read_cache as cache
from masterone.storage.object_store import LocalObjectStore


@pytest.fixture
def state(monkeypatch):
    cache._entries.clear()
    cache._bytes = 0
    monkeypatch.setattr(cache, 'active_assessment', lambda *_: None)
    monkeypatch.setattr(cache, 'remote_store', lambda: None)
    db = Mock()
    db.bind.url = 'sqlite:///isolated-cache-test'
    db.new, db.dirty, db.deleted = set(), set(), set()
    db.query.return_value.filter.return_value.first.return_value = None
    run = SimpleNamespace(id='run', owner_user_id='owner', workflow_revision=1,
                          decision_revision=0, cluster_revision=0)
    return db, run


def test_repeat_read_is_cached_and_revision_and_owner_fence_results(state):
    db, run = state
    compute = Mock(return_value={'items': [{'decision': 'UNDECIDED'}]})
    first = cache.cached_read(db, run, 'page', {}, compute)
    first['items'][0]['decision'] = 'MUTATED_CLIENT_COPY'
    assert cache.cached_read(db, run, 'page', {}, compute)['items'][0]['decision'] == 'UNDECIDED'
    assert compute.call_count == 1
    run.decision_revision += 1
    cache.cached_read(db, run, 'page', {}, compute)
    run.owner_user_id = 'different-owner'
    cache.cached_read(db, run, 'page', {}, compute)
    assert compute.call_count == 3


def test_active_job_bypasses_cache(state):
    db, run = state
    compute = Mock(return_value={'total': 12})
    cache.cached_read(db, run, 'summary', {}, compute)
    db.query.return_value.filter.return_value.first.return_value = ('active-operation',)
    cache.cached_read(db, run, 'summary', {}, compute)
    assert compute.call_count == 2


def test_r2_summary_survives_process_cache_clear_but_pages_stay_local(state, monkeypatch, tmp_path):
    db, run = state
    store = LocalObjectStore(tmp_path)
    monkeypatch.setattr(cache, 'remote_store', lambda: store)
    compute = Mock(return_value={'total': 12, 'decision_revision': 0})
    assert cache.cached_read(db, run, 'summary', {}, compute)['total'] == 12
    cache._entries.clear()
    cache._bytes = 0
    assert cache.cached_read(db, run, 'summary', {}, compute)['total'] == 12
    assert compute.call_count == 1
    cache.cached_read(db, run, 'page', {}, compute)
    assert len(list(tmp_path.rglob('*.json'))) == 1


def test_corrupt_remote_falls_back_and_memory_is_bounded(state, monkeypatch, tmp_path):
    db, run = state
    store = LocalObjectStore(tmp_path)
    monkeypatch.setattr(cache, 'remote_store', lambda: store)
    key = cache.identity(db, run, 'summary', {})
    store.upload_bytes(b'{"key":"wrong","value":{"total":999},"sha256":"invalid"}',
                       f'pair-read-cache/v1/{key}.json')
    compute = Mock(return_value={'total': 12})
    assert cache.cached_read(db, run, 'summary', {}, compute)['total'] == 12
    monkeypatch.setattr(cache, 'MAX_ENTRIES', 2)
    for index in range(5):
        cache.cached_read(db, run, 'page', {'offset': index}, compute)
    assert len(cache._entries) == 2
    assert cache._bytes <= cache.MAX_BYTES


def test_api_cache_refreshes_after_accept_and_never_bypasses_login(client, workflow_run, monkeypatch):
    from masterone.mdm import workflow_pair_reads
    from tests.test_mdm.test_pair_review_redesign import seed_pairs
    from tests.test_mdm.test_workflow_contract import request
    from tests.test_mdm.test_workflow_stages import read

    monkeypatch.setattr(cache, 'remote_store', lambda: None)
    url = seed_pairs(client, workflow_run)
    original = workflow_pair_reads.pair_summary
    summary = Mock(side_effect=original)
    monkeypatch.setattr(workflow_pair_reads, 'pair_summary', summary)
    assert client.get(url + '/pairs/summary').json()['accepted'] == 0
    assert client.get(url + '/pairs/summary').json()['accepted'] == 0
    assert summary.call_count == 1
    state = read(client, workflow_run)
    accepted = client.post(url + '/pairs/pair-0000/decision', json=request(
        state['workflow_revision'], expected_pair_revision=0, decision='merge'))
    assert accepted.status_code == 200, accepted.text
    assert client.get(url + '/pairs/summary').json()['accepted'] == 1
    assert summary.call_count == 2
    assert client.post('/api/v1/auth/logout').status_code in (200, 204)
    assert client.get(url + '/pairs/summary').status_code == 401
