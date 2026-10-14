"""Owned immutable workflow manifests using the existing partition protocol."""
import io
import os
import re
import sqlite3
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pyarrow as pa

from masterone.config import get_settings
from masterone.mdm.generation_staging import _canonical_chunks, digest
from masterone.mdm.workflow_models import WorkflowArtifact, WorkflowOperation
from masterone.storage.artifact import ArtifactWriter, DatasetManifest
from masterone.storage.background_upload import BackgroundUploader
from masterone.storage.object_store import LocalObjectStore
from masterone.storage.r2 import get_store

FORMAT = 'p119_workflow_snapshot_v2'
BATCH_ROWS = 512
MAX_BATCH_ROWS = 2048
PART_BYTES = 16 * 1024**2
MANIFEST_BYTES = 8 * 1024**2
SCHEMA = pa.schema([('id', pa.string()), ('left_id', pa.string()), ('right_id', pa.string()), ('payload', pa.string())])


class WorkflowArtifactBudgetError(ValueError):
    def __init__(self, code, **metrics):
        self.metrics = metrics
        super().__init__(code)


def canonical_payload(value, limit=PART_BYTES - 65536):
    output = io.BytesIO()
    for chunk in _canonical_chunks(value):
        if output.tell() + len(chunk) > limit:
            raise WorkflowArtifactBudgetError('WORKFLOW_ARTIFACT_BUDGET', max_bytes=limit)
        output.write(chunk)
    return output.getvalue().decode('utf-8')


def workspace(artifact_id):
    root = Path(get_settings().workflow_artifact_dir).resolve()
    path = root / 'cache' / sha256(artifact_id.encode()).hexdigest()[:24]
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError('WORKFLOW_ARTIFACT_UNSAFE_PATH')
    # Existing immutable partition temp names can exceed Win32 MAX_PATH in an
    # isolated report directory. Preserve the same resolved owned workspace.
    if os.name == 'nt' and not str(path).startswith('\\\\?\\'):
        path = Path('\\\\?\\' + str(path))
    return path


def object_store():
    settings = get_settings()
    return get_store(settings) or LocalObjectStore(Path(settings.workflow_artifact_dir) / 'objects')


def row_key(row):
    key = row.get('id') or row.get('record_id') or row.get('operation_id') or digest(row)
    if not isinstance(key, str) or not key or len(key.encode('utf-8')) > 512:
        raise ValueError('WORKFLOW_ROW_ID_INVALID')
    return key


def file_identity(path):
    checksum, size = sha256(), 0
    with Path(path).open('rb') as stream:
        while chunk := stream.read(1024**2):
            checksum.update(chunk)
            size += len(chunk)
    return checksum.hexdigest(), size


def seal_snapshot(factory, run_id, phase, metadata, streams, *, operation_id):
    from masterone.mdm.job_queue import current_claim, guard
    metadata = dict(metadata)
    canonical_payload(metadata, 64 * 1024)
    with factory.begin() as db:
        op = db.get(WorkflowOperation, operation_id)
        if op is None or op.run_id != run_id:
            raise ValueError('WORKFLOW_OPERATION_INVALID')
        guard(db, run_id)
        actor = op.actor_user_id
    claim = current_claim()
    attempt_id = claim.attempt_id if claim else operation_id
    artifact_id = str(uuid4())
    directory = workspace(artifact_id)
    directory.mkdir(parents=True, exist_ok=True)
    index = sqlite3.connect(str(directory / 'locators.sqlite'))
    index.execute('CREATE TABLE locations (stream TEXT, id TEXT, partition_id TEXT, ordinal INTEGER, left_id TEXT, right_id TEXT, PRIMARY KEY(stream,id))')
    index.execute('CREATE INDEX pair_endpoints ON locations(stream,left_id,right_id)')
    descriptors = {}
    settings = get_settings()
    try:
        with BackgroundUploader(object_store(), directory, run_id=run_id, attempt_id=attempt_id,
            max_pending_partitions=4, max_pending_bytes=64 * 1024**2, upload_concurrency=1,
            local_disk_budget=settings.stream_disk_bytes) as uploader:
            for name, rows in streams.items():
                if not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', name):
                    raise ValueError('WORKFLOW_STREAM_NAME_INVALID')
                manifest = DatasetManifest.create(directory / name, run_id=run_id, attempt_id=attempt_id,
                    artifact_id=artifact_id + '_' + name, schema=SCHEMA)
                writer = ArtifactWriter(manifest, uploader, SCHEMA, PART_BYTES)
                bounds, count, batch, size, previous_key = {}, 0, [], 0, None
                ordered = True
                def flush():
                    nonlocal batch, size, count
                    if not batch:
                        return
                    pid = f'p{len(bounds):08}'
                    writer.write(pid, [pa.Table.from_pylist(batch, schema=SCHEMA)])
                    keys = [row['id'] for row in batch]
                    bounds[pid] = {'minimum': min(keys), 'maximum': max(keys)}
                    index.executemany('INSERT INTO locations VALUES (?,?,?,?,?,?)',
                        ((name, row['id'], pid, i, row['left_id'], row['right_id']) for i, row in enumerate(batch)))
                    count += len(batch)
                    batch, size = [], 0
                for row in rows:
                    if not isinstance(row, dict):
                        raise ValueError('WORKFLOW_STREAM_ROW_INVALID')
                    payload = canonical_payload(row)
                    key = row_key(row)
                    ordered &= previous_key is None or key > previous_key
                    previous_key = key
                    row_size = len(payload.encode('utf-8')) + len(key.encode('utf-8')) + 64
                    if row_size > PART_BYTES - 65536:
                        raise WorkflowArtifactBudgetError('WORKFLOW_ARTIFACT_BUDGET', max_bytes=PART_BYTES)
                    if batch and (len(batch) >= BATCH_ROWS or size + row_size > PART_BYTES - 65536):
                        flush()
                    batch.append({'id': key, 'left_id': row.get('left_id'), 'right_id': row.get('right_id'), 'payload': payload})
                    size += row_size
                flush()
                writer.close()
                uploader.flush()
                remote_manifest = uploader.upload_manifest(manifest)
                descriptors[name] = {**manifest.read().model_dump(mode='json'), 'count': count,
                                     'bounds': bounds, 'ordered': ordered, 'manifest_key': remote_manifest}
                index.commit()
                with factory.begin() as db:
                    guard(db, run_id)
        index.commit()
        index.close()
        locator_path = directory / 'locators.sqlite'
        locator_hash, locator_size = file_identity(locator_path)
        locator_key = f'workflow/{run_id}/{artifact_id}/locators/{locator_hash}.sqlite'
        store = object_store()
        store.upload_file(locator_path, locator_key, 'application/vnd.sqlite3')
        remote_hash, remote_size = sha256(), 0
        for chunk in store.iter_chunks(locator_key):
            remote_hash.update(chunk)
            remote_size += len(chunk)
        if remote_hash.hexdigest() != locator_hash or remote_size != locator_size:
            raise ValueError('WORKFLOW_INDEX_INVALID')
        payload = {'format': FORMAT, 'run_id': run_id, 'artifact_id': artifact_id, 'phase': phase,
                   'operation_id': operation_id, 'attempt_id': attempt_id, 'actor_user_id': actor,
                   'metadata': metadata, 'streams': descriptors,
                   'locator_receipt': {'key': locator_key, 'sha256': locator_hash, 'byte_size': locator_size}}
        canonical_payload(payload, MANIFEST_BYTES)
        with factory.begin() as db:
            guard(db, run_id)
            op = db.get(WorkflowOperation, operation_id)
            if op is None or op.run_id != run_id or op.actor_user_id != actor:
                raise ValueError('WORKFLOW_OPERATION_INVALID')
            db.add(WorkflowArtifact(id=artifact_id, run_id=run_id, phase=phase, payload=payload, content_hash=digest(payload)))
        return artifact_id
    finally:
        index.close()


@contextmanager
def open_snapshot(db, artifact_id, run_id, phase):
    from masterone.mdm.workflow_snapshot_views import SnapshotView
    row = db.get(WorkflowArtifact, artifact_id)
    if row is None or row.run_id != run_id or row.phase != phase or digest(row.payload) != row.content_hash:
        raise ValueError('WORKFLOW_ARTIFACT_INVALID')
    view = SnapshotView(db, row)
    try:
        yield view
    finally:
        view.close()
