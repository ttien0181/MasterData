from copy import deepcopy

import pytest
from masterone.mdm.engine_adapter import engine_context
from masterone.mdm.pipeline.pair_assessment import assess_pair
from masterone.mdm.workflow_finalize import cluster_snapshot
from masterone.mdm.workflow_models import WorkflowPair

from tests.test_mdm.test_pair_classification_regressions import record
from tests.test_mdm.test_workflow_contract import request
from tests.test_mdm.test_workflow_stages import read, ready_decisions


@pytest.fixture
def person_engine():
    with engine_context(dict(decision_semantics_v2_enabled=True, cluster_safety_v2_enabled=True,
                             generation_staging_enabled=False)):
        yield


def soft_snapshot():
    records = [record('a', government_id='001090123456'), record('b', government_id='001090123457')]
    pair = {**assess_pair(*records), 'id': 'p'}
    return dict(records=records, pairs=[pair], overlays={'p': 'ACCEPTED'}, negative_pairs=[],
                review_reasons={'p': 'Đã đối chiếu giấy tờ gốc; lỗi nhập liệu ở nguồn ERP'}, policy_version='person-v3')


def test_reasoned_soft_accept_can_form_group(person_engine):
    source = soft_snapshot()
    before = deepcopy(source)
    result = cluster_snapshot(source, 'r')
    assert result['conflicts'] == []
    assert [c['member_record_ids'] for c in result['safe_clusters']] == [['a', 'b']]
    assert source == before


def test_historical_soft_accept_without_reason_requires_review(person_engine):
    source = soft_snapshot()
    source['review_reasons'] = {}
    result = cluster_snapshot(source, 'r')
    assert result['safe_clusters'] == []
    assert result['conflicts'][0]['reason'] == 'SOFT_ACCEPT_REASON_REQUIRED'


@pytest.mark.parametrize('constraint', ['deny', 'verified_id'])
def test_soft_accept_reason_never_overrides_hard_constraints(person_engine, constraint):
    source = soft_snapshot()
    if constraint == 'deny':
        source['negative_pairs'] = [['a', 'b']]
    else:
        for item in source['records']:
            item['normalized_fields']['_government_id_verified'] = True
            item['normalized_fields']['government_id_namespace'] = 'VN'
    assert cluster_snapshot(source, 'r')['conflicts']


def test_soft_accept_api_requires_reason_and_audits_it(client, workflow_run):
    ready_decisions(client, workflow_run)
    url = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    with client.session_factory.begin() as db:
        pair = db.query(WorkflowPair).filter_by(run_id=workflow_run).first()
        pair.baseline = {**pair.baseline, 'decision': 'REVIEW', 'primary_group': 'D',
                         'reason': 'UNVERIFIED_ID_CONFLICT'}
        pair_id, revision = pair.id, pair.revision
    state = read(client, workflow_run)
    body = request(state['workflow_revision'], expected_pair_revision=revision, decision='merge')
    denied = client.post(url + f'/pairs/{pair_id}/decision', json=body)
    assert denied.status_code == 409
    assert denied.json()['detail']['code'] == 'SOFT_ACCEPT_REASON_REQUIRED'
    reason = 'Đã đối chiếu bản gốc; xác nhận lỗi nhập liệu'
    accepted = client.post(url + f'/pairs/{pair_id}/decision', json={**body, 'reason': reason})
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()['review_reason'] == reason
    from masterone.mdm.models import ResolutionAuditEvent
    with client.session_factory() as db:
        event = db.query(ResolutionAuditEvent).filter_by(run_id=workflow_run, action='WORKFLOW_PAIR_ACCEPTED').one()
        assert event.details['reason'] == reason


def test_exact_group_approval_permits_bridge_but_not_expanded_group(person_engine):
    from masterone.mdm.workflow_group_approvals import approval_fingerprint, validate_group

    from tests.test_mdm.test_safe_clustering import chain, edge
    records = chain()
    pairs = [edge(records[0], records[1], 15), edge(records[1], records[2], 14)]
    for pair in pairs:
        pair['decision'] = 'REVIEW'
    source = dict(records=records, pairs=pairs, overlays={p['id']: 'ACCEPTED' for p in pairs},
                  negative_pairs=[], policy_version='person-v3', profile={}, execution_identity='config')
    ids = ['a', 'b', 'c']
    assert cluster_snapshot(source, 'r')['conflicts'][0]['reason'] == 'CLUSTER_BRIDGE_MISSING_EVIDENCE'
    assert validate_group(source, ids) == ''
    approval = dict(record_ids=ids, reason='Đã đối chiếu lịch sử liên hệ',
                    fingerprint=approval_fingerprint(source, ids), operation_id='approval')
    source['group_approvals'] = [approval]
    result = cluster_snapshot(source, 'r')
    assert result['conflicts'] == []
    assert [c['member_record_ids'] for c in result['safe_clusters']] == [ids]
    changed = deepcopy(source)
    changed['overlays'][pairs[0]['id']] = 'NON_MATCH'
    assert ['a', 'b', 'c'] not in [c['member_record_ids'] for c in cluster_snapshot(changed, 'r')['safe_clusters']]
    changed = deepcopy(source)
    changed['records'][0]['normalized_fields']['phone'] = '0900000000'
    assert cluster_snapshot(changed, 'r')['conflicts']


@pytest.mark.parametrize('constraint', ['deny', 'veto', 'unaccepted', 'verified_id'])
def test_group_approval_cannot_bypass_constraints(person_engine, constraint):
    from masterone.mdm.workflow_group_approvals import validate_group
    source = soft_snapshot()
    if constraint == 'deny':
        source['negative_pairs'] = [['a', 'b']]
    elif constraint == 'veto':
        source['overlays']['p'] = 'REJECTED'
    elif constraint == 'unaccepted':
        source['overlays']['p'] = 'UNDECIDED'
    else:
        for item in source['records']:
            item['normalized_fields'].update(_government_id_verified=True, government_id_namespace='VN')
    assert validate_group(source, ['a', 'b'])


def test_manual_cluster_accepts_null_weights_with_deterministic_order(person_engine):
    a, b = record('a'), record('b')
    pair = {**assess_pair(a, b), 'id': 'p', 'match_weight': None}
    source = dict(records=[a, b], pairs=[pair], overlays={'p': 'ACCEPTED'}, negative_pairs=[], policy_version='person-v3')
    assert [c['member_record_ids'] for c in cluster_snapshot(source, 'r')['safe_clusters']] == [['a', 'b']]


def test_manual_star_checks_each_identity_pair_at_most_twice(person_engine, monkeypatch):
    from masterone.mdm.pipeline import matching
    records = [record(f'{i:02}', government_id='001090123456') for i in range(20)]
    for item in records:
        item['normalized_fields'].update(_government_id_verified=True, government_id_namespace='VN')
    pairs = [{**assess_pair(records[0], right), 'id': right['id'], 'decision': 'REVIEW'} for right in records[1:]]
    source = dict(records=records, pairs=pairs, overlays={p['id']: 'ACCEPTED' for p in pairs},
                  negative_pairs=[], policy_version='person-v3')
    calls = []
    compare = matching.compare_and_score_rule

    def counted(*args, **kwargs):
        calls.append((args[0]['id'], args[1]['id']))
        return compare(*args, **kwargs)

    monkeypatch.setattr(matching, 'compare_and_score_rule', counted)
    result = cluster_snapshot(source, 'r')
    assert [c['member_record_ids'] for c in result['safe_clusters']] == [[f'{i:02}' for i in range(20)]]
    assert len(calls) <= 380  # 190 distinct pairs: eligibility and consistency, once each.


@pytest.mark.parametrize('legacy', [False, True])
def test_historical_soft_reason_required_with_both_cluster_engines(legacy):
    with engine_context(dict(decision_semantics_v2_enabled=True, cluster_safety_v2_enabled=not legacy)):
        source = soft_snapshot()
        source['review_reasons'] = {}
        result = cluster_snapshot(source, 'r')
        assert result['conflicts'][0]['reason'] == 'SOFT_ACCEPT_REASON_REQUIRED'


def test_group_approval_api_accepts_internal_pending_pairs_with_audit(client, workflow_run):
    from masterone.mdm.models import ResolutionAuditEvent, ResolutionRecord
    from masterone.mdm.pipeline.golden_v2 import record_payload
    from masterone.mdm.workflow_models import WorkflowArtifact

    ready_decisions(client, workflow_run)
    url = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    state = read(client, workflow_run)
    assert client.post(url + '/reassess', json=request(state['workflow_revision'])).status_code == 202
    from tests.test_mdm.test_workflow_stages import run_one
    run_one(client)
    with client.session_factory.begin() as db:
        raw = sorted(db.query(ResolutionRecord).filter_by(run_id=workflow_run).all(), key=lambda r: r.id)
        ids = [r.id for r in raw]
        # Three equivalent observations; one candidate remains undecided.
        for item in raw:
            item.normalized_fields = deepcopy(raw[0].normalized_fields)
            item.entity_type = raw[0].entity_type
        records = [record_payload(r) for r in raw]
        db.query(WorkflowPair).filter_by(run_id=workflow_run).delete()
        for i, (a, b) in enumerate(((0, 1), (1, 2), (0, 2))):
            pair = {**assess_pair(records[a], records[b]), 'id': f'group-p{i}', 'decision': 'REVIEW'}
            db.add(WorkflowPair(id=pair['id'], run_id=workflow_run, baseline=pair,
                               decision='UNDECIDED' if i == 2 else 'ACCEPTED'))
        # Refresh the assessment snapshot after controlled test input mutation.
        from masterone.mdm.generation_staging import digest
        from masterone.mdm.models import ResolutionEngineSelection
        from masterone.mdm.workflow_assessment import compute_assessment
        from masterone.mdm.workflow_assessment_reads import active_assessment
        assessed = active_assessment(db, workflow_run)
        pairs = [p.baseline for p in db.query(WorkflowPair).filter_by(run_id=workflow_run)]
        payload = compute_assessment(records, pairs, db.get(ResolutionEngineSelection, workflow_run).configuration, frozenset())
        artifact = WorkflowArtifact(run_id=workflow_run, phase='PAIR_ASSESSMENT', payload=payload, content_hash=digest(payload))
        db.add(artifact)
        db.flush()
        assessed.artifact_id = artifact.id
        assessed.configuration = {k: v for k, v in payload.items() if k not in ('pairs', 'records')}
        from masterone.mdm.workflow_models import WorkflowConflict, WorkflowOperation
        operation = db.query(WorkflowOperation).filter_by(run_id=workflow_run).first()
        matching_conflict = WorkflowConflict(run_id=workflow_run, operation_id=operation.id,
            pair_id='historical-component-reference', payload={
                'reason': 'CLUSTER_BRIDGE_MISSING_EVIDENCE', 'record_ids': ids})
        foreign_conflict = WorkflowConflict(run_id=workflow_run, operation_id=operation.id,
            pair_id='another-component', payload={
                'reason': 'CLUSTER_BRIDGE_MISSING_EVIDENCE', 'record_ids': [ids[0], 'outside-record']})
        db.add_all([matching_conflict, foreign_conflict])
        db.flush()
        matching_conflict_id, foreign_conflict_id = matching_conflict.id, foreign_conflict.id
    state = read(client, workflow_run)
    body = request(state['workflow_revision'], expected_decision_revision=state['decision_revision'],
                   expected_assessment_id=state['assessment_id'], record_ids=ids, reason='Verified contact history')
    response = client.post(url + '/group-approvals', json=body)
    assert response.status_code == 200, response.text
    assert client.post(url + '/group-approvals', json=body).json() == response.json()
    with client.session_factory() as db:
        pending = db.get(WorkflowPair, 'group-p2')
        assert pending.decision == 'ACCEPTED'
        assert pending.review_reason == body['reason']
        assert db.query(ResolutionAuditEvent).filter_by(run_id=workflow_run, action='WORKFLOW_GROUP_APPROVAL').count() == 1
        assert db.get(WorkflowConflict, matching_conflict_id).resolved
        assert not db.get(WorkflowConflict, foreign_conflict_id).resolved
    with client.session_factory.begin() as db:
        # The unrelated conflict is outside this test's publication fixture.
        db.get(WorkflowConflict, foreign_conflict_id).resolved = True
    state = read(client, workflow_run)
    assert state['decision_revision'] == body['expected_decision_revision'] + 1
    assert not state['blockers']
    created = client.post(url + '/finalize-decisions', json=request(state['workflow_revision'],
                           expected_decision_revision=state['decision_revision']))
    assert created.status_code == 202
    run_one(client)
    clusters = client.get(url + '/clusters').json()['items']
    assert any(c['member_record_ids'] == ids for c in clusters)


def test_exact_approval_uses_legacy_source_policy():
    from masterone.mdm.workflow_group_approvals import validate_group
    with engine_context(dict(decision_semantics_v2_enabled=True, cluster_safety_v2_enabled=False)):
        source = soft_snapshot()
        for r in source['records']:
            r['source_system'] = 'CRM'
        assert validate_group(source, ['a', 'b']) == 'CLUSTER_SOURCE_UNIQUE_CONFLICT_CRM'


def test_group_reason_accepts_pending_soft_pair_without_inventing_connectivity(person_engine):
    from masterone.mdm.workflow_group_approvals import validate_group

    from tests.test_mdm.test_safe_clustering import edge

    records = [record(rid) for rid in ('a', 'b', 'c')]
    pairs = [edge(records[0], records[1]), edge(records[1], records[2]), edge(records[0], records[2])]
    pairs[2].update(primary_group='D', decision='REVIEW')
    source = dict(records=records, pairs=pairs, overlays={p['id']: 'ACCEPTED' for p in pairs[:2]},
                  negative_pairs=[], policy_version='person-v3')
    assert validate_group(source, ['a', 'b', 'c'], review_reason='Checked originals') == ''
    source['overlays'] = {}
    assert validate_group(source, ['a', 'b', 'c'], review_reason='Checked originals') == 'GROUP_ACCEPTED_LINKS_REQUIRED'


def test_group_approval_handler_never_hydrates_complete_run(client, workflow_run, monkeypatch):
    ready_decisions(client, workflow_run)
    from masterone.mdm import workflow_finalize
    monkeypatch.setattr(workflow_finalize, 'decision_snapshot', lambda *_a, **_k: (_ for _ in ()).throw(
        AssertionError('Small group approval must only read selected members')))
    url = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    with client.session_factory.begin() as db:
        pair = db.query(WorkflowPair).filter_by(run_id=workflow_run).first()
        ids = [pair.baseline['left_id'], pair.baseline['right_id']]
        pair.decision = 'ACCEPTED'
    state = read(client, workflow_run)
    response = client.post(url + '/group-approvals', json=request(state['workflow_revision'],
        expected_decision_revision=state['decision_revision'], record_ids=ids, reason='Checked members'))
    assert response.status_code in (200, 409), response.text


def test_group_approval_keeps_legacy_confirmed_non_match_edges(client, workflow_run):
    from masterone.mdm.models import ResolutionPair, ResolutionRun
    from masterone.mdm.workflow_group_approvals import group_snapshot
    ready_decisions(client, workflow_run)
    with client.session_factory.begin() as db:
        pair = db.query(WorkflowPair).filter_by(run_id=workflow_run).first()
        ids = [pair.baseline['left_id'], pair.baseline['right_id']]
        pair.decision = 'ACCEPTED'
        db.add(ResolutionPair(id='legacy-deny', run_id=workflow_run, left_record_id=ids[0], right_record_id=ids[1],
                             pair_decision='REVIEW', user_decision='NON_MATCH', score=0,
                             decision_reason='STEWARD_NEGATIVE_DECISION', field_evidence=[], blocking_reasons=[],
                             triage_status='REVIEW', triage_reason='STEWARD_NEGATIVE_DECISION'))
    with client.session_factory() as db:
        assert group_snapshot(db, db.get(ResolutionRun, workflow_run), ids)['negative_pairs'] == [tuple(sorted(ids))]


def test_group_approval_preserves_legacy_identifier_guards():
    from masterone.mdm.workflow_group_approvals import validate_group
    with engine_context(dict(decision_semantics_v2_enabled=False, cluster_safety_v2_enabled=False)):
        assert validate_group(soft_snapshot(), ['a', 'b']) == 'CLUSTER_GOV_ID_CONFLICT'


@pytest.mark.parametrize('bulk', [False, True])
def test_reasonless_requests_replay_preupgrade_receipts(client, workflow_run, bulk):
    from masterone.mdm.generation_staging import digest
    from masterone.mdm.workflow_models import WorkflowOperation
    ready_decisions(client, workflow_run)
    state = read(client, workflow_run)
    url = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    if bulk:
        suffix = '/bulk-decisions'
        body = request(state['workflow_revision'], expected_decision_revision=state['decision_revision'],
                       decision='no_merge', scope={'filter': {}})
    else:
        pair = client.get(url + '/pairs').json()['items'][0]
        suffix = f"/pairs/{pair['pair_id']}/decision"
        body = request(state['workflow_revision'], expected_pair_revision=pair['revision'], decision='no_merge')
    first = client.post(url + suffix, json=body)
    assert first.status_code in (200, 202)
    with client.session_factory.begin() as db:
        op = db.query(WorkflowOperation).filter_by(run_id=workflow_run, request_id=body['request_id']).one()
        op.payload = {k: v for k, v in op.payload.items() if k != 'reason'}
        op.payload_hash = digest(op.payload)
    repeated = client.post(url + suffix, json=body)
    assert repeated.status_code == first.status_code
    assert repeated.json() == first.json()
