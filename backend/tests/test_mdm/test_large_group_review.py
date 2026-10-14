"""Large reviewed groups use exact receipts, full consistency and bounded pages."""
from contextlib import closing
from copy import deepcopy

import pytest
from masterone.mdm.engine_adapter import engine_context
from masterone.mdm.pipeline.cluster_guards import check_component
from masterone.mdm.pipeline.cluster_policy import GuardBudget, load_policy
from masterone.mdm.pipeline.decision_store import DecisionStore
from masterone.mdm.pipeline.matching import value_frequencies
from masterone.mdm.pipeline.pair_assessment import assess_pair
from masterone.mdm.workflow_contracts import GroupApprovalRequest
from masterone.mdm.workflow_finalize import cluster_snapshot, compute_publication
from masterone.mdm.workflow_group_approvals import approval_fingerprint, validate_group
from pydantic import ValidationError

from tests.test_mdm.test_pair_classification_regressions import record


@pytest.fixture(autouse=True)
def context():
    with engine_context(dict(decision_semantics_v2_enabled=True, cluster_safety_v2_enabled=True)):
        yield


def large_snapshot(size):
    records = [record(f'{i:03}', government_id='001090123456') for i in range(size)]
    for r in records:
        r['normalized_fields'].update(_government_id_verified=True, government_id_namespace='VN')
    frequencies = value_frequencies(records)
    pairs = [{**assess_pair(records[0], r, frequencies), 'id': r['id'], 'decision': 'REVIEW'} for r in records[1:]]
    return dict(records=records, pairs=pairs, overlays={p['id']: 'ACCEPTED' for p in pairs},
                review_reasons={}, negative_pairs=[], profile={}, execution_identity='code', policy_version='person-v3')


def approved(source):
    source = deepcopy(source)
    ids = [r['id'] for r in source['records']]
    source['group_approvals'] = [dict(record_ids=ids, reason='Verified all members', operation_id='approval',
                                    fingerprint=approval_fingerprint(source, ids))]
    return source


@pytest.mark.parametrize('size', [19, 20, 21, 100])
def test_reviewed_group_bounds_apply_to_full_and_partial_publication(size):
    source = approved(large_snapshot(size))
    for partial in (False, True):
        source['allow_partial'] = partial
        result = compute_publication(source, 'run', 1)
        assert result['conflicts'] == []
        assert result['publication']['covered_records'] == size
        assert result['clusters'][0]['member_record_ids'] == [f'{i:03}' for i in range(size)]


@pytest.mark.parametrize('size', [21, 100])
def test_large_group_needs_explicit_reason_and_ordinary_guard_stays_at_twenty(size):
    source = large_snapshot(size)
    ids = [r['id'] for r in source['records']]
    assert validate_group(source, ids) == 'GROUP_APPROVAL_REASON_REQUIRED'
    assert validate_group(source, ids, review_reason='Verified all members') == ''
    result = cluster_snapshot(source, 'run')
    assert result['conflicts'][0]['reason'] == 'CLUSTER_SIZE_LIMIT'
    assert result['safe_clusters'] == []
    assert result['review_groups'][0]['member_count'] == size
    with closing(DecisionStore()) as store:
        store.seal()
        assert check_component(ids, {r['id']: r for r in source['records']}, store, load_policy(),
                               GuardBudget(), value_frequencies(source['records'])) == 'CLUSTER_SIZE_LIMIT'


def test_over_bound_component_reports_total_without_an_arbitrary_split():
    source = large_snapshot(101)
    ids = [r['id'] for r in source['records']]
    assert validate_group(source, ids, review_reason='Verified all members') == 'GROUP_MEMBERS_INVALID'
    result = cluster_snapshot(source, 'run')
    assert result['safe_clusters'] == []
    draft = result['review_groups'][0]
    assert draft['member_count'] == 101
    assert len(draft['member_record_ids']) <= 100
    assert draft['approvable'] is False


@pytest.mark.parametrize('constraint', ['deny', 'veto', 'verified_id', 'stale_member', 'stale_profile'])
def test_large_exact_receipt_never_exempts_hard_constraints_or_changed_inputs(constraint):
    source = approved(large_snapshot(100))
    if constraint == 'deny':
        source['negative_pairs'] = [['000', '099']]
    elif constraint == 'veto':
        source['overlays']['099'] = 'REJECTED'
    elif constraint in ('verified_id', 'stale_member'):
        source['records'][-1]['normalized_fields']['government_id'] = '001090123457'
    else:
        source['profile'] = {'changed': True}
    assert cluster_snapshot(source, 'run')['conflicts']


def test_reviewed_hundred_checks_every_distinct_pair_once(monkeypatch):
    source = large_snapshot(100)
    calls = []
    lookup = DecisionStore.get
    def counted(self, a, b):
        calls.append((a, b))
        return lookup(self, a, b)
    monkeypatch.setattr(DecisionStore, 'get', counted)
    assert validate_group(source, [r['id'] for r in source['records']], review_reason='Verified all members') == ''
    assert len(calls) == len(set(calls)) == 4950


def test_transport_accepts_hundred_and_rejects_hundred_and_one():
    payload = dict(request_id='request', expected_workflow_revision=0, expected_decision_revision=0,
                   record_ids=[f'{i:03}' for i in range(100)], reason='Verified all members')
    assert len(GroupApprovalRequest(**payload).record_ids) == 100
    with pytest.raises(ValidationError):
        GroupApprovalRequest(**{**payload, 'record_ids': [*payload['record_ids'], '100']})


def test_preview_embeds_only_twenty_five_members_and_exposes_full_counts():
    from masterone.mdm.workflow_cluster_preview import enrich_groups, group_summary
    source = approved(large_snapshot(100))
    result = compute_publication(source, 'run', 1)
    enrich_groups(result, source)
    group = group_summary(result['clusters'][0])
    assert group['member_count'] == 100
    assert len(group['preview_members']) == 25


def test_approved_preview_retains_global_shared_contact_census():
    from masterone.mdm.workflow_cluster_preview import enrich_groups
    source = approved(large_snapshot(100))
    result = compute_publication(source, 'run', 1)
    enrich_groups(result, source)
    group = result['clusters'][0]
    frozen = result['review_group_pages'][group['fingerprint']]
    last = assess_pair(frozen['records'][-2], frozen['records'][-1], frozen['frequencies'])
    assert last['comparisons']['phone']['state'] == 'SH'


def test_sealed_review_group_pages_include_last_member_and_absent_pair_evidence(client, workflow_run, monkeypatch):
    from masterone.mdm.generation_staging import digest
    from masterone.mdm.models import ResolutionRun
    from masterone.mdm.workflow_artifacts import seal
    from masterone.mdm.workflow_cluster_preview import enrich_groups
    from masterone.mdm.workflow_models import WorkflowArtifact, WorkflowOperation

    from tests.test_mdm.test_workflow_stages import read, ready_decisions
    ready_decisions(client, workflow_run)
    state = read(client, workflow_run)
    source = large_snapshot(100)
    result = compute_publication(source, workflow_run, 1)
    enrich_groups(result, source)
    draft = result['review_groups'][0]
    # Exercise a partitioned intermediate mapping, not only a root array.
    pages = result['review_group_pages']
    result['review_group_pages'] = {**{f'dummy-{i:058}': {} for i in range(512)}, **pages}
    artifact_id = seal(client.session_factory, workflow_run, 'CLUSTER_PLAN', {'computed': result})
    with client.session_factory.begin() as db:
        op = WorkflowOperation(run_id=workflow_run, operation_type='CLUSTER_PREVIEW', status='COMPLETED',
                               request_id='large-page', payload={}, payload_hash=digest({}),
                               actor_user_id=db.get(ResolutionRun, workflow_run).owner_user_id,
                               result=dict(plan_artifact_id=artifact_id, plan_storage_hash=db.get(WorkflowArtifact, artifact_id).content_hash,
                                           source_decision_revision=state['decision_revision'], source_assessment_id=state['assessment_id']))
        db.add(op)
        db.flush()
        operation_id = op.id
    monkeypatch.setattr('masterone.mdm.workflow_finalize.compute_publication', lambda *_: pytest.fail('Read must not recluster'))
    root = f'/api/v1/mdm/runs/{workflow_run}/workflow/cluster-previews/{operation_id}/review-groups/{draft["fingerprint"]}'
    first = client.get(root + '/members').json()
    assert first['total'] == 100 and len(first['items']) == 25 and first['next_cursor'] == '25'
    last = client.get(root + '/members', params={'cursor': 75}).json()
    assert last['items'][-1]['record_id'] == '099' and last['next_cursor'] is None
    evidence = client.get(root + '/evidence', params={'cursor': 4925}).json()
    assert evidence['total'] == 4950 and len(evidence['items']) == 25
    assert evidence['items'][-1]['left_id'] == '098' and evidence['items'][-1]['right_id'] == '099'
    assert evidence['items'][-1]['computed_only'] is True
    assert client.get(root + '/members', params={'limit': 101}).status_code == 422
    assert client.get(root.replace(draft['fingerprint'], '0' * 64) + '/members').status_code == 404


def prepare_large_api_case(client, run_id, size=21):
    from masterone.mdm.models import ResolutionRecord
    from masterone.mdm.pipeline.golden_v2 import record_payload
    from masterone.mdm.workflow_models import WorkflowPair

    from tests.test_mdm.test_workflow_contract import request
    from tests.test_mdm.test_workflow_stages import read, ready_decisions, run_one
    ready_decisions(client, run_id)
    fixture = large_snapshot(size)
    with client.session_factory.begin() as db:
        rows = db.query(ResolutionRecord).filter_by(run_id=run_id).order_by(ResolutionRecord.id).all()
        base = rows[0]
        for i in range(len(rows), size):
            item = ResolutionRecord(id=f'large-{i:03}', run_id=run_id, file_id=base.file_id,
                                    external_id=f'large-{i:03}', source_system=f'S{i}', entity_type='PERSON',
                                    raw_fields={}, normalized_fields={}, quality_flags=[], extra_fields={})
            db.add(item)
            rows.append(item)
        for item in rows:
            item.entity_type = 'PERSON'
            item.raw_fields = deepcopy(fixture['records'][0]['raw_fields'])
            item.normalized_fields = deepcopy(fixture['records'][0]['normalized_fields'])
            item.quality_flags = []
        db.flush()
        records = sorted([record_payload(r) for r in rows], key=lambda r: r['id'])
        ids = [r['id'] for r in records]
        db.query(WorkflowPair).filter_by(run_id=run_id).delete()
        pairs = [{**assess_pair(records[0], r, value_frequencies(records)), 'id': f'large-p{i}'}
                 for i, r in enumerate(records[1:])]
        pairs.append({**assess_pair(records[-2], records[-1], value_frequencies(records)), 'id': 'hidden-pending'})
        for pair in pairs:
            db.add(WorkflowPair(id=pair['id'], run_id=run_id, baseline=pair,
                                decision='UNDECIDED' if pair['id'] == 'hidden-pending' else 'ACCEPTED'))
    state = read(client, run_id)
    root = f'/api/v1/mdm/runs/{run_id}/workflow'
    response = client.post(root + '/reassess', json=request(state['workflow_revision']))
    assert response.status_code == 202, response.text
    run_one(client)
    state = read(client, run_id)
    response = client.post(root + '/cluster-preview', json=request(state['workflow_revision'],
        expected_decision_revision=state['decision_revision'], allow_partial=False))
    assert response.status_code == 202, response.text
    run_one(client)
    preview = client.get(root + '/operations/' + response.json()['operation_id']).json()
    assert preview['status'] == 'COMPLETED', preview
    return root, ids, preview


def test_large_api_receipt_accepts_hidden_pending_pair_and_matches_publication(client, workflow_run):
    from masterone.mdm.workflow_models import WorkflowPair

    from tests.test_mdm.test_workflow_contract import request
    from tests.test_mdm.test_workflow_stages import read, run_one
    root, ids, preview = prepare_large_api_case(client, workflow_run)
    draft = preview['result']['conflicts'][0]
    assert draft['member_count'] == 21 and draft['approvable']
    state = read(client, workflow_run)
    payload = request(state['workflow_revision'], expected_decision_revision=state['decision_revision'],
        expected_assessment_id=state['assessment_id'], record_ids=ids, reason='Verified all members',
        fingerprint=draft['fingerprint'], preview_operation_id=preview['operation_id'])
    response = client.post(root + '/group-approvals', json=payload)
    assert response.status_code == 200, response.text
    assert client.post(root + '/group-approvals', json=payload).json() == response.json()
    with client.session_factory() as db:
        assert db.get(WorkflowPair, 'hidden-pending').decision == 'ACCEPTED'
        assert db.get(WorkflowPair, 'hidden-pending').review_reason == 'Verified all members'
    state = read(client, workflow_run)
    response = client.post(root + '/finalize-decisions', json=request(state['workflow_revision'],
        expected_decision_revision=state['decision_revision']))
    assert response.status_code == 202, response.text
    run_one(client)
    clusters = client.get(root + '/clusters').json()['items']
    assert any(c['member_record_ids'] == ids for c in clusters)


def test_large_api_blocks_changed_members_outside_first_page(client, workflow_run):
    from masterone.mdm.models import ResolutionRecord

    from tests.test_mdm.test_workflow_contract import request
    from tests.test_mdm.test_workflow_stages import read
    root, ids, preview = prepare_large_api_case(client, workflow_run, 30)
    draft = preview['result']['conflicts'][0]
    with client.session_factory.begin() as db:
        record_row = db.get(ResolutionRecord, ids[-1])
        record_row.normalized_fields = {**record_row.normalized_fields, 'government_id': '001090123457'}
    state = read(client, workflow_run)
    response = client.post(root + '/group-approvals', json=request(state['workflow_revision'],
        expected_decision_revision=state['decision_revision'], record_ids=ids, reason='Verified all members',
        fingerprint=draft['fingerprint'], preview_operation_id=preview['operation_id']))
    assert response.status_code == 409, response.text
    assert response.json()['detail']['code'] == 'GROUP_REVIEW_CHANGED'


def test_large_approval_fingerprint_must_belong_to_the_named_preview(client, workflow_run):
    from masterone.mdm.generation_staging import digest
    from masterone.mdm.models import ResolutionRun
    from masterone.mdm.workflow_artifacts import seal
    from masterone.mdm.workflow_models import WorkflowArtifact, WorkflowOperation

    from tests.test_mdm.test_workflow_contract import request
    from tests.test_mdm.test_workflow_stages import read
    root, ids, preview = prepare_large_api_case(client, workflow_run)
    artifact_id = seal(client.session_factory, workflow_run, 'CLUSTER_PLAN', {'computed': {'review_group_pages': {}}})
    with client.session_factory.begin() as db:
        fake = WorkflowOperation(run_id=workflow_run, operation_type='CLUSTER_PREVIEW', status='COMPLETED',
                                 request_id='unrelated-preview', payload={}, payload_hash=digest({}),
                                 actor_user_id=db.get(ResolutionRun, workflow_run).owner_user_id,
                                 result={**preview['result'], 'plan_artifact_id': artifact_id,
                                         'plan_storage_hash': db.get(WorkflowArtifact, artifact_id).content_hash})
        db.add(fake)
        db.flush()
        operation_id = fake.id
    state = read(client, workflow_run)
    response = client.post(root + '/group-approvals', json=request(state['workflow_revision'],
        expected_decision_revision=state['decision_revision'], record_ids=ids, reason='Verified all members',
        fingerprint=preview['result']['conflicts'][0]['fingerprint'], preview_operation_id=operation_id))
    assert response.status_code == 409, response.text
    assert response.json()['detail']['code'] == 'GROUP_REVIEW_CHANGED'
