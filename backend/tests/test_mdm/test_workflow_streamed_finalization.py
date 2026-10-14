"""Real queued finalization against a frozen Parquet generation fixture."""
import pytest
from masterone.mdm.job_queue import claim_next, ownership
from masterone.mdm.models import ResolutionRecord, ResolutionRun, ResolutionTask
from masterone.mdm.workflow_models import WorkflowArtifact, WorkflowGeneration

from tests.test_mdm.test_workflow_golden import golden_request
from tests.test_mdm.test_workflow_stages import read
from tests.test_mdm.test_workflow_streamed_worker import ready_streamed_golden


def queue_fixture(client, run_id):
    ready_streamed_golden(client, run_id)
    root = f'/api/v1/mdm/runs/{run_id}/workflow'
    assert client.post(root + '/golden/confirm-selected', json=golden_request(client, run_id)).status_code == 200
    with client.session_factory.begin() as db:
        run = db.get(ResolutionRun, run_id)
        generation = db.query(WorkflowGeneration).filter_by(run_id=run_id, revision=run.cluster_revision).one()
        gid = generation.id
    body = golden_request(client, run_id)
    body.pop('expected_cluster_revision')
    response = client.post(root + '/finalize-golden', json=body)
    assert response.status_code == 202, response.text
    oid = response.json()['operation_id']
    claim = claim_next(client.session_factory, 'streamed-finalization-test', lease_seconds=300)
    assert claim
    with client.session_factory() as db:
        assert db.get(ResolutionTask, claim.task_id).operation_id == oid
    return claim, oid, gid


def test_queued_finalization_writes_receipt_and_exports_owned_frozen_sources(client, workflow_run):
    import csv
    from io import StringIO

    from masterone.mdm.workflow_jobs import execute_workflow
    claim, oid, gid = queue_fixture(client, workflow_run)
    with ownership(claim):
        execute_workflow(oid, client.session_factory, lambda *_: None)
    assert read(client, workflow_run)['workflow_state'] == 'FINALIZED'
    with client.session_factory() as db:
        result = db.get(WorkflowGeneration, gid).finalized_result
        assert result['version'] == 'p119_workflow_final_v2'
        assert 'clusters' not in result and 'records' not in result
    base = f'/api/v1/mdm/runs/{workflow_run}'
    preview = client.post(base + '/previews', json={'choices': {}, 'acknowledge_unreviewed': False})
    assert preview.status_code == 200, preview.text
    exported = client.post(base + '/previews/' + preview.json()['preview_id'] + '/execute',
        json={'execute_request_id': 'new-final-receipt-export'})
    assert exported.status_code == 200, exported.text
    rows = list(csv.DictReader(StringIO(exported.content.decode('utf-8-sig'))))
    ids = [rid for row in rows for rid in row['source_record_ids'].split(';')]
    assert len(ids) == len(set(ids)) == 3


def test_queued_finalization_rejects_input_changed_since_generation(client, workflow_run):
    from masterone.mdm.workflow_jobs import execute_workflow
    claim, oid, gid = queue_fixture(client, workflow_run)
    with client.session_factory.begin() as db:
        source = db.query(ResolutionRecord).filter_by(run_id=workflow_run).first()
        source.raw_fields = {**source.raw_fields, 'name': 'changed after generation'}
    with ownership(claim), pytest.raises(ValueError, match='WORKFLOW_INPUT_CHANGED|ASSESSMENT_INPUT_CHANGED'):
        execute_workflow(oid, client.session_factory, lambda *_: None)
    with client.session_factory() as db:
        assert not db.get(WorkflowGeneration, gid).finalized_result
        assert db.get(ResolutionRun, workflow_run).workflow_state != 'FINALIZED'


def test_queued_finalization_pins_golden_revision_at_request_time(client, workflow_run):
    from masterone.mdm.workflow_jobs import execute_workflow
    claim, oid, gid = queue_fixture(client, workflow_run)
    with client.session_factory.begin() as db:
        db.get(ResolutionRun, workflow_run).golden_revision += 1
    with ownership(claim), pytest.raises(ValueError, match='WORKFLOW_REVISION_CHANGED'):
        execute_workflow(oid, client.session_factory, lambda *_: None)
    with client.session_factory() as db:
        assert not db.get(WorkflowGeneration, gid).finalized_result


def test_change_after_final_partition_seal_cannot_register_finalized_generation(client, workflow_run, monkeypatch):
    from masterone.mdm import workflow_snapshot_storage
    from masterone.mdm.workflow_jobs import execute_workflow
    claim, oid, gid = queue_fixture(client, workflow_run)
    original = workflow_snapshot_storage.seal_snapshot
    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        if args[2] == 'FINAL_GOLDEN':
            with client.session_factory.begin() as db:
                source = db.query(ResolutionRecord).filter_by(run_id=workflow_run).first()
                source.raw_fields = {**source.raw_fields, 'name': 'changed during upload'}
        return result
    monkeypatch.setattr(workflow_snapshot_storage, 'seal_snapshot', changed)
    with ownership(claim), pytest.raises(ValueError, match='WORKFLOW_INPUT_CHANGED|ASSESSMENT_INPUT_CHANGED'):
        execute_workflow(oid, client.session_factory, lambda *_: None)
    with client.session_factory() as db:
        assert not db.get(WorkflowGeneration, gid).finalized_result


def test_export_rejects_a_modified_final_partition(client, workflow_run):
    from masterone.mdm.workflow_jobs import execute_workflow
    from masterone.mdm.workflow_snapshot_storage import workspace
    claim, oid, gid = queue_fixture(client, workflow_run)
    with ownership(claim):
        execute_workflow(oid, client.session_factory, lambda *_: None)
    base = f'/api/v1/mdm/runs/{workflow_run}'
    preview = client.post(base + '/previews', json={'choices': {}, 'acknowledge_unreviewed': False})
    assert preview.status_code == 200, preview.text
    with client.session_factory() as db:
        result = db.get(WorkflowGeneration, gid).finalized_result
        artifact = db.get(WorkflowArtifact, result['artifact_id'])
        from masterone.storage.artifact import ArtifactManifest, DatasetManifest
        descriptor = artifact.payload['streams']['groups']
        identity = ArtifactManifest.model_validate({k:v for k,v in descriptor.items() if k in ArtifactManifest.model_fields})
        manifest = DatasetManifest(workspace(artifact.id) / 'groups', identity)
        path = manifest.path(identity.partitions[0])
    data = bytearray(path.read_bytes())
    data[10] ^= 1
    import stat
    path.chmod(stat.S_IWRITE | stat.S_IREAD)
    path.write_bytes(data)
    response = client.post(base + '/previews/' + preview.json()['preview_id'] + '/execute',
        json={'execute_request_id': 'tampered-final-export'})
    assert response.status_code == 409, response.text


def test_finalization_rejects_two_groups_using_one_entity_id(client, workflow_run):
    from fastapi import HTTPException
    from masterone.mdm.workflow_generation_reads import GenerationReader
    from masterone.mdm.workflow_jobs import execute_workflow
    from masterone.mdm.workflow_models import WorkflowCluster
    claim, oid, gid = queue_fixture(client, workflow_run)
    with client.session_factory.begin() as db:
        reader = GenerationReader(db, db.get(ResolutionRun, workflow_run), db.get(WorkflowGeneration, gid))
        single_id = next(row.payload['entity_id'] for row in reader.rows() if row.payload['singleton'])
        multi = db.query(WorkflowCluster).filter_by(generation_id=gid).first()
        multi.payload = {**multi.payload, 'entity_id': single_id}
    with ownership(claim), pytest.raises(HTTPException) as error:
        execute_workflow(oid, client.session_factory, lambda *_: None)
    assert error.value.detail['code'] == 'WORKFLOW_ENTITY_IDENTITY_INVALID'
    with client.session_factory() as db:
        assert not db.get(WorkflowGeneration, gid).finalized_result
