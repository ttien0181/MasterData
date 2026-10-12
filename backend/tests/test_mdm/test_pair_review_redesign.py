from copy import deepcopy

import pytest
from masterone.mdm.models import ResolutionNegativeEdge, ResolutionRecord, ResolutionTask
from masterone.mdm.workflow_models import WorkflowPair

from tests.test_mdm.test_workflow_contract import request
from tests.test_mdm.test_workflow_stages import read, ready_decisions, run_one


def seed_pairs(client, run_id, count=13):
    ready_decisions(client, run_id)
    with client.session_factory.begin() as db:
        records = db.query(ResolutionRecord).filter_by(run_id=run_id).order_by(ResolutionRecord.id).all()
        base = deepcopy(db.query(WorkflowPair).filter_by(run_id=run_id).first().baseline)
        db.query(WorkflowPair).filter_by(run_id=run_id).delete()
        for index in range(count):
            identifier = f'pair-{index:04}'
            baseline = {**base, 'id': identifier, 'left_id': records[0].id, 'right_id': records[1].id,
                        'decision': 'REVIEW', 'primary_group': 'A', 'subgroup': 'A1_NAME_PHONE'}
            if index == count - 1:
                baseline.update(decision='NOT_MERGEABLE', primary_group=None, subgroup=None, reason='SAME_TYPE_GOV_ID_CONFLICT')
            db.add(WorkflowPair(id=identifier, run_id=run_id, baseline=baseline))
    return f'/api/v1/mdm/runs/{run_id}/workflow'


def test_pair_page_has_ten_rows_and_source_records(client, workflow_run):
    url = seed_pairs(client, workflow_run)
    first = client.get(url + '/pairs').json()
    assert len(first['items']) == 10
    assert first['total'] == 13
    assert first['items'][0]['left_record']['source_system']
    assert first['items'][0]['left_record']['raw_fields']['name']
    second = client.get(url + '/pairs?offset=10&limit=10').json()
    assert len(second['items']) == 3
    assert second['offset'] == 10
    assert not {p['pair_id'] for p in first['items']} & {p['pair_id'] for p in second['items']}
    assert client.get(url + '/pairs?group=UNCLASSIFIED').json()['total'] == 1


def test_bulk_review_advertises_queue_and_allows_read_only_preview(client, workflow_run):
    url = seed_pairs(client, workflow_run)
    state = read(client, workflow_run)
    body = request(state['workflow_revision'], expected_decision_revision=state['decision_revision'],
                   decision='merge', scope={'filter': {'group': 'A'}})
    assert client.post(url + '/bulk-decisions', json=body).status_code == 202
    active = read(client, workflow_run)
    assert 'queue-decisions' in active['allowed_actions']
    assert 'decide-pair' not in active['allowed_actions']
    # Preview is an observation; a queued scope can be inspected while earlier work progresses.
    preview = client.post(url + '/bulk-decisions/preview', json=body)
    assert preview.status_code == 200, preview.text
    assert preview.json()['applicable'] == 12
    assert read(client, workflow_run)['decision_revision'] == state['decision_revision']


def test_pair_summary_and_preview_do_not_write_decisions(client, workflow_run):
    url = seed_pairs(client, workflow_run)
    before = read(client, workflow_run)
    summary_response = client.get(url + '/pairs/summary')
    assert summary_response.status_code == 200
    summary = summary_response.json()
    assert summary['total'] == 13
    assert summary['needs_review'] == 12
    assert summary['blocked'] == 1
    assert summary['manual_available'] == 12
    body = request(before['workflow_revision'], expected_decision_revision=before['decision_revision'], decision='merge', scope={'filter': {}})
    preview_response = client.post(url + '/bulk-decisions/preview', json=body)
    assert preview_response.status_code == 200
    assert preview_response.json()['applicable'] == 12
    assert preview_response.json()['blocked'] == 1
    assert read(client, workflow_run)['decision_revision'] == before['decision_revision']


def test_summary_counts_review_veto_as_blocked_accept(client, workflow_run):
    url = seed_pairs(client, workflow_run)
    with client.session_factory.begin() as db:
        db.get(WorkflowPair, 'pair-0000').decision = 'REJECTED'
    summary = client.get(url + '/pairs/summary').json()
    assert summary['kept_separate'] == 1
    assert summary['blocked'] == 2
    assert summary['manual_available'] == 11


def test_bulk_accept_uses_manual_permission_and_preserves_resolved_pairs(client, workflow_run):
    url = seed_pairs(client, workflow_run)
    with client.session_factory.begin() as db:
        db.get(WorkflowPair, 'pair-0000').decision = 'REJECTED'
    state = read(client, workflow_run)
    response = client.post(url + '/bulk-decisions', json=request(state['workflow_revision'],
        expected_decision_revision=state['decision_revision'], decision='merge', scope={'filter': {'group': 'A'}}))
    assert response.status_code == 202, response.text
    run_one(client)
    with client.session_factory() as db:
        assert db.get(WorkflowPair, 'pair-0000').decision == 'REJECTED'
        assert db.get(WorkflowPair, 'pair-0001').decision == 'ACCEPTED'
        assert db.get(WorkflowPair, 'pair-0012').decision == 'UNDECIDED'
    progress = client.get(url + '/operations/' + response.json()['operation_id']).json()['progress']
    assert progress['applied'] == 11
    assert progress['skipped'] == 1


def test_explicit_deny_creates_cannot_link_and_replay_keeps_one_edge(client, workflow_run):
    url = seed_pairs(client, workflow_run)
    pair = client.get(url + '/pairs').json()['items'][0]
    state = read(client, workflow_run)
    body = request(state['workflow_revision'], expected_pair_revision=pair['revision'], decision='deny')
    response = client.post(url + f"/pairs/{pair['pair_id']}/decision", json=body)
    assert response.status_code == 200, response.text
    assert response.json()['decision'] == 'NON_MATCH'
    assert client.post(url + f"/pairs/{pair['pair_id']}/decision", json=body).json() == response.json()
    with client.session_factory() as db:
        assert db.query(ResolutionNegativeEdge).filter_by(run_id=workflow_run, active=True).count() == 1
    state = read(client, workflow_run)
    other = client.get(url + '/pairs').json()['items'][1]
    assert client.post(url + f"/pairs/{other['pair_id']}/decision", json=request(state['workflow_revision'],
        expected_pair_revision=other['revision'], decision='merge')).status_code == 409


def test_accepted_insufficient_pair_is_checked_instead_of_silently_dropped():
    from masterone.mdm.engine_adapter import engine_context
    from masterone.mdm.workflow_finalize import cluster_snapshot

    from tests.test_mdm.test_safe_clustering import chain, edge
    a, b, _ = chain()
    pair = edge(a, b, 15)
    pair['decision'] = 'INSUFFICIENT_EVIDENCE'
    snapshot = dict(records=[a, b], pairs=[pair], overlays={pair['id']: 'ACCEPTED'},
                    negative_pairs=[], policy_version='person-v3')
    with engine_context(dict(decision_semantics_v2_enabled=True, cluster_safety_v2_enabled=True, generation_staging_enabled=False)):
        result = cluster_snapshot(snapshot, 'r')
    assert result['clusters'] or result['conflicts']


def test_cannot_link_blocks_indirect_accepted_path_at_finalize():
    from masterone.mdm.engine_adapter import engine_context
    from masterone.mdm.workflow_finalize import cluster_snapshot
    from tests.test_mdm.test_decision_semantics import record
    from tests.test_mdm.test_safe_clustering import edge

    a, b, c = [record(key, phone='0912345678', email='qa@example.test') for key in ('a', 'b', 'c')]
    pairs = [edge(a, b, 15), edge(b, c, 15)]
    snapshot = dict(records=[a, b, c], pairs=pairs, overlays={p['id']: 'ACCEPTED' for p in pairs},
                    negative_pairs=[], policy_version='person-v3')
    with engine_context(dict(decision_semantics_v2_enabled=True, cluster_safety_v2_enabled=True, generation_staging_enabled=False)):
        allowed = cluster_snapshot(snapshot, 'r')
        denied = cluster_snapshot({**snapshot, 'negative_pairs': [('a', 'c')]}, 'r')
    assert any(set(cluster['member_record_ids']) == {'a', 'b', 'c'} for cluster in allowed['clusters'])
    assert not any({'a', 'c'} <= set(cluster['member_record_ids']) for cluster in denied['clusters'])
    assert denied['conflicts']


def test_bulk_deny_selection_excludes_unselected_and_preserves_reviewed(client, workflow_run):
    url = seed_pairs(client, workflow_run)
    with client.session_factory.begin() as db:
        db.get(WorkflowPair, 'pair-0000').decision = 'ACCEPTED'
    state = read(client, workflow_run)
    response = client.post(url + '/bulk-decisions', json=request(state['workflow_revision'],
        expected_decision_revision=state['decision_revision'], decision='deny',
        scope={'pair_ids': ['pair-0000', 'pair-0002']}))
    assert response.status_code == 202
    run_one(client)
    with client.session_factory() as db:
        assert db.get(WorkflowPair, 'pair-0000').decision == 'ACCEPTED'
        assert db.get(WorkflowPair, 'pair-0001').decision == 'UNDECIDED'
        assert db.get(WorkflowPair, 'pair-0002').decision == 'NON_MATCH'
        assert db.query(ResolutionNegativeEdge).filter_by(run_id=workflow_run, active=True).count() == 1


def test_bulk_resume_keeps_committed_decisions_and_counts_without_reapplying(client, workflow_run):
    from masterone.mdm.job_queue import claim_next, failed, ownership
    from masterone.mdm.job_queue import progress as record_progress
    from masterone.mdm.workflow_jobs import execute_workflow
    url = seed_pairs(client, workflow_run, 205)
    state = read(client, workflow_run)
    response = client.post(url + '/bulk-decisions', json=request(state['workflow_revision'],
        expected_decision_revision=state['decision_revision'], decision='deny', scope={'filter': {}}))
    assert response.status_code == 202
    claim = claim_next(client.session_factory, 'interrupted', lease_seconds=300)
    with client.session_factory.begin() as db:
        db.get(ResolutionTask, claim.task_id).max_attempts = 1
    def stop(phase, processed, total):
        record_progress(client.session_factory, claim, phase, processed, total)
        raise RuntimeError('Interrupted after a durable batch')
    with ownership(claim), pytest.raises(RuntimeError):
        execute_workflow(response.json()['operation_id'], client.session_factory, stop)
    with client.session_factory() as db:
        assert db.query(WorkflowPair).filter_by(run_id=workflow_run, decision='NON_MATCH').count() == 100
    failed(client.session_factory, claim)
    state = read(client, workflow_run)
    assert client.post(url + f"/operations/{response.json()['operation_id']}/retry", json=request(state['workflow_revision'])).status_code == 202
    run_one(client)
    with client.session_factory() as db:
        assert db.query(WorkflowPair).filter_by(run_id=workflow_run, decision='NON_MATCH').count() == 205
        assert all(p.revision == 1 for p in db.query(WorkflowPair).filter_by(run_id=workflow_run))
        assert db.query(ResolutionNegativeEdge).filter_by(run_id=workflow_run, active=True).count() == 1
    operation = client.get(url + '/operations/' + response.json()['operation_id']).json()
    assert operation['progress']['applied'] == 205
    assert operation['progress']['processed'] == 205
