from copy import deepcopy
from uuid import uuid4

import pytest
from masterone.mdm.generation_staging import digest
from masterone.mdm.models import ResolutionRun
from masterone.mdm.workflow_artifacts import seal
from masterone.mdm.workflow_models import WorkflowArtifact, WorkflowOperation
from sqlalchemy import update


def operation(client, run_id):
    with client.session_factory.begin() as db:
        run = db.get(ResolutionRun, run_id)
        row = WorkflowOperation(run_id=run_id, request_id=str(uuid4()), operation_type='SNAPSHOT_QA',
            payload={}, payload_hash=digest({}), actor_user_id=run.owner_user_id, status='COMPLETED')
        db.add(row)
        db.flush()
        return row.id


def record_rows(count):
    for i in range(count):
        yield {'id': f'r{i:07}', 'source_system': 'CRM', 'external_id': str(i),
               'raw_fields': {'name': 'Nguyễn An', 'number': i, 'flag': True, 'missing': None, 'array': ['a', i]},
               'normalized_fields': {'phone': f'+8490{i % 7:07}', 'email': 'a@example.test',
                   'emails': [{'value': 'a@example.test', 'quality': 'VALID'}, {'value': f'{i}@example.test', 'quality': 'VALID'}]},
               'extra_fields': {}, 'quality_flags': [], 'entity_type': 'PERSON'}


def test_manifest_stream_roundtrip_and_scoped_lookup(client, workflow_run, monkeypatch):
    from masterone.config import get_settings
    from masterone.mdm.workflow_snapshot_storage import open_snapshot, seal_snapshot
    from masterone.storage.artifact import DatasetManifest
    oid = operation(client, workflow_run)
    batches = []
    original = DatasetManifest.iter_batches
    def track(self, item, batch_size=512):
        for batch in original(self, item, batch_size):
            batches.append(batch.num_rows)
            yield batch
    monkeypatch.setattr(DatasetManifest, 'iter_batches', track)
    artifact_id = seal_snapshot(client.session_factory, workflow_run, 'DECISION_SNAPSHOT',
        {'profile': {'cluster_safety_v2_enabled': True}}, {'records': record_rows(1100)}, operation_id=oid)
    with client.session_factory() as db:
        row = db.get(WorkflowArtifact, artifact_id)
        assert row.payload['format'] == 'p119_workflow_snapshot_v2'
        assert 'records' not in row.payload
        assert row.payload['operation_id'] == oid
        with open_snapshot(db, artifact_id, workflow_run, 'DECISION_SNAPSHOT') as view:
            assert len(view.records) == 1100
            assert view.record_page(['r0000510', 'r0000512']) == [list(record_rows(513))[i] for i in (510, 512)]
            with pytest.raises(TypeError):
                view.metadata['profile']['cluster_safety_v2_enabled'] = False
            with pytest.raises(TypeError):
                deepcopy(view)
            assert max(batches) <= 2048
            assert view.metrics['source_rows_read'] <= 1024
            assert 'benchmark_entity_id' not in str(row.payload)
    assert get_settings().workflow_artifact_dir


def test_legacy_view_preserves_json_snapshot(client, workflow_run):
    from masterone.mdm.workflow_snapshot_storage import open_snapshot
    records = list(record_rows(3))
    artifact_id = seal(client.session_factory, workflow_run, 'DECISION_SNAPSHOT',
                       {'records': records, 'pairs': [], 'overlays': {}, 'negative_pairs': []})
    with client.session_factory() as db, open_snapshot(db, artifact_id, workflow_run, 'DECISION_SNAPSHOT') as view:
        assert view.records[records[1]['id']] == records[1]
        assert list(view.iter_rows('records')) == records


def test_late_record_cursor_reads_only_remaining_partitions(client, workflow_run):
    from masterone.mdm.workflow_snapshot_storage import open_snapshot, seal_snapshot
    aid = seal_snapshot(client.session_factory, workflow_run, 'SINGLETON_SOURCE', {},
                        {'records': record_rows(1100)}, operation_id=operation(client, workflow_run))
    with client.session_factory() as db, open_snapshot(db, aid, workflow_run, 'SINGLETON_SOURCE') as view:
        assert [r['id'] for r in view.iter_rows_after('records', 'r0001090')] == [f'r{i:07}' for i in range(1091, 1100)]
        assert view.metrics['source_rows_read'] == 76


def test_manifest_row_page_skips_preceding_partitions(client, workflow_run):
    from masterone.mdm.workflow_snapshot_storage import open_snapshot, seal_snapshot
    aid = seal_snapshot(client.session_factory, workflow_run, 'CLUSTER_PLAN', {},
                        {'groups': record_rows(1100)}, operation_id=operation(client, workflow_run))
    with client.session_factory() as db, open_snapshot(db, aid, workflow_run, 'CLUSTER_PLAN') as view:
        page = view.list_page('groups', 1023, 5)
        assert [r['id'] for r in page['items']] == [f'r{i:07}' for i in range(1023, 1028)]
        assert page['total'] == 1100
        assert page['next_cursor'] == '1028'
        assert view.metrics['source_rows_read'] == 588


@pytest.mark.parametrize('field,value', [('run_id', 'other-run'), ('phase', 'CLUSTER'), ('content_hash', '0' * 64)])
def test_manifest_owner_phase_and_hash_fail_closed(client, workflow_run, field, value):
    from masterone.mdm.workflow_snapshot_storage import open_snapshot, seal_snapshot
    oid = operation(client, workflow_run)
    artifact_id = seal_snapshot(client.session_factory, workflow_run, 'DECISION_SNAPSHOT', {},
                                {'records': record_rows(1)}, operation_id=oid)
    with client.session_factory.begin() as db:
        db.execute(update(WorkflowArtifact).where(WorkflowArtifact.id == artifact_id).values({field: value}))
    with client.session_factory() as db, pytest.raises(ValueError):
        with open_snapshot(db, artifact_id, workflow_run, 'DECISION_SNAPSHOT'):
            pass


def test_large_row_budget_and_unowned_operation_never_seal(client, workflow_run):
    from masterone.mdm.workflow_snapshot_storage import seal_snapshot
    oid = operation(client, workflow_run)
    record = next(record_rows(1))
    record['raw_fields']['name'] = 'x' * (17 * 1024**2)
    for owner, data in [('not-an-operation', next(record_rows(1))), (oid, record)]:
        with pytest.raises(ValueError):
            seal_snapshot(client.session_factory, workflow_run, 'DECISION_SNAPSHOT', {},
                          {'records': iter([data])}, operation_id=owner)
    with client.session_factory() as db:
        assert db.query(WorkflowArtifact).filter_by(phase='DECISION_SNAPSHOT').count() == 0


def test_disk_index_frequency_and_oversized_region_remain_complete(client, workflow_run):
    from masterone.mdm.pipeline.matching import value_frequencies
    from masterone.mdm.workflow_cluster_index import SnapshotIndex
    from masterone.mdm.workflow_snapshot_storage import open_snapshot, seal_snapshot
    records = list(record_rows(1100))
    pairs = [{'id': f'p{i:07}', 'left_id': records[i]['id'], 'right_id': records[i+1]['id'],
              'decision': 'REVIEW'} for i in range(1099)]
    artifact_id = seal_snapshot(client.session_factory, workflow_run, 'DECISION_SNAPSHOT', {},
        {'records': iter(records), 'pairs': iter(pairs),
         'overlays': ({'id': p['id'], 'value': 'ACCEPTED'} for p in pairs)}, operation_id=operation(client, workflow_run))
    with client.session_factory() as db, open_snapshot(db, artifact_id, workflow_run, 'DECISION_SNAPSHOT') as view:
        assert view.pairs[tuple(sorted((records[0]['id'], records[1]['id'])))] == pairs[0]
        index = SnapshotIndex(view)
        assert index.storage_kind == 'sqlite'
        assert {field: dict(values) for field, values in index.frequencies.items()} == value_frequencies(records)
        region = index.region([records[0]['id']])
        assert not isinstance(region, (list, set, dict))
        assert len(region) == 1100
        assert records[-1]['id'] in region
        assert region.summary(25)['record_count'] == 1100
        assert len(region.summary(25)['record_ids']) == 25
        assert region.summary(25)['approvable'] is False
        assert index.subset([records[0]['id'], records[1]['id']])['records'] == records[:2]


@pytest.mark.parametrize('fault', ['missing', 'modified', 'schema', 'count', 'locator'])
def test_partition_faults_are_not_readiness(client, workflow_run, fault):
    import sqlite3

    from masterone.mdm.workflow_snapshot_storage import open_snapshot, seal_snapshot, workspace
    oid = operation(client, workflow_run)
    artifact_id = seal_snapshot(client.session_factory, workflow_run, 'DECISION_SNAPSHOT', {},
                                {'records': record_rows(2)}, operation_id=oid)
    with client.session_factory() as db:
        payload = deepcopy(db.get(WorkflowArtifact, artifact_id).payload)
    part = payload['streams']['records']['partitions'][0]
    path = workspace(artifact_id) / 'records' / part['path']
    if fault == 'locator':
        with sqlite3.connect(str(workspace(artifact_id) / 'locators.sqlite')) as connection:
            connection.execute("UPDATE locations SET ordinal=1 WHERE id='r0000000'")
    elif fault == 'missing':
        import os
        os.chmod(path, 0o600)
        path.unlink()
        from masterone.mdm.workflow_snapshot_storage import object_store
        from masterone.storage.artifact import PartitionArtifact
        store = object_store()
        key = PartitionArtifact.model_validate(part).key
        os.chmod(store._path(key), 0o600)
        store.delete(key)
    elif fault == 'modified':
        import os
        os.chmod(path, 0o600)
        path.write_bytes(b'corrupt parquet')
    else:
        if fault == 'schema':
            payload['streams']['records']['schema_version'] = 'arrow_v1:' + '0' * 64
        else:
            payload['streams']['records']['count'] += 1
        with client.session_factory.begin() as db:
            db.execute(update(WorkflowArtifact).where(WorkflowArtifact.id == artifact_id).values(
                payload=payload, content_hash=digest(payload)))
    with client.session_factory() as db, pytest.raises((ValueError, OSError)):
        with open_snapshot(db, artifact_id, workflow_run, 'DECISION_SNAPSHOT') as view:
            view.records['r0000000']


def test_unverified_remote_readback_cannot_publish_manifest(client, workflow_run, monkeypatch, tmp_path):
    from masterone.mdm import workflow_snapshot_storage
    from masterone.storage.object_store import LocalObjectStore
    class BadReadback(LocalObjectStore):
        def iter_chunks(self, key, chunk_size=1024**2):
            yield b'not the uploaded partition'
    monkeypatch.setattr(workflow_snapshot_storage, 'object_store', lambda: BadReadback(tmp_path / 'remote'))
    with pytest.raises(ValueError):
        workflow_snapshot_storage.seal_snapshot(client.session_factory, workflow_run, 'DECISION_SNAPSHOT', {},
            {'records': record_rows(2)}, operation_id=operation(client, workflow_run))
    with client.session_factory() as db:
        assert db.query(WorkflowArtifact).filter_by(phase='DECISION_SNAPSHOT').count() == 0


def test_locator_loss_can_use_verified_ordered_bounds(client, workflow_run):
    from masterone.mdm.workflow_snapshot_storage import open_snapshot, seal_snapshot, workspace
    artifact_id = seal_snapshot(client.session_factory, workflow_run, 'DECISION_SNAPSHOT', {},
                                {'records': record_rows(1100)}, operation_id=operation(client, workflow_run))
    (workspace(artifact_id) / 'locators.sqlite').unlink()
    with client.session_factory() as db, open_snapshot(db, artifact_id, workflow_run, 'DECISION_SNAPSHOT') as view:
        assert view.records['r0000512']['external_id'] == '512'
        assert view.metrics['source_rows_read'] == 512


def test_disk_incident_and_hard_edges_match_memory_index(client, workflow_run):
    from masterone.mdm.workflow_cluster_index import SnapshotIndex
    from masterone.mdm.workflow_snapshot_storage import open_snapshot, seal_snapshot
    records = list(record_rows(4))
    a, b, c, d = [r['id'] for r in records]
    pairs = [dict(id='accepted-hard', left_id=a, right_id=b, decision='BLOCKED'),
             dict(id='kept-separate', left_id=b, right_id=c, decision='MERGEABLE'),
             dict(id='deferred', left_id=c, right_id=d, decision='REVIEW')]
    overlays = {'accepted-hard': 'ACCEPTED', 'kept-separate': 'REJECTED', 'deferred': 'DEFERRED'}
    source = dict(records=records, pairs=pairs, overlays=overlays, negative_pairs=[(a, b)])
    expected = SnapshotIndex(source)
    artifact_id = seal_snapshot(client.session_factory, workflow_run, 'DECISION_SNAPSHOT', {},
        {'records': iter(records), 'pairs': iter(pairs),
         'overlays': ({'id': k, 'value': v} for k, v in overlays.items()),
         'negative_pairs': iter([{'left_id': a, 'right_id': b}])}, operation_id=operation(client, workflow_run))
    with client.session_factory() as db, open_snapshot(db, artifact_id, workflow_run, 'DECISION_SNAPSHOT') as view:
        index = SnapshotIndex(view)
        for rid in [a, b, c, d]:
            assert set(index.region([rid])) == expected.region([rid])
            assert list(index.incident[rid]) == expected.incident[rid]
            assert set(index.adjacency[rid]) == expected.adjacency[rid]
            assert set(index.accepted[rid]) == expected.accepted[rid]
            assert list(index.negative_incident[rid]) == expected.negative_incident[rid]


def test_compressed_oversized_part_rejected_before_decoding(client, workflow_run):
    import json
    import os

    import pyarrow as pa
    import pyarrow.parquet as pq
    from masterone.mdm.workflow_snapshot_storage import SCHEMA, open_snapshot, seal_snapshot, workspace
    from masterone.storage.artifact import MANIFEST, ArtifactManifest, atomic_json
    from masterone.storage.manifest import fingerprint_file
    artifact_id = seal_snapshot(client.session_factory, workflow_run, 'DECISION_SNAPSHOT', {},
                                {'records': record_rows(2)}, operation_id=operation(client, workflow_run))
    with client.session_factory() as db:
        payload = deepcopy(db.get(WorkflowArtifact, artifact_id).payload)
    descriptor = payload['streams']['records']
    part = descriptor['partitions'][0]
    path = workspace(artifact_id) / 'records' / part['path']
    os.chmod(path, 0o600)
    rows = [{'id': f'r{i:07}', 'left_id': None, 'right_id': None,
             'payload': json.dumps({'id': f'r{i:07}', 'raw_fields': {'name': 'x' * 1024**2}})} for i in range(20)]
    pq.write_table(pa.Table.from_pylist(rows, schema=SCHEMA), path, compression='zstd')
    part.update(sha256=fingerprint_file(path), byte_size=path.stat().st_size, row_count=20)
    descriptor['count'] = 20
    identity = ArtifactManifest.model_validate({k: v for k, v in descriptor.items() if k in ArtifactManifest.model_fields})
    atomic_json(path.parent / MANIFEST, identity.model_dump(mode='json'))
    with client.session_factory.begin() as db:
        db.execute(update(WorkflowArtifact).where(WorkflowArtifact.id == artifact_id).values(payload=payload, content_hash=digest(payload)))
    with client.session_factory() as db, open_snapshot(db, artifact_id, workflow_run, 'DECISION_SNAPSHOT') as view:
        with pytest.raises(ValueError, match='WORKFLOW_ARTIFACT_BUDGET'):
            view.records['r0000000']
        assert view.metrics['source_rows_read'] == 0


def test_stream_producer_does_not_request_full_length_or_reiterate(client, workflow_run):
    from masterone.mdm.workflow_snapshot_storage import open_snapshot, seal_snapshot
    class Once:
        def __init__(self):
            self.iterator = iter(record_rows(1100))
        def __len__(self):
            pytest.fail('full input materialization requested')
        def __iter__(self):
            return self
        def __next__(self):
            return next(self.iterator)
    artifact_id = seal_snapshot(client.session_factory, workflow_run, 'DECISION_SNAPSHOT', {},
                                {'records': Once()}, operation_id=operation(client, workflow_run))
    with client.session_factory() as db, open_snapshot(db, artifact_id, workflow_run, 'DECISION_SNAPSHOT') as view:
        assert len(view.records) == 1100


def test_disk_subset_keeps_only_intersecting_conflicts_and_complete_approvals(client, workflow_run):
    from masterone.mdm.workflow_cluster_index import SnapshotIndex
    from masterone.mdm.workflow_snapshot_storage import open_snapshot, seal_snapshot
    records = list(record_rows(4))
    a, b, c, d = [r['id'] for r in records]
    conflicts = [{'id': 'inside', 'record_ids': [a, b]}, {'id': 'outside', 'record_ids': [c, d]}]
    approvals = [{'id': 'complete', 'record_ids': [a, b]}, {'id': 'incomplete', 'record_ids': [b, c]}]
    artifact_id = seal_snapshot(client.session_factory, workflow_run, 'DECISION_SNAPSHOT', {},
        {'records': iter(records), 'known_conflicts': iter(conflicts), 'group_approvals': iter(approvals)},
        operation_id=operation(client, workflow_run))
    with client.session_factory() as db, open_snapshot(db, artifact_id, workflow_run, 'DECISION_SNAPSHOT') as view:
        scoped = SnapshotIndex(view).subset([a, b])
        assert scoped['known_conflicts'] == conflicts[:1]
        assert scoped['group_approvals'] == approvals[:1]


def test_working_index_disk_budget_and_owned_cleanup(client, workflow_run, monkeypatch):
    from masterone.config import get_settings
    from masterone.mdm.workflow_cluster_index import SnapshotIndex
    from masterone.mdm.workflow_snapshot_storage import open_snapshot, seal_snapshot, workspace
    artifact_id = seal_snapshot(client.session_factory, workflow_run, 'DECISION_SNAPSHOT', {},
                                {'records': record_rows(2)}, operation_id=operation(client, workflow_run))
    monkeypatch.setattr(get_settings(), 'stream_disk_bytes', 32768)
    with client.session_factory() as db, pytest.raises(ValueError, match='WORKFLOW_INDEX_DISK_BUDGET'):
        with open_snapshot(db, artifact_id, workflow_run, 'DECISION_SNAPSHOT') as view:
            SnapshotIndex(view)
    assert not list(workspace(artifact_id).glob('working-*'))
    assert (workspace(artifact_id) / 'locators.sqlite').exists()
