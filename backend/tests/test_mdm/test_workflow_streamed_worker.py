"""Leased CLUSTER worker consumes a sealed Parquet decision universe."""
from masterone.mdm.job_queue import claim_next, ownership
from masterone.mdm.models import ResolutionRun
from masterone.mdm.workflow_models import WorkflowGeneration, WorkflowOperation

from tests.test_mdm.test_workflow_finalize import complete_reviews, finalize
from tests.test_mdm.test_workflow_golden import golden_request
from tests.test_mdm.test_workflow_stages import read, ready_decisions, run_one


def ready_streamed_golden(client, workflow_run):
    from masterone.mdm.workflow_jobs import execute_workflow
    from masterone.mdm.workflow_snapshot_producers import snapshot_streams
    from masterone.mdm.workflow_snapshot_storage import seal_snapshot
    ready_decisions(client, workflow_run)
    complete_reviews(client, workflow_run)
    queued = finalize(client, workflow_run)
    assert queued.status_code == 202
    oid = queued.json()['operation_id']
    claim = claim_next(client.session_factory, 'streamed-cluster-test', lease_seconds=300)
    assert claim
    with ownership(claim):
        with client.session_factory() as db:
            metadata, streams = snapshot_streams(db, db.get(ResolutionRun, workflow_run))
            aid = seal_snapshot(client.session_factory, workflow_run, 'DECISION_SNAPSHOT', metadata, streams, operation_id=oid)
        with client.session_factory.begin() as db:
            db.get(WorkflowOperation, oid).artifact_id = aid
        execute_workflow(oid, client.session_factory, lambda *_: None)
    with client.session_factory() as db:
        run = db.get(ResolutionRun, workflow_run)
        generation = db.query(WorkflowGeneration).filter_by(run_id=workflow_run, revision=run.cluster_revision).one()
        assert generation.storage_version == 'workflow_parquet_v2'
    return client.get(f'/api/v1/mdm/runs/{workflow_run}/workflow/clusters').json()['items']


def test_leased_streamed_cluster_then_finalization_and_export(client, workflow_run):
    ready_streamed_golden(client, workflow_run)
    root = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    groups = client.get(root + '/clusters')
    assert groups.status_code == 200, groups.text
    assert sum(g['member_count'] for g in groups.json()['items']) == 3
    assert client.post(root + '/golden/confirm-selected', json=golden_request(client, workflow_run)).status_code == 200
    state = read(client, workflow_run)
    from tests.test_mdm.test_workflow_contract import request
    response = client.post(root + '/finalize-golden', json=request(state['workflow_revision'], expected_golden_revision=state['golden_revision']))
    assert response.status_code == 202, response.text
    run_one(client)
    assert read(client, workflow_run)['workflow_state'] == 'FINALIZED'
    base = f'/api/v1/mdm/runs/{workflow_run}'
    preview = client.post(base + '/previews', json={'choices': {}, 'acknowledge_unreviewed': False})
    assert preview.status_code == 200, preview.text
    exported = client.post(base + '/previews/' + preview.json()['preview_id'] + '/execute', json={'execute_request_id': 'streamed-worker-export'})
    assert exported.status_code == 200, exported.text


def ready_streamed_preview(client, workflow_run, *, complete=True):
    from masterone.mdm.workflow_jobs import execute_workflow
    from masterone.mdm.workflow_snapshot_producers import snapshot_streams
    from masterone.mdm.workflow_snapshot_storage import seal_snapshot

    from tests.test_mdm.test_workflow_contract import request
    ready_decisions(client, workflow_run)
    if complete:
        complete_reviews(client, workflow_run)
    else:
        from masterone.mdm.workflow_models import WorkflowPair
        with client.session_factory.begin() as db:
            row = db.query(WorkflowPair).filter_by(run_id=workflow_run).first()
            row.baseline = {**row.baseline, 'decision': 'MERGEABLE', 'triage_status': 'REVIEW'}
    root = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    state = read(client, workflow_run)
    queued = client.post(root + '/cluster-preview', json=request(state['workflow_revision'], expected_decision_revision=state['decision_revision'], allow_partial=not complete))
    assert queued.status_code == 202, queued.text
    oid = queued.json()['operation_id']
    claim = claim_next(client.session_factory, 'streamed-preview-test', lease_seconds=300)
    assert claim
    with ownership(claim):
        with client.session_factory() as db:
            metadata, streams = snapshot_streams(db, db.get(ResolutionRun, workflow_run), allow_partial=not complete)
            aid = seal_snapshot(client.session_factory, workflow_run, 'DECISION_SNAPSHOT', metadata, streams, operation_id=oid)
        with client.session_factory.begin() as db:
            db.get(WorkflowOperation, oid).artifact_id = aid
        execute_workflow(oid, client.session_factory, lambda *_: None)
    preview = client.get(root + '/operations/' + oid).json()
    return preview


def test_streamed_preview_pages_and_publication_keep_same_memberships_and_entities(client, workflow_run):
    from tests.test_mdm.test_workflow_contract import request
    preview = ready_streamed_preview(client, workflow_run)
    oid = preview['operation_id']
    root = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    page = client.get(root + '/cluster-preview/' + oid + '/groups?limit=1&cursor=1')
    assert page.status_code == 200, page.text
    assert page.json()['total'] == 2
    assert len(page.json()['items']) == 1
    planned = {tuple(g['member_record_ids']): g['entity_id'] for g in preview['result']['groups']}
    state = read(client, workflow_run)
    queued = client.post(root + '/finalize-decisions', json=request(state['workflow_revision'],
        expected_decision_revision=state['decision_revision'], preview_operation_id=oid, preview_token=preview['result']['preview_token']))
    assert queued.status_code == 202, queued.text
    run_one(client)
    published = client.get(root + '/clusters').json()['items']
    assert {tuple(g['member_record_ids']): g['entity_id'] for g in published} == planned


def test_streamed_publication_rejects_a_modified_preview_group_partition(client, workflow_run):
    import stat

    import pytest
    from masterone.mdm.workflow_models import WorkflowArtifact
    from masterone.mdm.workflow_snapshot_storage import workspace
    from masterone.storage.artifact import ArtifactManifest, DatasetManifest

    from tests.test_mdm.test_workflow_contract import request
    preview = ready_streamed_preview(client, workflow_run)
    with client.session_factory() as db:
        artifact = db.get(WorkflowArtifact, preview['result']['plan_artifact_id'])
        descriptor = artifact.payload['streams']['groups']
        identity = ArtifactManifest.model_validate({k:v for k,v in descriptor.items() if k in ArtifactManifest.model_fields})
        path = DatasetManifest(workspace(artifact.id) / 'groups', identity).path(identity.partitions[0])
    data = bytearray(path.read_bytes())
    data[10] ^= 1
    path.chmod(stat.S_IREAD | stat.S_IWRITE)
    path.write_bytes(data)
    state = read(client, workflow_run)
    root = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    queued = client.post(root + '/finalize-decisions', json=request(state['workflow_revision'],
        expected_decision_revision=state['decision_revision'], preview_operation_id=preview['operation_id'],
        preview_token=preview['result']['preview_token']))
    assert queued.status_code == 202, queued.text
    with pytest.raises(ValueError):
        run_one(client)
    with client.session_factory() as db:
        assert db.get(ResolutionRun, workflow_run).cluster_revision == 0


def test_streamed_preview_exposes_pending_regions_without_publishing_them(client, workflow_run):
    preview = ready_streamed_preview(client, workflow_run, complete=False)
    pending = preview['result']['pending_regions']
    assert pending
    assert pending[0]['record_count'] == 2
    assert pending[0]['pending_pair_count'] >= 1
    assert preview['result']['publication']['unresolved_records'] == 2
    assert preview['result']['publication']['covered_records'] == 1


def test_streamed_preview_pages_survive_locator_cache_loss_and_reject_locator_tampering(client, workflow_run):
    import sqlite3

    import pytest
    from masterone.mdm.workflow_cluster_preview import preview_groups
    from masterone.mdm.workflow_snapshot_storage import workspace
    preview = ready_streamed_preview(client, workflow_run)
    pid = preview['result']['plan_artifact_id']
    with client.session_factory() as db:
        from masterone.mdm.workflow_models import WorkflowArtifact
        source_id = db.get(WorkflowArtifact, pid).payload['metadata']['source_artifact_id']
    for artifact_id in (pid, source_id):
        (workspace(artifact_id) / 'locators.sqlite').unlink()
    with client.session_factory() as db:
        run = db.get(ResolutionRun, workflow_run)
        page = preview_groups(db, run, preview['operation_id'], 1, 1)
        assert page['total'] == 2
        assert page['items'][0]['singleton']
    with sqlite3.connect(str(workspace(pid) / 'locators.sqlite')) as sql:
        sql.execute("DELETE FROM locations WHERE stream='members'")
    with client.session_factory() as db, pytest.raises(ValueError, match='WORKFLOW_INDEX_INVALID'):
        preview_groups(db, db.get(ResolutionRun, workflow_run), preview['operation_id'], 1, 1)


def test_partial_parquet_generation_exposes_every_excluded_record(client, workflow_run):
    from tests.test_mdm.test_workflow_contract import request
    preview = ready_streamed_preview(client, workflow_run, complete=False)
    state = read(client, workflow_run)
    root = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    response = client.post(root + '/finalize-decisions', json=request(state['workflow_revision'],
        expected_decision_revision=state['decision_revision'], allow_partial=True,
        preview_operation_id=preview['operation_id'], preview_token=preview['result']['preview_token']))
    assert response.status_code == 202, response.text
    run_one(client)
    page = client.get(root + '/unresolved-records?limit=1')
    assert page.status_code == 200, page.text
    assert page.json()['total'] == 2
    assert len(page.json()['items']) == 1
    second = client.get(root + '/unresolved-records', params={'limit':1,'cursor':page.json()['next_cursor']})
    assert second.status_code == 200, second.text
    assert second.json()['items'][0]['record_id'] != page.json()['items'][0]['record_id']
