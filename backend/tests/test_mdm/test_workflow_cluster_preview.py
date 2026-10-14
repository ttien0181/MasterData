import pytest
from masterone.mdm.workflow_models import WorkflowCluster, WorkflowOperation

from tests.test_mdm.test_workflow_contract import request
from tests.test_mdm.test_workflow_finalize import complete_reviews
from tests.test_mdm.test_workflow_stages import read, ready_decisions, run_one


def preview(client, run_id, partial=False):
    state = read(client, run_id)
    return client.post(f'/api/v1/mdm/runs/{run_id}/workflow/cluster-preview', json=request(
        state['workflow_revision'], expected_decision_revision=state['decision_revision'], allow_partial=partial))


def test_preview_is_async_and_never_publishes_or_changes_pair_decisions(client, workflow_run):
    ready_decisions(client, workflow_run)
    complete_reviews(client, workflow_run)
    before = read(client, workflow_run)
    response = preview(client, workflow_run)
    assert response.status_code == 202, response.text
    run_one(client)
    url = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    receipt = client.get(url + '/operations/' + response.json()['operation_id']).json()
    assert receipt['status'] == 'COMPLETED'
    assert receipt['result']['publication']['total_records'] == 3
    assert receipt['result']['publication']['covered_records'] == 3
    assert receipt['result']['source_decision_revision'] == before['decision_revision']
    after = read(client, workflow_run)
    assert after['decision_revision'] == before['decision_revision']
    assert after['cluster_revision'] == 0
    assert after['workflow_state'] == 'DECIDE_REVIEW'
    with client.session_factory() as db:
        assert db.query(WorkflowCluster).count() == 0


def test_preview_receipt_fences_publication_and_reuses_exact_snapshot(client, workflow_run):
    ready_decisions(client, workflow_run)
    complete_reviews(client, workflow_run)
    response = preview(client, workflow_run)
    assert response.status_code == 202
    run_one(client)
    url = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    receipt = client.get(url + '/operations/' + response.json()['operation_id']).json()
    state = read(client, workflow_run)
    body = request(state['workflow_revision'], expected_decision_revision=state['decision_revision'],
                   preview_operation_id=receipt['operation_id'], preview_token=receipt['result']['preview_token'])
    wrong = client.post(url + '/finalize-decisions', json={**body, 'preview_token': '0' * 64})
    assert wrong.status_code == 409
    accepted = client.post(url + '/finalize-decisions', json=body)
    assert accepted.status_code == 202, accepted.text
    assert client.post(url + '/finalize-decisions', json=body).json() == accepted.json()
    run_one(client)
    final = read(client, workflow_run)['cluster_result']
    for key in ('covered_records', 'unresolved_records', 'multi_record_groups', 'singletons'):
        assert final[key] == receipt['result']['publication'][key]


def test_changed_decision_invalidates_preview_even_with_current_request_revision(client, workflow_run):
    ready_decisions(client, workflow_run)
    complete_reviews(client, workflow_run)
    response = preview(client, workflow_run)
    assert response.status_code == 202
    run_one(client)
    url = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    receipt = client.get(url + '/operations/' + response.json()['operation_id']).json()
    pair = client.get(url + '/pairs').json()['items'][0]
    state = read(client, workflow_run)
    changed = client.post(url + f"/pairs/{pair['pair_id']}/amend", json=request(state['workflow_revision'],
        expected_pair_revision=pair['revision'], decision='no_merge', reason='Review correction'))
    assert changed.status_code == 200, changed.text
    state = read(client, workflow_run)
    result = client.post(url + '/finalize-decisions', json=request(state['workflow_revision'],
        expected_decision_revision=state['decision_revision'], preview_operation_id=receipt['operation_id'],
        preview_token=receipt['result']['preview_token']))
    assert result.status_code == 409
    assert result.json()['detail']['code'] == 'CLUSTER_PREVIEW_STALE'


def test_partial_preview_explains_pending_regions(client, workflow_run):
    ready_decisions(client, workflow_run)
    from masterone.mdm.workflow_models import WorkflowPair
    with client.session_factory.begin() as db:
        db.query(WorkflowPair).filter_by(run_id=workflow_run).first().decision = 'DEFERRED'
    response = preview(client, workflow_run, partial=True)
    assert response.status_code == 202
    run_one(client)
    op = client.get(f'/api/v1/mdm/runs/{workflow_run}/workflow/operations/' + response.json()['operation_id']).json()
    result = op['result']
    assert result['publication']['unresolved_records'] > 0
    assert result['pending_regions'][0]['pending_pair_ids']
    assert result['pending_regions'][0]['record_count'] >= 2


def test_finalize_handler_only_queues_snapshot_preparation(client, workflow_run, monkeypatch):
    ready_decisions(client, workflow_run)
    complete_reviews(client, workflow_run)
    from masterone.mdm import workflow_finalize
    monkeypatch.setattr(workflow_finalize, 'record_payload', lambda _: (_ for _ in ()).throw(
        AssertionError('HTTP finalize must not hydrate records')))
    state = read(client, workflow_run)
    response = client.post(f'/api/v1/mdm/runs/{workflow_run}/workflow/finalize-decisions', json=request(
        state['workflow_revision'], expected_decision_revision=state['decision_revision']))
    assert response.status_code == 202, response.text
    with client.session_factory() as db:
        assert db.get(WorkflowOperation, response.json()['operation_id']).artifact_id is None


def test_nonassessed_preview_cannot_publish_after_raw_input_change(client, workflow_run, monkeypatch):
    from masterone.config import get_settings
    from masterone.mdm.models import ResolutionRecord
    monkeypatch.setattr(get_settings(), 'pair_assessment_auto_enabled', False)
    ready_decisions(client, workflow_run)
    complete_reviews(client, workflow_run)
    response = preview(client, workflow_run)
    assert response.status_code == 202
    run_one(client)
    url = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    receipt = client.get(url + '/operations/' + response.json()['operation_id']).json()
    with client.session_factory.begin() as db:
        record = db.query(ResolutionRecord).filter_by(run_id=workflow_run).first()
        record.normalized_fields = {**record.normalized_fields, 'name': 'Changed source input'}
    state = read(client, workflow_run)
    submitted = client.post(url + '/finalize-decisions', json=request(state['workflow_revision'],
        expected_decision_revision=state['decision_revision'], preview_operation_id=receipt['operation_id'],
        preview_token=receipt['result']['preview_token']))
    assert submitted.status_code == 202  # Heavy input validation belongs to the worker.
    import pytest
    with pytest.raises(ValueError, match='WORKFLOW_INPUT_CHANGED'):
        run_one(client)
    with client.session_factory() as db:
        assert db.query(WorkflowCluster).count() == 0


def test_confirmation_reuses_sealed_membership_without_recomputing(client, workflow_run, monkeypatch):
    ready_decisions(client, workflow_run)
    complete_reviews(client, workflow_run)
    response = preview(client, workflow_run)
    run_one(client)
    url = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    receipt = client.get(url + '/operations/' + response.json()['operation_id']).json()
    from masterone.mdm import workflow_finalize
    monkeypatch.setattr(workflow_finalize, 'compute_publication', lambda *_: (_ for _ in ()).throw(
        AssertionError('Confirmation must publish the membership already reviewed')))
    state = read(client, workflow_run)
    submitted = client.post(url + '/finalize-decisions', json=request(
        state['workflow_revision'], expected_decision_revision=state['decision_revision'],
        preview_operation_id=receipt['operation_id'], preview_token=receipt['result']['preview_token']))
    assert submitted.status_code == 202, submitted.text
    run_one(client)
    actual = client.get(url + '/clusters').json()['items']
    expected = receipt['result']['groups']
    assert sorted(c['member_record_ids'] for c in actual) == sorted(c['member_record_ids'] for c in expected)
    operation = client.get(url + '/operations/' + submitted.json()['operation_id']).json()
    assert operation['result']['performance']['plan_reused'] is True


def test_corrupt_publication_plan_cannot_write_any_group(client, workflow_run):
    import pytest
    from masterone.mdm.workflow_models import WorkflowArtifact
    from sqlalchemy import update
    ready_decisions(client, workflow_run)
    complete_reviews(client, workflow_run)
    response = preview(client, workflow_run)
    run_one(client)
    url = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    receipt = client.get(url + '/operations/' + response.json()['operation_id']).json()
    assert receipt['result'].get('plan_artifact_id'), 'Preview must seal its complete result'
    with client.session_factory.begin() as db:
        plan = db.get(WorkflowArtifact, receipt['result']['plan_artifact_id'])
        db.execute(update(WorkflowArtifact).where(WorkflowArtifact.id == plan.id).values(
            payload={**plan.payload, 'target_cluster_revision': -1}))
    state = read(client, workflow_run)
    submitted = client.post(url + '/finalize-decisions', json=request(
        state['workflow_revision'], expected_decision_revision=state['decision_revision'],
        preview_operation_id=receipt['operation_id'], preview_token=receipt['result']['preview_token']))
    assert submitted.status_code == 202
    with pytest.raises(ValueError, match='WORKFLOW_ARTIFACT_INVALID'):
        run_one(client)
    with client.session_factory() as db:
        assert db.query(WorkflowCluster).count() == 0


@pytest.mark.parametrize('decision', ['no_merge', 'deny'])
def test_single_decision_during_preview_keeps_result_stale_and_unpublishable(client, workflow_run, monkeypatch, decision):
    from masterone.mdm import workflow_finalize
    ready_decisions(client, workflow_run)
    complete_reviews(client, workflow_run)
    response = preview(client, workflow_run)
    url = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    compute = workflow_finalize.compute_publication

    def compute_while_reviewing(*args):
        state = read(client, workflow_run)
        assert 'decide-pair' in state['allowed_actions']
        pair = client.get(url + '/pairs').json()['items'][0]
        changed = client.post(url + f"/pairs/{pair['pair_id']}/decision", json=request(
            state['workflow_revision'], expected_pair_revision=pair['revision'], decision=decision))
        assert changed.status_code == 200, changed.text
        return compute(*args)

    monkeypatch.setattr(workflow_finalize, 'compute_publication', compute_while_reviewing)
    run_one(client)
    receipt = client.get(url + '/operations/' + response.json()['operation_id']).json()
    assert receipt['status'] == 'COMPLETED'
    state = read(client, workflow_run)
    assert receipt['result']['source_decision_revision'] < state['decision_revision']
    rejected = client.post(url + '/finalize-decisions', json=request(
        state['workflow_revision'], expected_decision_revision=state['decision_revision'],
        preview_operation_id=receipt['operation_id'], preview_token=receipt['result']['preview_token']))
    assert rejected.status_code == 409
    assert rejected.json()['detail']['code'] == 'CLUSTER_PREVIEW_STALE'


def test_preview_pages_include_readable_members_and_do_not_recompute(client, workflow_run, monkeypatch):
    ready_decisions(client, workflow_run)
    complete_reviews(client, workflow_run)
    response = preview(client, workflow_run)
    run_one(client)
    op_id = response.json()['operation_id']
    url = f'/api/v1/mdm/runs/{workflow_run}/workflow/cluster-preview/{op_id}/groups'
    monkeypatch.setattr('masterone.mdm.workflow_finalize.compute_publication', lambda *_: (_ for _ in ()).throw(
        AssertionError('Paging must read the sealed result')))
    first = client.get(url, params={'limit': 1})
    assert first.status_code == 200, first.text
    page = first.json()
    assert page['total'] == 2 and page['next_cursor'] == '1'
    assert page['items'][0]['preview_members'][0]['name'] == 'Nguyen Van An'
    assert page['items'][0]['display_name'] == 'Nguyen Van An'
    second = client.get(url, params={'limit': 1, 'cursor': page['next_cursor']}).json()
    assert second['next_cursor'] is None
    assert second['items'][0]['member_record_ids'] != page['items'][0]['member_record_ids']


def test_full_preview_never_counts_pending_endpoints_as_complete_singletons(client, workflow_run):
    from masterone.mdm.workflow_models import WorkflowPair
    ready_decisions(client, workflow_run)
    with client.session_factory.begin() as db:
        pair = db.query(WorkflowPair).filter_by(run_id=workflow_run).first()
        pair.assessment = {**pair.baseline, 'decision': 'REVIEW', 'triage_status': 'REVIEW'}
        pending = {pair.baseline['left_id'], pair.baseline['right_id']}
    response = preview(client, workflow_run)
    run_one(client)
    result = client.get(f'/api/v1/mdm/runs/{workflow_run}/workflow/operations/' +
                        response.json()['operation_id']).json()['result']
    assert result['publication']['publication_status'] == 'PARTIAL'
    assert result['publication']['unresolved_records'] >= len(pending)
    assert all(not pending.intersection(g['member_record_ids']) for g in result['groups'])


def test_optional_annotation_never_runs_in_base_publication(client, workflow_run, monkeypatch):
    from masterone.config import get_settings
    monkeypatch.setattr(get_settings(), 'llm_enabled', True)
    monkeypatch.setattr(get_settings(), 'semantic_address_enabled', True)
    ready_decisions(client, workflow_run)
    complete_reviews(client, workflow_run)
    monkeypatch.setattr('masterone.mdm.pipeline.semantic_address.run_annotations', lambda *_args, **_kw: (
        _ for _ in ()).throw(AssertionError('Optional service cannot delay or fail base group publication')))
    state = read(client, workflow_run)
    queued = client.post(f'/api/v1/mdm/runs/{workflow_run}/workflow/finalize-decisions', json=request(
        state['workflow_revision'], expected_decision_revision=state['decision_revision']))
    assert queued.status_code == 202
    run_one(client)
    assert read(client, workflow_run)['cluster_revision'] == 1
