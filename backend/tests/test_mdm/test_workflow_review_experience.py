"""User-visible workflow contracts, exercised through isolated real API sessions."""
from copy import deepcopy

from masterone.mdm.models import ResolutionNegativeEdge
from masterone.mdm.workflow_models import WorkflowConflict, WorkflowOperation, WorkflowPair

from tests.test_mdm.test_workflow_contract import request
from tests.test_mdm.test_workflow_finalize import complete_reviews, finalize
from tests.test_mdm.test_workflow_stages import read, ready_decisions, run_one


def published(client, run_id):
    ready_decisions(client, run_id)
    complete_reviews(client, run_id)
    assert finalize(client, run_id).status_code == 202
    run_one(client)
    return f'/api/v1/mdm/runs/{run_id}/workflow'


def test_reopened_snapshot_is_readable_but_cannot_be_changed(client, workflow_run):
    url = published(client, workflow_run)
    before = client.get(url + '/clusters').json()
    state = read(client, workflow_run)
    assert client.post(url + '/reopen-decisions', json=request(state['workflow_revision'], reason='Correct decisions')).status_code == 200
    state = read(client, workflow_run)
    snapshot = client.get(url + '/clusters')
    assert snapshot.status_code == 200, snapshot.text
    assert snapshot.json()['items'] == before['items']
    assert snapshot.json()['generation']['stale'] is True
    assert state['cluster_result']['read_only'] is True
    assert 'save-golden' not in state['allowed_actions']
    cid = before['items'][0]['cluster_id']
    golden = client.get(url + f'/clusters/{cid}/golden')
    assert golden.status_code == 200
    assert golden.json()['read_only'] is True
    body = request(state['workflow_revision'], expected_cluster_revision=state['cluster_revision'],
                   expected_golden_revision=state['golden_revision'], selections={})
    assert client.put(url + f'/clusters/{cid}/golden', json=body).status_code == 409
    assert client.post(url.replace('/workflow', '') + '/previews', json={'choices': {}, 'acknowledge_unreviewed': False}).status_code == 409
    assert finalize(client, workflow_run).status_code == 202
    # The published snapshot remains readable while its replacement is queued.
    assert client.get(url + '/clusters').status_code == 200
    assert client.get(url + '/clusters').json()['generation']['read_only'] is True


def test_required_review_counts_groups_and_filter_agree(client, workflow_run):
    ready_decisions(client, workflow_run)
    url = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    with client.session_factory.begin() as db:
        base = deepcopy(db.query(WorkflowPair).filter_by(run_id=workflow_run).first().baseline)
        db.query(WorkflowPair).filter_by(run_id=workflow_run).delete()
        for identifier, baseline, decision, triage in [
            ('auto', 'MERGEABLE', 'UNDECIDED', 'HIGH_CONFIDENCE_PROPOSAL'),
            ('blocked-deferred', 'BLOCKED', 'DEFERRED', 'BLOCKED'),
            ('guard-review', 'MERGEABLE', 'UNDECIDED', 'REVIEW'),
            ('manual', 'REVIEW', 'UNDECIDED', 'REVIEW'),
        ]:
            db.add(WorkflowPair(id=identifier, run_id=workflow_run, decision=decision,
                baseline={**base, 'id': identifier, 'decision': baseline, 'triage_status': triage,
                          'primary_group': 'A', 'subgroup': 'A1_NAME_PHONE'}))
    summary = client.get(url + '/pairs/summary').json()
    assert summary['needs_review'] == 3
    assert sum(group['pending'] for group in summary['groups']) == 3
    filtered = client.get(url + '/pairs?resolution=REQUIRED').json()
    assert {p['pair_id'] for p in filtered['items']} == {'blocked-deferred', 'guard-review', 'manual'}
    assert client.get(url + '/pairs?resolution=PENDING').json()['total'] == 4
    blocker = next(b for b in read(client, workflow_run)['blockers'] if b['code'] == 'PENDING_DECISIONS')
    assert blocker['count'] == 3


def test_cluster_receipt_reports_full_record_coverage(client, workflow_run):
    url = published(client, workflow_run)
    state = read(client, workflow_run)
    result = state['cluster_result']
    assert result['publication_status'] == 'COMPLETE'
    assert result['covered_records'] == result['total_records'] == 3
    assert result['unresolved_records'] == 0
    assert result['clusters'] == result['multi_record_groups'] + result['singletons']
    assert state['latest_cluster_operation']['result']['publication_status'] == 'COMPLETE'
    assert client.get(url + '/clusters').json()['generation']['revision'] == result['revision']


def test_archived_run_explains_disabled_completion(client, workflow_run):
    from masterone.db.base import now_utc
    from masterone.mdm.models import ResolutionRun
    ready_decisions(client, workflow_run)
    with client.session_factory.begin() as db:
        db.get(ResolutionRun, workflow_run).archived_at = now_utc()
    state = read(client, workflow_run)
    assert 'RUN_ARCHIVED' in {b['code'] for b in state['blockers']}
    assert state['allowed_actions'] == []


def test_conflict_read_hydrates_related_records_and_pairs(client, workflow_run):
    ready_decisions(client, workflow_run)
    url = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    with client.session_factory.begin() as db:
        pair = db.query(WorkflowPair).filter_by(run_id=workflow_run).first()
        operation = db.query(WorkflowOperation).filter_by(run_id=workflow_run).first()
        db.add(WorkflowConflict(run_id=workflow_run, operation_id=operation.id, pair_id=pair.id,
            payload={'reason': 'CLUSTER_REVIEW_VETO', 'record_ids': [pair.baseline['left_id'], pair.baseline['right_id']]}))
    response = client.get(url + '/conflicts?limit=1')
    assert response.status_code == 200, response.text
    item = response.json()['items'][0]
    assert item['reason'] == 'CLUSTER_REVIEW_VETO'
    assert len(item['records']) == 2
    assert item['pairs'][0]['left_record']['source_system']
    assert item['pairs'][0]['right_record']['raw_fields']['name']
    assert client.get(url + '/pairs/' + item['pair_id']).status_code == 200


def test_amend_deny_removes_only_its_traced_edge_and_is_idempotent(client, workflow_run):
    ready_decisions(client, workflow_run)
    url = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    pair = client.get(url + '/pairs').json()['items'][0]
    state = read(client, workflow_run)
    denied = client.post(url + f"/pairs/{pair['pair_id']}/decision", json=request(state['workflow_revision'],
        expected_pair_revision=pair['revision'], decision='deny')).json()
    with client.session_factory.begin() as db:
        ids = sorted([pair['baseline']['left_id'], pair['baseline']['right_id']])
        db.add(ResolutionNegativeEdge(run_id=workflow_run, left_record_id=ids[0], right_record_id=ids[1],
                                     created_by='SYSTEM', reason='HARD_CONSTRAINT', active=True))
    state = read(client, workflow_run)
    body = request(state['workflow_revision'], expected_pair_revision=denied['revision'],
                   decision='defer', reason='The previous Deny was a mistaken click')
    response = client.post(url + f"/pairs/{pair['pair_id']}/amend", json=body)
    assert response.status_code == 200, response.text
    assert response.json()['decision'] == 'DEFERRED'
    assert client.post(url + f"/pairs/{pair['pair_id']}/amend", json=body).json() == response.json()
    with client.session_factory() as db:
        edges = db.query(ResolutionNegativeEdge).filter_by(run_id=workflow_run).all()
        assert len(edges) == 2
        assert next(e for e in edges if e.reason == 'HARD_CONSTRAINT').active
        assert not next(e for e in edges if e.reason == 'STEWARD_REJECT').active
    state = read(client, workflow_run)
    blocked = client.post(url + f"/pairs/{pair['pair_id']}/amend", json=request(state['workflow_revision'],
        expected_pair_revision=response.json()['revision'], decision='merge', reason='Try merging'))
    assert blocked.status_code == 409


def test_receipt_lookup_proves_acknowledged_decision_without_rewriting(client, workflow_run):
    ready_decisions(client, workflow_run)
    url = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    assert client.get(url + '/requests/not-sent').status_code == 200
    assert client.get(url + '/requests/not-sent').json() is None
    pair = client.get(url + '/pairs').json()['items'][0]
    state = read(client, workflow_run)
    body = request(state['workflow_revision'], expected_pair_revision=pair['revision'], decision='defer')
    saved = client.post(url + f"/pairs/{pair['pair_id']}/decision", json=body)
    receipt = client.get(url + '/requests/' + body['request_id'])
    assert receipt.status_code == 200
    assert receipt.json()['status'] == 'COMPLETED'
    assert receipt.json()['result']['revision'] == saved.json()['revision']
    assert read(client, workflow_run)['decision_revision'] == state['decision_revision'] + 1


def test_legacy_deny_without_trace_is_not_guessed_or_removed(client, workflow_run):
    from masterone.mdm.models import ResolutionAuditEvent, ResolutionRun
    ready_decisions(client, workflow_run)
    url = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    with client.session_factory.begin() as db:
        pair = db.query(WorkflowPair).filter_by(run_id=workflow_run).first()
        pair.decision = 'NON_MATCH'
        pair.revision += 1
        identifier, revision = pair.id, pair.revision
        ids = sorted([pair.baseline['left_id'], pair.baseline['right_id']])
        owner = db.get(ResolutionRun, workflow_run).owner_user_id
        db.add(ResolutionNegativeEdge(run_id=workflow_run, left_record_id=ids[0], right_record_id=ids[1],
                                     created_by=owner, reason='STEWARD_REJECT', active=True))
        db.add(ResolutionAuditEvent(run_id=workflow_run, actor_user_id=owner,
            action='WORKFLOW_PAIR_NON_MATCH', details={'pair_id': identifier, 'pair_revision': revision}))
    state = read(client, workflow_run)
    response = client.post(url + f'/pairs/{identifier}/amend', json=request(state['workflow_revision'],
        expected_pair_revision=revision, decision='defer', reason='Correct an old decision'))
    assert response.status_code == 409
    assert response.json()['detail']['code'] == 'DENY_SOURCE_UNKNOWN'
    with client.session_factory() as db:
        assert db.query(ResolutionNegativeEdge).filter_by(run_id=workflow_run, active=True).count() == 1
        assert db.get(WorkflowPair, identifier).decision == 'NON_MATCH'
        assert db.get(WorkflowPair, identifier).revision == revision


def test_amend_deny_rechecks_cached_assessment_and_can_publish_corrected_match(client, workflow_run):
    ready_decisions(client, workflow_run)
    url = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    pair = next(p for p in client.get(url + '/pairs').json()['items'] if p['baseline']['decision'] == 'MERGEABLE')
    state = read(client, workflow_run)
    denied = client.post(url + f"/pairs/{pair['pair_id']}/decision", json=request(state['workflow_revision'],
        expected_pair_revision=pair['revision'], decision='deny')).json()
    state = read(client, workflow_run)
    assert client.post(url + '/reassess', json=request(state['workflow_revision'])).status_code == 202
    run_one(client)
    assessed = client.get(url + f"/pairs/{pair['pair_id']}").json()
    assert not assessed['eligibility']['manual_allowed']
    state = read(client, workflow_run)
    response = client.post(url + f"/pairs/{pair['pair_id']}/amend", json=request(state['workflow_revision'],
        expected_pair_revision=denied['revision'], expected_assessment_id=state['assessment_id'],
        decision='merge', reason='Verified the earlier Deny was incorrect'))
    assert response.status_code == 200, response.text
    assert response.json()['decision'] == 'ACCEPTED'
    for other in client.get(url + '/pairs').json()['items']:
        if other['decision'] in ('UNDECIDED', 'DEFERRED') and other['baseline']['decision'] == 'REVIEW':
            current = read(client, workflow_run)
            assert client.post(url + f"/pairs/{other['pair_id']}/decision", json=request(current['workflow_revision'],
                expected_pair_revision=other['revision'], decision='no_merge')).status_code == 200
    assert finalize(client, workflow_run).status_code == 202
    run_one(client)
    assert any(c['member_count'] == 2 for c in client.get(url + '/clusters').json()['items'])


def test_bulk_preview_token_rejects_changed_scope_without_writes(client, workflow_run):
    from tests.test_mdm.test_pair_review_redesign import seed_pairs
    url = seed_pairs(client, workflow_run)
    state = read(client, workflow_run)
    body = request(state['workflow_revision'], expected_decision_revision=state['decision_revision'],
                   decision='deny', scope={'filter': {'group': 'A'}})
    preview = client.post(url + '/bulk-decisions/preview', json=body).json()
    assert len(preview['preview_token']) == 64
    assert preview['new_negative_links'] == 1  # Duplicate candidate endpoints produce one cannot-link.
    with client.session_factory.begin() as db:
        changed = db.get(WorkflowPair, 'pair-0000')
        changed.decision = 'ACCEPTED'
        changed.revision += 1
    response = client.post(url + '/bulk-decisions', json={**body, 'preview_token': preview['preview_token']})
    assert response.status_code == 409
    assert response.json()['detail']['code'] == 'BULK_PREVIEW_CHANGED'
    with client.session_factory() as db:
        assert db.query(ResolutionNegativeEdge).filter_by(run_id=workflow_run, active=True).count() == 0
        assert db.query(WorkflowOperation).filter_by(run_id=workflow_run, operation_type='BULK_DECISIONS').count() == 0


def test_receipt_and_pair_detail_do_not_expose_other_users_runs(client, workflow_run):
    from masterone.mdm.models import ResolutionRun
    ready_decisions(client, workflow_run)
    url = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    pair = client.get(url + '/pairs').json()['items'][0]
    with client.session_factory.begin() as db:
        db.get(ResolutionRun, workflow_run).owner_user_id = 'different-owner'
    for suffix in ('/requests/not-sent', f"/pairs/{pair['pair_id']}", '/conflicts'):
        assert client.get(url + suffix).status_code == 404


def test_partial_repair_preview_does_not_show_deferred_region_as_resolved():
    from masterone.mdm import workflow_resolution
    from masterone.mdm.engine_adapter import engine_context

    from tests.test_mdm.test_decision_semantics import record
    from tests.test_mdm.test_safe_clustering import edge
    preview = getattr(workflow_resolution, 'repair_preview_groups', None)
    assert callable(preview), 'Repair previews must use the same partial isolation as actual publication'
    a, b, x, y, z = [record(identifier) for identifier in ('a', 'b', 'x', 'y', 'z')]
    pairs = [edge(a, b), edge(x, y)]
    source = dict(records=[a, b, x, y, z], pairs=pairs,
                  overlays={pairs[0]['id']: 'DEFERRED', pairs[1]['id']: 'ACCEPTED'},
                  negative_pairs=[], policy_version='person-v3', allow_partial=True)
    with engine_context(dict(decision_semantics_v2_enabled=True, cluster_safety_v2_enabled=True,
                             generation_staging_enabled=False)):
        result, groups = preview(source, 'preview-run', 2)
    assert result['unresolved_record_ids'] == ['a', 'b']
    assert sorted(g['member_record_ids'] for g in groups) == [['x', 'y'], ['z']]


def test_reassessment_invalidates_partial_generation_even_without_decision_change(client, workflow_run):
    ready_decisions(client, workflow_run)
    url = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    for pair in client.get(url + '/pairs').json()['items']:
        state = read(client, workflow_run)
        decision = 'defer' if pair['baseline']['decision'] == 'MERGEABLE' else 'no_merge'
        assert client.post(url + f"/pairs/{pair['pair_id']}/decision", json=request(state['workflow_revision'],
            expected_pair_revision=pair['revision'], decision=decision)).status_code == 200
    state = read(client, workflow_run)
    assert client.post(url + '/finalize-decisions', json=request(state['workflow_revision'],
        expected_decision_revision=state['decision_revision'], allow_partial=True)).status_code == 202
    run_one(client)
    before = read(client, workflow_run)
    assert before['cluster_result']['publication_status'] == 'PARTIAL'
    assert not before['cluster_result']['read_only']
    published_groups = client.get(url + '/clusters').json()['items']
    cid = published_groups[0]['cluster_id']
    first = client.get(url + '/unresolved-records?limit=1').json()
    assert first['total'] == 2
    assert len(first['items']) == 1
    second = client.get(url + '/unresolved-records?limit=1&cursor=' + first['next_cursor']).json()
    unresolved = {record['record_id'] for record in first['items'] + second['items']}
    assert len(unresolved) == 2
    assert not unresolved.intersection(rid for group in published_groups for rid in group['member_record_ids'])
    assert client.post(url + '/reassess', json=request(before['workflow_revision'])).status_code == 202
    run_one(client)
    after = read(client, workflow_run)
    assert after['decision_revision'] == before['decision_revision']
    assert after['cluster_result']['stale'] is True
    assert after['cluster_result']['read_only'] is True
    assert 'confirm-golden' not in after['allowed_actions']
    assert client.get(url + '/clusters').status_code == 200
    body = request(after['workflow_revision'], expected_cluster_revision=after['cluster_revision'],
                   expected_golden_revision=after['golden_revision'])
    assert client.post(url + f'/clusters/{cid}/golden/confirm', json=body).status_code == 409


def test_golden_page_uses_one_bounded_generation_summary(client, workflow_run):
    from sqlalchemy import event
    url = published(client, workflow_run)
    statements = []
    engine = client.session_factory.kw['bind']
    def capture(_conn, _cursor, statement, *_):
        statements.append(statement.lower())
    event.listen(engine, 'before_cursor_execute', capture)
    try:
        response = client.get(url + '/golden')
    finally:
        event.remove(engine, 'before_cursor_execute', capture)
    assert response.status_code == 200
    for item in response.json()['items']:
        assert 'unresolved_record_ids' not in item['generation']
        assert 'unresolved_pair_ids' not in item['generation']
    assert sum('from mdm_workflow_artifacts' in statement for statement in statements) <= 2
    state = read(client, workflow_run)
    assert 'unresolved_record_ids' not in state['latest_cluster_operation']['result']


def test_repeat_deny_does_not_create_fake_provenance_for_legacy_constraint(client, workflow_run):
    from masterone.mdm.models import ResolutionAuditEvent, ResolutionRun
    ready_decisions(client, workflow_run)
    url = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    with client.session_factory.begin() as db:
        pair = db.query(WorkflowPair).filter_by(run_id=workflow_run).first()
        pair.decision = 'NON_MATCH'
        pair.revision += 1
        identifier, revision = pair.id, pair.revision
        ids = sorted([pair.baseline['left_id'], pair.baseline['right_id']])
        owner = db.get(ResolutionRun, workflow_run).owner_user_id
        db.add(ResolutionNegativeEdge(run_id=workflow_run, left_record_id=ids[0], right_record_id=ids[1],
            created_by=owner, reason='STEWARD_REJECT', active=True))
        db.add(ResolutionAuditEvent(run_id=workflow_run, actor_user_id=owner,
            action='WORKFLOW_PAIR_NON_MATCH', details={'pair_id': identifier, 'pair_revision': revision}))
    state = read(client, workflow_run)
    repeat = client.post(url + f'/pairs/{identifier}/decision', json=request(state['workflow_revision'],
        expected_pair_revision=revision, decision='deny'))
    assert repeat.status_code == 409
    assert repeat.json()['detail']['code'] == 'AMENDMENT_REQUIRED'
    state = read(client, workflow_run)
    response = client.post(url + f'/pairs/{identifier}/amend', json=request(state['workflow_revision'],
        expected_pair_revision=revision, decision='defer', reason='Correct old Deny'))
    assert response.status_code == 409
    assert response.json()['detail']['code'] == 'DENY_SOURCE_UNKNOWN'


def test_new_optional_fields_keep_existing_operation_receipts_replayable(client, workflow_run):
    from masterone.mdm.generation_staging import digest
    ready_decisions(client, workflow_run)
    url = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    state = read(client, workflow_run)
    response = client.post(url + '/bulk-decisions', json=request(state['workflow_revision'],
        expected_decision_revision=state['decision_revision'], decision='no_merge', scope={'filter': {}}))
    assert response.status_code == 202
    run_one(client)
    with client.session_factory.begin() as db:
        op = db.get(WorkflowOperation, response.json()['operation_id'])
        op.payload = {k: v for k, v in op.payload.items() if k != 'preview_token'}
        op.payload_hash = digest(op.payload)
        old_bulk_request = deepcopy(op.payload)
    replay = client.post(url + '/bulk-decisions', json=old_bulk_request)
    assert replay.status_code == 202, replay.text
    assert replay.json()['operation_id'] == response.json()['operation_id']
    assert finalize(client, workflow_run).status_code == 202
    run_one(client)
    with client.session_factory.begin() as db:
        op = db.query(WorkflowOperation).filter_by(run_id=workflow_run, operation_type='CLUSTER').one()
        op.payload = {k: v for k, v in op.payload.items() if k != 'allow_partial'}
        op.payload_hash = digest(op.payload)
        old_cluster_request = deepcopy(op.payload)
        identifier = op.id
    replay = client.post(url + '/finalize-decisions', json=old_cluster_request)
    assert replay.status_code == 202, replay.text
    assert replay.json()['operation_id'] == identifier
