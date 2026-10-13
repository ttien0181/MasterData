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
    assert cluster_snapshot(changed, 'r')['safe_clusters'] == []
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
