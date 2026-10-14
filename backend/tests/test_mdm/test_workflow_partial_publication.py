from copy import deepcopy

import pytest
from masterone.mdm.engine_adapter import engine_context
from masterone.mdm.workflow_models import WorkflowArtifact, WorkflowConflict, WorkflowOperation, WorkflowPair

from tests.test_mdm.test_decision_semantics import record
from tests.test_mdm.test_safe_clustering import edge as candidate_edge
from tests.test_mdm.test_workflow_contract import request
from tests.test_mdm.test_workflow_stages import read, ready_decisions, run_one


def edge(a, b):
    return {**candidate_edge(a, b), 'triage_status': 'HIGH_CONFIDENCE_PROPOSAL'}


def test_unreviewed_edges_do_not_transitively_expand_one_dependency():
    from masterone.mdm.workflow_partial import candidate_region
    records = [record(rid) for rid in 'abcd']
    pairs = [edge(records[i], records[i + 1]) for i in range(3)]
    for pair in pairs:
        pair['decision'] = 'REVIEW'
    source = snapshot(records, pairs)
    assert candidate_region(source, {'a', 'b'}) == {'a', 'b'}
    # Explicitly accepted links still carry the affected dependency to the full group.
    source['overlays'][pairs[1]['id']] = 'ACCEPTED'
    assert candidate_region(source, {'a', 'b'}) == {'a', 'b', 'c'}


def test_all_pending_endpoints_remain_unresolved_even_without_review_propagation():
    records = [record(rid) for rid in 'abcd']
    pairs = [edge(records[i], records[i + 1]) for i in range(3)]
    for pair in pairs:
        pair['decision'] = 'REVIEW'
    assert partial_result(snapshot(records, pairs))['unresolved_record_ids'] == list('abcd')


def partial_result(snapshot):
    from masterone.mdm import workflow_finalize
    compute = getattr(workflow_finalize, 'partial_cluster_snapshot', None)
    assert callable(compute), 'Partial publication must isolate unresolved candidate regions'
    with engine_context(dict(decision_semantics_v2_enabled=True, cluster_safety_v2_enabled=True,
                             generation_staging_enabled=False)):
        return compute(snapshot, 'partial-run')


def snapshot(records, pairs, overlays=None, conflicts=None):
    return dict(records=records, pairs=pairs, overlays=overlays or {}, negative_pairs=[],
                policy_version='person-v3', known_conflicts=conflicts or [])


def test_deferred_candidate_region_cannot_join_through_accepted_edges():
    a, b, c, x, y, z = [record(rid) for rid in ('a', 'b', 'c', 'x', 'y', 'z')]
    pairs = [edge(a, b), edge(b, c), edge(x, y)]
    source = snapshot([a, b, c, x, y, z], pairs,
                      {pairs[0]['id']: 'DEFERRED', pairs[1]['id']: 'ACCEPTED'})
    original = deepcopy(source)
    result = partial_result(source)
    assert result['unresolved_record_ids'] == ['a', 'b', 'c']
    assert result['unresolved_pair_ids'] == [pairs[0]['id']]
    assert [c['member_record_ids'] for c in result['safe_clusters']] == [['x', 'y']]
    assert source == original


@pytest.mark.parametrize('boundary', ['REJECTED', 'NON_MATCH', 'BLOCKED', 'NOT_MERGEABLE'])
def test_negative_and_blocked_boundaries_do_not_spread_pending_region(boundary):
    a, b, x, y = [record(rid) for rid in ('a', 'b', 'x', 'y')]
    pairs = [edge(a, b), edge(b, x), edge(x, y)]
    overlays = {pairs[0]['id']: 'DEFERRED'}
    if boundary in ('REJECTED', 'NON_MATCH'):
        overlays[pairs[1]['id']] = boundary
    else:
        pairs[1]['decision'] = boundary
    result = partial_result(snapshot([a, b, x, y], pairs, overlays))
    assert result['unresolved_record_ids'] == ['a', 'b']
    assert [c['member_record_ids'] for c in result['safe_clusters']] == [['x', 'y']]


def test_unacknowledged_mergeable_triage_is_pending():
    a, b, x, y = [record(rid) for rid in ('a', 'b', 'x', 'y')]
    pairs = [edge(a, b), edge(x, y)]
    pairs[0].update(triage_status='REVIEW', triage_reason='CLUSTER_BRIDGE_MISSING_EVIDENCE')
    result = partial_result(snapshot([a, b, x, y], pairs))
    assert result['unresolved_record_ids'] == ['a', 'b']
    assert result['unresolved_pair_ids'] == [pairs[0]['id']]


def test_known_conflict_is_regenerated_and_disconnected_safe_group_survives():
    a, b, x, y = [record(rid) for rid in ('a', 'b', 'x', 'y')]
    pairs = [edge(a, b), edge(x, y)]
    source = snapshot([a, b, x, y], pairs, {pairs[0]['id']: 'ACCEPTED'},
                      [{'id': 'old', 'pair_id': pairs[0]['id'], 'reason': 'CLUSTER_CONFIRMED_CANNOT_LINK',
                        'record_ids': ['a', 'b']}])
    source['negative_pairs'] = [['a', 'b']]
    result = partial_result(source)
    assert result['conflicts']
    assert result['unresolved_record_ids'] == ['a', 'b']
    assert [c['member_record_ids'] for c in result['safe_clusters']] == [['x', 'y']]
    assert result['reevaluated_conflict_ids'] == ['old']
    source['negative_pairs'] = []
    result = partial_result(source)
    assert not result['conflicts']
    assert not result['unresolved_record_ids']
    assert len(result['safe_clusters']) == 2


@pytest.mark.parametrize('baseline', ['REVIEW'])
def test_unresolved_review_expands_through_automatic_candidate_chain(baseline):
    a, b, c, d, x, y = [record(rid) for rid in ('a', 'b', 'c', 'd', 'x', 'y')]
    pairs = [edge(a, b), edge(b, c), edge(c, d), edge(x, y)]
    pairs[0]['decision'] = baseline
    result = partial_result(snapshot([a, b, c, d, x, y], pairs, {pairs[2]['id']: 'ACCEPTED'}))
    assert result['unresolved_record_ids'] == ['a', 'b', 'c', 'd']
    assert [c['member_record_ids'] for c in result['safe_clusters']] == [['x', 'y']]


def test_known_conflict_in_pending_region_is_retained_until_review():
    a, b, c, x, y = [record(rid) for rid in ('a', 'b', 'c', 'x', 'y')]
    pairs = [edge(a, b), edge(b, c), edge(x, y)]
    known = {'id': 'retained', 'pair_id': pairs[1]['id'], 'reason': 'CLUSTER_PAIR_PERMISSION_CONFLICT',
             'record_ids': ['b', 'c']}
    result = partial_result(snapshot([a, b, c, x, y], pairs,
        {pairs[0]['id']: 'DEFERRED', pairs[1]['id']: 'ACCEPTED'}, [known]))
    assert result['unresolved_record_ids'] == ['a', 'b', 'c']
    assert result['conflicts'] == [{k: v for k, v in known.items() if k != 'id'}]
    assert result['retained_conflict_ids'] == ['retained']
    assert result['reevaluated_conflict_ids'] == []


def test_partial_publication_api_covers_safe_groups_then_completes(client, workflow_run):
    csv = (b'source_record_id,source_system,name,phone,email,address\n'
           b'1,CRM,Nguyen Van An,0901234567,a@example.test,123 Hanoi\n'
           b'2,BILL,Nguyen Van An,0901234567,a@example.test,123 Hanoi\n'
           b'3,CRM,Tran Thi Binh,0987654321,b@example.test,456 HCM\n'
           b'4,BILL,Tran Thi Binh,0987654321,b@example.test,456 HCM\n'
           b'5,CRM,Le Van Cuong,0911223344,c@example.test,789 Hue\n')
    response = client.post('/api/v1/mdm/runs', data={'workflow_version': '2'},
                           files={'files': ('partial.csv', csv, 'text/csv')})
    assert response.status_code == 201, response.text
    run_id = response.json()['run_id']
    # Finish the fixture run before claiming operations for this independent run.
    run_one(client)
    ready_decisions(client, run_id)
    url = f'/api/v1/mdm/runs/{run_id}/workflow'
    pairs = client.get(url + '/pairs').json()['items']
    mergeable = [p for p in pairs if p['baseline']['decision'] == 'MERGEABLE']
    assert len(mergeable) == 2
    deferred_id = mergeable[-1]['pair_id']
    safe_id = mergeable[0]['pair_id']
    for pair in pairs:
        decision = 'defer' if pair['pair_id'] == deferred_id else 'merge' if pair['pair_id'] == safe_id else 'no_merge'
        state = read(client, run_id)
        response = client.post(url + f"/pairs/{pair['pair_id']}/decision", json=request(
            state['workflow_revision'], expected_pair_revision=pair['revision'], decision=decision))
        assert response.status_code == 200, response.text
    with client.session_factory.begin() as db:
        deferred = db.get(WorkflowPair, deferred_id)
        source_operation = db.query(WorkflowOperation).filter_by(run_id=run_id, operation_type='DECIDE').one()
        db.add(WorkflowConflict(run_id=run_id, operation_id=source_operation.id, pair_id=deferred_id,
            payload={'reason': 'CLUSTER_PAIR_PERMISSION_CONFLICT',
                     'record_ids': [deferred.baseline['left_id'], deferred.baseline['right_id']]}))
    state = read(client, run_id)
    body = request(state['workflow_revision'], expected_decision_revision=state['decision_revision'])
    assert client.post(url + '/finalize-decisions', json=body).status_code == 409
    body['request_id'] += '-partial'
    body['allow_partial'] = True
    response = client.post(url + '/finalize-decisions', json=body)
    assert response.status_code == 202, response.text
    assert client.post(url + '/finalize-decisions', json=body).json() == response.json()
    op_id = response.json()['operation_id']
    with client.session_factory.begin() as db:
        db.get(WorkflowOperation, op_id).result = {'repair_record_ids': ['receipt-sentinel'],
                                                 'auto_confirm_record_ids': []}
    run_one(client)
    state = read(client, run_id)
    assert state['workflow_state'] == 'DECIDE_REVIEW'
    assert 'finalize-golden' not in state['allowed_actions']
    groups = client.get(url + '/clusters').json()['items']
    assert sorted(len(g['member_record_ids']) for g in groups) == [1, 2]
    operation_result = client.get(url + f'/operations/{op_id}').json()['result']
    assert 'unresolved_record_ids' not in operation_result
    assert 'unresolved_pair_ids' not in operation_result
    assert 'unresolved_record_ids' not in operation_result['publication']
    assert 'unresolved_pair_ids' not in operation_result['publication']
    with client.session_factory() as db:
        op = db.get(WorkflowOperation, op_id)
        publication = op.result['publication']
        assert publication['publication_status'] == 'PARTIAL'
        assert (publication['covered_records'], publication['unresolved_records'], publication['total_records']) == (3, 2, 5)
        assert publication['multi_record_groups'] == publication['singletons'] == 1
        assert 'unresolved_record_ids' not in op.result
        assert 'unresolved_pair_ids' not in op.result
        assert 'unresolved_record_ids' not in publication
        assert 'unresolved_pair_ids' not in publication
        assert op.result['repair_record_ids'] == ['receipt-sentinel']
        assert op.result['auto_confirm_record_ids'] == []
        assert db.query(WorkflowConflict).filter_by(run_id=run_id, resolved=False).count() == 1
        artifact = db.query(WorkflowArtifact).filter_by(run_id=run_id, phase='CLUSTER').one()
        artifact_publication = artifact.payload['publication']
        assert artifact_publication['unresolved_pair_ids'] == [deferred_id]
        assert len(artifact_publication['unresolved_record_ids']) == 2
        assert {key: artifact_publication[key] for key in publication} == publication
        assert artifact.payload['cluster_metrics']['total_records'] == 5
        snapshot_artifact = db.get(WorkflowArtifact, op.artifact_id)
        assert len(snapshot_artifact.payload['records']) == 5
        pair = db.get(WorkflowPair, deferred_id)
        pair_revision = pair.revision
    state = read(client, run_id)
    assert client.post(url + f'/pairs/{deferred_id}/decision', json=request(state['workflow_revision'],
        expected_pair_revision=pair_revision, decision='no_merge')).status_code == 200
    state = read(client, run_id)
    response = client.post(url + '/finalize-decisions', json=request(state['workflow_revision'],
        expected_decision_revision=state['decision_revision']))
    assert response.status_code == 202, response.text
    run_one(client)
    assert read(client, run_id)['workflow_state'] == 'GOLDEN_REVIEW'
    with client.session_factory() as db:
        publication = db.get(WorkflowOperation, response.json()['operation_id']).result['publication']
        assert publication['publication_status'] == 'COMPLETE'
        assert publication['covered_records'] == publication['total_records'] == 5
        assert publication['unresolved_records'] == 0
        assert 'unresolved_record_ids' not in publication
        assert 'unresolved_pair_ids' not in publication


def test_cluster_operation_bounds_blockers_without_truncating_artifact(client, workflow_run, monkeypatch):
    from masterone.mdm import workflow_finalize

    from tests.test_mdm.test_workflow_finalize import complete_reviews, finalize

    ready_decisions(client, workflow_run)
    complete_reviews(client, workflow_run)
    original = workflow_finalize.cluster_snapshot

    def many_conflicts(snapshot, run_id, **kwargs):
        result = original(snapshot, run_id, **kwargs)
        record_ids = [r['id'] for r in snapshot['records']]
        evidence = record_ids + [f'conflict-record-{index}' for index in range(150 - len(record_ids))]
        result.update(clusters=[], safe_clusters=[], conflict_record_ids=record_ids,
            conflicts=[{'pair_id': snapshot['pairs'][0]['id'], 'reason': f'CLUSTER_BLOCKER_{index}',
                        'record_ids': evidence} for index in range(30)])
        return result

    monkeypatch.setattr(workflow_finalize, 'cluster_snapshot', many_conflicts)
    response = finalize(client, workflow_run)
    assert response.status_code == 202, response.text
    run_one(client)
    op_id = response.json()['operation_id']
    url = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    result = client.get(url + f'/operations/{op_id}').json()['result']
    assert result['blocker_count'] == 30
    assert read(client, workflow_run)['latest_cluster_operation']['result']['blocker_count'] == 30
    assert len(result['blockers']) == 25
    assert all(len(b['record_ids']) == 100 and b['record_count'] == 150 for b in result['blockers'])
    with client.session_factory() as db:
        stored = db.get(WorkflowOperation, op_id).result
        assert stored['blocker_count'] == 30
        assert len(stored['blockers']) == 25
        assert all(len(b['record_ids']) == 100 and b['record_count'] == 150 for b in stored['blockers'])
        artifact = db.query(WorkflowArtifact).filter_by(run_id=workflow_run, phase='CLUSTER').one()
        assert len(artifact.payload['conflicts']) == 30
        assert all(len(b['record_ids']) == 150 for b in artifact.payload['conflicts'])
        assert db.query(WorkflowConflict).filter_by(run_id=workflow_run, resolved=False).count() == 30


@pytest.mark.parametrize('blocker', ['DECIDE_NOT_READY', 'ACTIVE_OPERATION', 'NO_VALID_RECORDS'])
def test_allow_partial_keeps_basic_completion_guards(client, workflow_run, blocker):
    from masterone.mdm.models import ResolutionRun
    if blocker == 'DECIDE_NOT_READY':
        run_one(client)
    else:
        ready_decisions(client, workflow_run)
    state = read(client, workflow_run)
    if blocker == 'ACTIVE_OPERATION':
        queued = client.post(f'/api/v1/mdm/runs/{workflow_run}/workflow/bulk-decisions', json=request(
            state['workflow_revision'], expected_decision_revision=state['decision_revision'],
            decision='no_merge', scope={'filter': {}}))
        assert queued.status_code == 202, queued.text
    elif blocker == 'NO_VALID_RECORDS':
        with client.session_factory.begin() as db:
            db.get(ResolutionRun, workflow_run).record_count = 0
    state = read(client, workflow_run)
    response = client.post(f'/api/v1/mdm/runs/{workflow_run}/workflow/finalize-decisions', json=request(
        state['workflow_revision'], expected_decision_revision=state['decision_revision'], allow_partial=True))
    assert response.status_code == 409, response.text
    assert blocker in {b['code'] for b in response.json()['detail']['blockers']}
