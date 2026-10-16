from sqlalchemy import event

from tests.test_mdm.test_workflow_contract import request
from tests.test_mdm.test_workflow_golden import ready_golden
from tests.test_mdm.test_workflow_stages import read, ready_decisions, run_one


def test_status_does_not_materialize_pair_payloads(client, workflow_run):
    ready_decisions(client, workflow_run)
    from masterone.mdm.workflow_models import WorkflowPair
    with client.session_factory.begin() as db:
        pair = db.query(WorkflowPair).filter_by(run_id=workflow_run).first()
        pair.decision = 'DEFERRED'
        from masterone.mdm.models import ResolutionRun
        # Direct fixture writes must advance the revision just like the review API.
        db.get(ResolutionRun, workflow_run).decision_revision += 1
    statements = []
    engine = client.session_factory.kw['bind']
    def record(_conn, _cursor, statement, *_):
        statements.append(statement.lower())
    event.listen(engine, 'before_cursor_execute', record)
    try:
        state = read(client, workflow_run)
        assert any(b['code'] == 'PENDING_DECISIONS' for b in state['blockers'])
    finally:
        event.remove(engine, 'before_cursor_execute', record)
    assert not any('mdm_workflow_pairs.baseline as' in s for s in statements)
    # Status includes auth/profile queries; it must not repeat operation/count reads.
    assert sum('from mdm_workflow_operations' in s for s in statements) <= 1
    assert sum('from mdm_workflow_pairs' in s for s in statements) <= 1


def test_bulk_confirm_scope_replay_and_exclusion(client, workflow_run):
    ready_golden(client, workflow_run)
    url = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    clusters = client.get(url + '/clusters').json()['items']
    excluded = clusters[0]['cluster_id']
    state = read(client, workflow_run)
    body = request(state['workflow_revision'], expected_cluster_revision=state['cluster_revision'],
                   expected_golden_revision=state['golden_revision'], excluded_cluster_ids=[excluded])
    result = client.post(url + '/golden/confirm-selected', json=body)
    assert result.status_code == 200, result.text
    assert result.json()['confirmed'] == len(clusters) - 1
    assert client.post(url + '/golden/confirm-selected', json=body).json() == result.json()
    records = client.get(url + '/golden').json()['items']
    assert next(g for g in records if g['cluster_id'] == excluded)['status'] == 'DRAFT'
    assert all(g['status'] == 'CONFIRMED' for g in records if g['cluster_id'] != excluded)


def test_explanation_reports_actual_fallback(client, workflow_run):
    ready_decisions(client, workflow_run)
    url = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    pair = client.get(url + '/pairs').json()['items'][0]
    response = client.post(url + f"/pairs/{pair['pair_id']}/explanation", json=request(read(client, workflow_run)['workflow_revision']))
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'TEMPLATE'
    assert response.json()['error_code'] == 'LLM_DISABLED'


def test_resolve_confirms_safe_golden_without_changing_pairs(client, workflow_run):
    ready_golden(client, workflow_run)
    url = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    state = read(client, workflow_run)
    body = request(state['workflow_revision'], expected_cluster_revision=state['cluster_revision'],
                   expected_golden_revision=state['golden_revision'], expected_decision_revision=state['decision_revision'])
    result = client.post(url + '/golden/resolve', json=body)
    assert result.status_code == 202, result.text
    run_one(client)
    assert read(client, workflow_run)['decision_revision'] == state['decision_revision']
    assert all(g['status'] == 'CONFIRMED' for g in client.get(url + '/golden').json()['items'])


def test_conflict_retains_disconnected_valid_group():
    from masterone.mdm.engine_adapter import engine_context
    from masterone.mdm.workflow_finalize import cluster_snapshot

    from tests.test_mdm.test_decision_semantics import record
    from tests.test_mdm.test_safe_clustering import chain, edge
    a, b, c = chain()
    x, y = record('x'), record('y')
    pairs = [edge(a, b, 15), edge(b, c, 14), edge(x, y, 20)]
    pairs[2]['triage_status'] = 'HIGH_CONFIDENCE_PROPOSAL'
    for p in pairs[:2]:
        p['decision'] = 'REVIEW'
    snapshot = {'records': [a,b,c,x,y], 'pairs': pairs,
                'overlays': {p['id']: 'ACCEPTED' for p in pairs[:2]},
                'negative_pairs': [], 'policy_version': 'person-v3'}
    with engine_context(dict(decision_semantics_v2_enabled=True, cluster_safety_v2_enabled=True, generation_staging_enabled=False)):
        result = cluster_snapshot(snapshot, 'audit')
    assert result['conflicts']
    assert any(set(g['member_record_ids']) == {'x', 'y'} for g in result['safe_clusters'])
    assert all(not set(g['member_record_ids']) & {'a','b','c'} for g in result['safe_clusters'])


def test_partial_publication_repair_and_undo_are_fenced(client, workflow_run, monkeypatch):
    from copy import deepcopy

    from masterone.mdm.workflow_finalize import cluster_snapshot

    from tests.test_mdm.test_workflow_finalize import complete_reviews, finalize

    ready_decisions(client, workflow_run)
    complete_reviews(client, workflow_run)
    url = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    original = cluster_snapshot
    forced_pair = {}
    def one_guard_failure(snapshot, run_id, **kwargs):
        result = original(snapshot, run_id, **kwargs)
        pair = next(p for p in snapshot['pairs'] if p['decision'] == 'MERGEABLE')
        forced_pair['id'] = pair['id']
        if snapshot['overlays'].get(pair['id']) != 'REJECTED':
            affected = [pair['left_id'],pair['right_id']]
            safe = [c for c in result['clusters'] if not set(c['member_record_ids']) & set(affected)]
            result.update(clusters=[],safe_clusters=safe,conflict_record_ids=affected,
                          conflicts=[{'pair_id':pair['id'],'reason':'CLUSTER_REVIEW_VETO','record_ids':affected}])
        return result
    monkeypatch.setattr('masterone.mdm.workflow_finalize.cluster_snapshot', one_guard_failure)
    assert finalize(client, workflow_run).status_code == 202
    run_one(client)
    state = read(client, workflow_run)
    assert state['workflow_state'] == 'DECIDE_REVIEW'
    assert client.get(url + '/clusters').json()['total'] == 1
    def body(state, **extra):
        return request(state['workflow_revision'],expected_cluster_revision=state['cluster_revision'],
                       expected_golden_revision=state['golden_revision'],expected_decision_revision=state['decision_revision'],**extra)
    resolution = client.post(url + '/golden/resolve',json=body(state))
    assert resolution.status_code == 202, resolution.text
    run_one(client)
    read_result = client.get(url + '/golden/resolution').json()
    assert read_result['resolution']['result']['confirmed'] == 1
    assert read_result['resolution']['result']['proposal']['status'] == 'READY'
    assert client.get(url + '/golden').json()['items'][0]['status'] == 'CONFIRMED'
    assert not read(client,workflow_run)['allowed_actions'].count('finalize-golden')
    state = read(client,workflow_run)
    apply_body = body(state,operation_id=resolution.json()['operation_id'])
    applied = client.post(url + '/golden/resolution/apply',json=apply_body)
    assert applied.status_code == 202,applied.text
    assert client.post(url + '/golden/resolution/apply',json=apply_body).json() == applied.json()
    # A failed publication followed by explicit retry must still offer a valid undo.
    import pytest
    from masterone.mdm.job_queue import claim_next, failed, ownership
    from masterone.mdm.models import ResolutionTask
    from masterone.mdm.workflow_jobs import execute_workflow
    with client.session_factory.begin() as db:
        db.query(ResolutionTask).filter_by(run_id=workflow_run).one().max_attempts = 1
    claim = claim_next(client.session_factory, 'repair-failure', lease_seconds=300)
    with ownership(claim), monkeypatch.context() as patch:
        def fail_compute(*_, **_kwargs):
            raise ValueError('injected-repair-publication-failure')
        patch.setattr('masterone.mdm.workflow_finalize.cluster_snapshot', fail_compute)
        with pytest.raises(ValueError, match='injected-repair'):
            execute_workflow(applied.json()['operation_id'],client.session_factory,lambda *_:None)
    failed(client.session_factory, claim)
    retry_body = request(read(client,workflow_run)['workflow_revision'])
    assert client.post(url + f"/operations/{applied.json()['operation_id']}/retry",json=retry_body).status_code == 202
    run_one(client)
    state = read(client,workflow_run)
    assert state['workflow_state'] == 'GOLDEN_REVIEW'
    groups = client.get(url + '/golden').json()['items']
    assert len(groups) == 3 and all(g['status'] == 'CONFIRMED' for g in groups)
    receipt = client.get(url + '/golden/resolution').json()['undo_operation_id']
    from masterone.mdm.workflow_models import WorkflowOperation
    with client.session_factory() as db:
        history = [(o.operation_type, str(o.created_at), o.id, o.result.get('decision_revision')) for o in db.query(WorkflowOperation).filter_by(run_id=workflow_run)]
    assert receipt == applied.json()['result']['operation_id'], history
    stale = deepcopy(apply_body)
    stale['request_id'] = 'stale-repair'
    assert client.post(url + '/golden/resolution/apply',json=stale).status_code == 409
    undone = client.post(url + '/golden/resolution/undo',json=body(state,operation_id=receipt))
    assert undone.status_code == 202,undone.text
    run_one(client)
    assert read(client,workflow_run)['workflow_state'] == 'DECIDE_REVIEW'
    assert client.get(url + '/golden/resolution').json()['undo_operation_id'] is None
