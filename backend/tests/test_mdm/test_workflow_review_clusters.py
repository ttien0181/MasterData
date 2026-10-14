"""Cluster tasks must be complete, bounded, and fenced by existing batch review."""
import pytest
from masterone.config import get_settings
from masterone.mdm.workflow_models import WorkflowPair

from tests.test_mdm.test_workflow_stages import read, ready_decisions, run_one


@pytest.fixture
def cluster_run(client, monkeypatch):
    monkeypatch.setattr(get_settings(), 'session_workflow_enabled', True)
    monkeypatch.setattr(get_settings(), 'pair_assessment_auto_enabled', True)
    csv = 'source_record_id,source_system,entity_type,name,phone,email,address\n'
    csv += ''.join(f'{i},{source},PERSON,Nguyen Van An,0901234567,an@example.test,123 Hanoi\n'
                   for i, source in enumerate(('CRM', 'BILL', 'ERP', 'CSV')))
    response = client.post('/api/v1/mdm/runs', data={'workflow_version': '2'},
                          files={'files': ('cluster.csv', csv.encode(), 'text/csv')})
    assert response.status_code == 201, response.text
    rid = response.json()['run_id']
    ready_decisions(client, rid)
    return rid


def seed(client, run):
    return client.get(f'/api/v1/mdm/runs/{run}/workflow/pairs').json()['items'][0]['pair_id']


def task(client, run, pair_id=None):
    return client.get(f'/api/v1/mdm/runs/{run}/workflow/pairs/{pair_id or seed(client, run)}/review-cluster')


def test_complete_cluster_reads_each_record_once_and_exact_pending_ids(client, cluster_run):
    response = task(client, cluster_run)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['complete'] and data['batch_allowed']
    assert len(data['records']) == 4 and len(data['pairs']) == 6
    assert len(data['pending_pair_ids']) == 6
    assert len({r['record_id'] for r in data['records']}) == 4
    assert data['decision_revision'] == read(client, cluster_run)['decision_revision']
    assert not data['blocked_reasons']
    # No pair decision or operation is created by reading a cluster task.
    assert all(p['decision'] == 'UNDECIDED' for p in data['pairs'])


@pytest.mark.parametrize('case', ['insufficient', 'unknown', 'blocked', 'no_merge', 'deny'])
def test_internal_weak_unknown_or_veto_edge_disables_batch(client, cluster_run, case):
    with client.session_factory.begin() as db:
        pair = db.query(WorkflowPair).filter_by(run_id=cluster_run).order_by(WorkflowPair.id).first()
        if case in ('no_merge', 'deny'):
            pair.decision = 'REJECTED' if case == 'no_merge' else 'NON_MATCH'
        else:
            conclusion = {'insufficient': 'INSUFFICIENT_EVIDENCE', 'unknown': 'UNKNOWN_HISTORY', 'blocked': 'NOT_MERGEABLE'}[case]
            # Effective assessment overrides a stronger original baseline.
            pair.assessment = {**pair.baseline, 'decision': conclusion}
    data = task(client, cluster_run).json()
    assert len(data['records']) == 4
    assert data['complete'] and not data['batch_allowed']
    assert data['blocked_reasons']


def test_weak_bridges_do_not_add_unrelated_member(client, cluster_run):
    with client.session_factory.begin() as db:
        rows = db.query(WorkflowPair).filter_by(run_id=cluster_run).order_by(WorkflowPair.id).all()
        isolated = max({p.baseline[k] for p in rows for k in ('left_id', 'right_id')})
        chosen = next(p.id for p in rows if isolated not in (p.baseline['left_id'], p.baseline['right_id']))
        for p in rows:
            if isolated in (p.baseline['left_id'], p.baseline['right_id']):
                p.assessment = {**p.baseline, 'decision': 'INSUFFICIENT_EVIDENCE'}
    data = task(client, cluster_run, chosen).json()
    assert len(data['records']) == 3 and len(data['pairs']) == 3
    assert isolated not in {r['record_id'] for r in data['records']}
    assert data['batch_allowed']


def test_missing_direct_edge_is_evaluated_without_inventing_a_decision(client, cluster_run):
    with client.session_factory.begin() as db:
        pair = db.query(WorkflowPair).filter_by(run_id=cluster_run).first()
        db.delete(pair)
    data = task(client, cluster_run).json()
    assert data['complete'] and data['batch_allowed']
    assert len(data['additional_evidence']) == 1
    assert len(data['pending_pair_ids']) == 5
    with client.session_factory() as db:
        assert db.query(WorkflowPair).filter_by(run_id=cluster_run).count() == 5


def test_missing_direct_edge_with_verified_conflict_blocks_group_review(client, cluster_run):
    from masterone.mdm.models import ResolutionRecord
    with client.session_factory.begin() as db:
        pair = db.query(WorkflowPair).filter_by(run_id=cluster_run).first()
        for index, rid in enumerate((pair.baseline['left_id'], pair.baseline['right_id'])):
            row = db.get(ResolutionRecord, rid)
            row.raw_fields = {**row.raw_fields, 'government_id_verified': 'true', 'government_id_namespace': 'VN'}
            row.normalized_fields = {**row.normalized_fields, 'government_id': f'00109012345{index}',
                'government_id_type': 'CCCD_12', 'government_id_namespace': 'VN', '_government_id_verified': True,
                '_field_quality': {**row.normalized_fields.get('_field_quality', {}), 'government_id': 'VALID'}}
        db.delete(pair)
    data = task(client, cluster_run).json()
    assert not data['batch_allowed']
    assert 'CLUSTER_REVIEW_MISSING_PAIR' in data['blocked_reasons']


def test_cluster_batch_reuses_exact_preview_and_keeps_existing_decision(client, cluster_run):
    with client.session_factory.begin() as db:
        p = db.query(WorkflowPair).filter_by(run_id=cluster_run).first()
        p.decision = 'ACCEPTED'
    data = task(client, cluster_run).json()
    assert len(data['pending_pair_ids']) == 5 and data['batch_allowed']
    state = read(client, cluster_run)
    body = dict(request_id='cluster-batch', expected_workflow_revision=state['workflow_revision'],
                expected_assessment_id=state['assessment_id'], expected_decision_revision=state['decision_revision'],
                decision='merge', scope={'pair_ids': data['pending_pair_ids']}, auto_only=False)
    root = f'/api/v1/mdm/runs/{cluster_run}/workflow'
    preview = client.post(root + '/bulk-decisions/preview', json=body)
    assert preview.status_code == 200, preview.text
    body['preview_token'] = preview.json()['preview_token']
    response = client.post(root + '/bulk-decisions', json=body)
    assert response.status_code == 202, response.text
    run_one(client)
    assert client.get(root + '/pairs/summary').json()['accepted'] == 6
    assert not client.get(root + '/pairs?resolution=REQUIRED').json()['total']


def test_cross_run_pair_id_is_not_hydrated(client, cluster_run):
    assert task(client, cluster_run, 'unrelated-pair').status_code == 404


def test_negative_link_and_soft_reason_remain_authoritative(client, cluster_run):
    from masterone.mdm.models import ResolutionNegativeEdge
    with client.session_factory.begin() as db:
        p = db.query(WorkflowPair).filter_by(run_id=cluster_run).first()
        p.assessment = {**p.baseline, 'primary_group': 'D'}
        pid, a, b = p.id, p.baseline['left_id'], p.baseline['right_id']
    data = task(client, cluster_run, pid).json()
    assert data['batch_allowed']
    assert next(p for p in data['pairs'] if p['pair_id'] == pid)['eligibility']['reason_required']
    with client.session_factory.begin() as db:
        db.add(ResolutionNegativeEdge(id='negative-task', run_id=cluster_run,
            left_record_id=a, right_record_id=b, created_by='test', reason='STEWARD_REJECT', active=True))
    data = task(client, cluster_run, pid).json()
    assert not data['batch_allowed'] and 'CLUSTER_REVIEW_VETO' in data['blocked_reasons']


def test_explicit_defer_is_included_in_exact_cluster_scope(client, cluster_run):
    with client.session_factory.begin() as db:
        p = db.query(WorkflowPair).filter_by(run_id=cluster_run).first()
        p.decision = 'DEFERRED'
        pid = p.id
    data = task(client, cluster_run, pid).json()
    assert pid in data['pending_pair_ids']
    assert data['batch_allowed']
    assert next(p for p in data['pairs'] if p['pair_id'] == pid)['decision'] == 'DEFERRED'


def test_large_component_returns_bounded_fallback(client, cluster_run):
    from itertools import combinations

    from masterone.mdm.models import ResolutionRecord
    with client.session_factory.begin() as db:
        template = db.query(WorkflowPair).filter_by(run_id=cluster_run).first().baseline
        file_id = db.query(ResolutionRecord).filter_by(run_id=cluster_run).first().file_id
        db.query(WorkflowPair).filter_by(run_id=cluster_run).delete()
        ids = [f'large-{i:02}' for i in range(21)]
        for rid in ids:
            db.add(ResolutionRecord(id=rid, run_id=cluster_run, file_id=file_id, external_id=rid, source_system='CRM',
                entity_type='PERSON', raw_fields={'name': rid}, normalized_fields={}, quality_flags=[]))
        for i, (a, b) in enumerate(combinations(ids, 2)):
            db.add(WorkflowPair(id=f'large-pair-{i:03}', run_id=cluster_run,
                baseline={**template, 'id': f'large-pair-{i:03}', 'left_id': a, 'right_id': b,
                          'decision': 'REVIEW', 'triage_status': 'REVIEW'}))
    data = task(client, cluster_run, 'large-pair-000').json()
    assert not data['complete'] and not data['batch_allowed']
    assert 'CLUSTER_REVIEW_TOO_LARGE' in data['blocked_reasons']
    assert len(data['records']) <= 20 and len(data['pairs']) <= 200


def soft_bulk(client, run):
    with client.session_factory.begin() as db:
        pair = db.query(WorkflowPair).filter_by(run_id=run).first()
        pair.assessment = {**pair.baseline, 'primary_group': 'D'}
    group = task(client, run).json()
    state = read(client, run)
    body = dict(request_id='soft-batch', expected_workflow_revision=state['workflow_revision'],
                expected_assessment_id=state['assessment_id'], expected_decision_revision=state['decision_revision'],
                decision='merge', scope={'pair_ids': group['pending_pair_ids']}, reason='Documents compared')
    return group, body, f'/api/v1/mdm/runs/{run}/workflow'


def test_soft_bulk_requires_one_reviewed_group_even_with_reason(client, cluster_run):
    _, body, root = soft_bulk(client, cluster_run)
    preview = client.post(root + '/bulk-decisions/preview', json=body)
    assert preview.json()['group_review_required']
    response = client.post(root + '/bulk-decisions', json=body)
    assert response.status_code == 409
    assert response.json()['detail']['code'] == 'SOFT_BULK_GROUP_REVIEW_REQUIRED'


def test_soft_bulk_uses_current_bounded_group_and_replays_receipt(client, cluster_run):
    group, body, root = soft_bulk(client, cluster_run)
    body.update(review_group_record_ids=[r['record_id'] for r in group['records']],
                review_group_token=group['review_scope_token'])
    preview = client.post(root + '/bulk-decisions/preview', json=body)
    assert preview.status_code == 200, preview.text
    assert not preview.json()['group_review_required']
    body['preview_token'] = preview.json()['preview_token']
    response = client.post(root + '/bulk-decisions', json=body)
    assert response.status_code == 202, response.text
    run_one(client)
    assert client.get(root + '/pairs/summary').json()['accepted'] == 6
    replay = client.post(root + '/bulk-decisions', json=body)
    assert replay.status_code == 202
    assert replay.json()['operation_id'] == response.json()['operation_id']


@pytest.mark.parametrize('change', ['record', 'scope', 'members'])
def test_soft_bulk_rejects_changed_evidence_or_scope(client, cluster_run, change):
    from masterone.mdm.models import ResolutionRecord
    group, body, root = soft_bulk(client, cluster_run)
    body.update(review_group_record_ids=[r['record_id'] for r in group['records']],
                review_group_token=group['review_scope_token'])
    if change == 'record':
        with client.session_factory.begin() as db:
            row = db.get(ResolutionRecord, body['review_group_record_ids'][0])
            row.raw_fields = {**row.raw_fields, 'name': 'Different name'}
    elif change == 'scope':
        body['scope'] = {'filter': {'group': 'D'}}
    else:
        body['review_group_record_ids'] = body['review_group_record_ids'][:-1]
    response = client.post(root + '/bulk-decisions', json=body)
    assert response.status_code == 409, response.text
