"""Sealed, hash-verified DB artifacts. No local file is a readiness signal."""
import json
from copy import deepcopy

from masterone.mdm.generation_staging import digest
from masterone.mdm.workflow_models import WorkflowArtifact

PART_ROWS = 512
PART_BYTES = 2 * 1024 * 1024
PARTITION_PHASES = frozenset({'DECISION_SNAPSHOT', 'CLUSTER_PLAN', 'CLUSTER', 'SINGLETON_SOURCE'})


def _partition(db, run_id, payload):
    descriptors = []

    def visit(value, path):
        mapping = isinstance(value, dict) and len(value) > PART_ROWS
        if isinstance(value, dict) and not mapping:
            return {key: visit(item, [*path, key]) for key, item in value.items()}
        if not isinstance(value, list) and not mapping:
            return value
        values = list(value.items()) if mapping else value
        encoded = [len(json.dumps(item, ensure_ascii=True, default=str).encode()) + 1 for item in values]
        if len(values) <= PART_ROWS and sum(encoded) <= PART_BYTES - 4096:
            return value
        parts, batch, size = [], [], 0

        def write_batch():
            row = WorkflowArtifact(run_id=run_id, phase='ARTIFACT_PART', payload={'items': list(batch)},
                                   content_hash=digest({'items': batch}))
            db.add(row)
            db.flush()
            parts.append({'id': row.id, 'hash': row.content_hash, 'count': len(batch)})

        for item, count in zip(values, encoded):
            if count > PART_BYTES - 4096:
                raise ValueError('WORKFLOW_ARTIFACT_ITEM_TOO_LARGE')
            if batch and (len(batch) >= PART_ROWS or size + count > PART_BYTES - 4096):
                write_batch()
                batch, size = [], 0
            batch.append(item)
            size += count
        if batch:
            write_batch()
        descriptors.append({'path': path, 'parts': parts, 'count': len(values), 'kind': 'mapping' if mapping else 'list'})
        return {} if mapping else []

    stored = visit(payload, [])
    if descriptors:
        stored = {**stored, '__workflow_parts__': descriptors, '__workflow_logical_hash__': digest(payload)}
    return stored


def profile_artifact(parent, profile):
    payload = {'prepare_artifact_id': parent.id, 'prepare_content_hash': parent.content_hash,
               'profile': profile}
    return WorkflowArtifact(run_id=parent.run_id, phase='PROFILE', payload=payload, content_hash=digest(payload))


def seal(factory, run_id, phase, payload):
    with factory.begin() as db:
        stored = _partition(db, run_id, payload) if phase in PARTITION_PHASES else payload
        artifact = WorkflowArtifact(run_id=run_id, phase=phase, payload=stored, content_hash=digest(stored))
        db.add(artifact)
        db.flush()
        if phase == 'PREPARE' and all(key in payload.get('profile', {}) for key in
                                      ('field_completeness', 'records_with_quality_flags', 'schema')):
            # Persist a small independently verified summary in the same transaction.
            db.add(profile_artifact(artifact, payload['profile']))
        return artifact.id


def load(db, artifact_id, run_id, phase):
    artifact = db.get(WorkflowArtifact, artifact_id)
    if artifact is None or artifact.run_id != run_id or artifact.phase != phase or digest(artifact.payload) != artifact.content_hash:
        raise ValueError('WORKFLOW_ARTIFACT_INVALID')
    if '__workflow_parts__' not in artifact.payload:
        return artifact.payload
    payload = deepcopy(artifact.payload)
    descriptors = payload.pop('__workflow_parts__')
    logical_hash = payload.pop('__workflow_logical_hash__')
    for descriptor in descriptors:
        ids = [part['id'] for part in descriptor['parts']]
        rows = {row.id: row for row in db.query(WorkflowArtifact).filter(WorkflowArtifact.id.in_(ids))}
        items = []
        for part in descriptor['parts']:
            row = rows.get(part['id'])
            if (row is None or row.run_id != run_id or row.phase != 'ARTIFACT_PART'
                    or row.content_hash != part['hash'] or digest(row.payload) != part['hash']
                    or len(row.payload.get('items', [])) != part['count']):
                raise ValueError('WORKFLOW_ARTIFACT_INVALID')
            items.extend(row.payload['items'])
        target = payload
        for key in descriptor['path'][:-1]:
            target = target[key]
        if len(items) != descriptor['count'] or not descriptor['path']:
            raise ValueError('WORKFLOW_ARTIFACT_INVALID')
        target[descriptor['path'][-1]] = dict(items) if descriptor.get('kind') == 'mapping' else items
    if digest(payload) != logical_hash:
        raise ValueError('WORKFLOW_ARTIFACT_INVALID')
    return payload


def logical_artifact_id(db, run_id, phase, logical_hash):
    """Resolve a logical receipt without hydrating parts; load still verifies it."""
    from sqlalchemy import or_
    return db.query(WorkflowArtifact.id).filter_by(run_id=run_id, phase=phase).filter(or_(
        WorkflowArtifact.content_hash == logical_hash,
        WorkflowArtifact.payload['__workflow_logical_hash__'].as_string() == logical_hash)).limit(1).scalar()


def _path_value(db, row, path):
    """Walk through independently verified intermediate mapping partitions."""
    value, prefix = row.payload, []
    for key in path:
        descriptor = next((d for d in row.payload.get('__workflow_parts__', [])
                           if d['path'] == prefix and d.get('kind') == 'mapping'), None)
        if descriptor:
            found = False
            for part in descriptor['parts']:
                chunk = db.get(WorkflowArtifact, part['id'])
                if (chunk is None or chunk.run_id != row.run_id or chunk.phase != 'ARTIFACT_PART'
                        or chunk.content_hash != part['hash'] or digest(chunk.payload) != part['hash']
                        or len(chunk.payload.get('items', [])) != part['count']):
                    raise ValueError('WORKFLOW_ARTIFACT_INVALID')
                for item_key, item_value in chunk.payload['items']:
                    if item_key == key:
                        value, found = item_value, True
                        break
                if found:
                    break
            if not found:
                raise KeyError(key)
        else:
            value = value[key]
        prefix.append(key)
    return value


def read_value(db, artifact_id, run_id, phase, path, *, expected_hash=None):
    row = db.get(WorkflowArtifact, artifact_id)
    if (row is None or row.run_id != run_id or row.phase != phase or digest(row.payload) != row.content_hash
            or expected_hash is not None and row.content_hash != expected_hash):
        raise ValueError('WORKFLOW_ARTIFACT_INVALID')
    return _path_value(db, row, path)


def list_page(db, artifact_id, run_id, phase, path, offset, limit, *, expected_hash=None):
    """Verify and read only array parts intersecting a requested page."""
    if offset < 0 or not 1 <= limit <= 100:
        raise ValueError('WORKFLOW_ARTIFACT_PAGE_INVALID')
    row = db.get(WorkflowArtifact, artifact_id)
    if (row is None or row.run_id != run_id or row.phase != phase or digest(row.payload) != row.content_hash
            or expected_hash is not None and row.content_hash != expected_hash):
        raise ValueError('WORKFLOW_ARTIFACT_INVALID')
    descriptor = next((part for part in row.payload.get('__workflow_parts__', [])
                       if part['path'] == list(path)), None)
    if descriptor is None:
        value = _path_value(db, row, path)
        items, total = value[offset:offset + limit], len(value)
    else:
        if descriptor.get('kind', 'list') != 'list':
            raise ValueError('WORKFLOW_ARTIFACT_PAGE_INVALID')
        total, items, position = descriptor['count'], [], 0
        for part in descriptor['parts']:
            end = position + part['count']
            if position < offset + limit and end > offset:
                chunk = db.get(WorkflowArtifact, part['id'])
                if (chunk is None or chunk.run_id != run_id or chunk.phase != 'ARTIFACT_PART'
                        or chunk.content_hash != part['hash'] or digest(chunk.payload) != part['hash']
                        or len(chunk.payload.get('items', [])) != part['count']):
                    raise ValueError('WORKFLOW_ARTIFACT_INVALID')
                items.extend(chunk.payload['items'][max(0, offset - position):offset + limit - position])
            position = end
        if position != total:
            raise ValueError('WORKFLOW_ARTIFACT_INVALID')
    following = offset + len(items)
    return {'items': items, 'total': total, 'next_cursor': str(following) if following < total else None}


def iter_list(db, artifact_id, run_id, phase, path, *, expected_hash=None):
    """Verify each legacy array partition once while retaining only that batch."""
    row = db.get(WorkflowArtifact, artifact_id)
    if (row is None or row.run_id != run_id or row.phase != phase or digest(row.payload) != row.content_hash
            or expected_hash is not None and row.content_hash != expected_hash):
        raise ValueError('WORKFLOW_ARTIFACT_INVALID')
    descriptor = next((part for part in row.payload.get('__workflow_parts__', [])
                       if part['path'] == list(path)), None)
    if descriptor is None:
        value = _path_value(db, row, path)
        if not isinstance(value, list):
            raise ValueError('WORKFLOW_ARTIFACT_PAGE_INVALID')
        yield from value
        return
    if descriptor.get('kind', 'list') != 'list':
        raise ValueError('WORKFLOW_ARTIFACT_PAGE_INVALID')
    if sum(part['count'] for part in descriptor['parts']) != descriptor['count']:
        raise ValueError('WORKFLOW_ARTIFACT_INVALID')
    for part in descriptor['parts']:
        chunk = db.get(WorkflowArtifact, part['id'])
        if (chunk is None or chunk.run_id != run_id or chunk.phase != 'ARTIFACT_PART'
                or chunk.content_hash != part['hash'] or digest(chunk.payload) != part['hash']
                or len(chunk.payload.get('items', [])) != part['count']):
            raise ValueError('WORKFLOW_ARTIFACT_INVALID')
        yield from chunk.payload['items']


def warm_profile(factory, run_id):
    """Explicit deployment backfill; GET never writes or rewrites a PREPARE artifact."""
    from masterone.mdm.data_profile import build_profile, record_read
    from masterone.mdm.models import ResolutionRecord

    with factory.begin() as db:
        parent = db.query(WorkflowArtifact).filter_by(run_id=run_id, phase='PREPARE').order_by(WorkflowArtifact.created_at.desc()).first()
        if parent is None:
            return
        existing = db.query(WorkflowArtifact).filter_by(run_id=run_id, phase='PROFILE').filter(
            WorkflowArtifact.payload['prepare_artifact_id'].as_string() == parent.id).first()
        if existing:
            summary = load(db, existing.id, run_id, 'PROFILE')
            if summary['prepare_content_hash'] != parent.content_hash:
                raise ValueError('WORKFLOW_ARTIFACT_INVALID')
            return
        prepared = load(db, parent.id, run_id, 'PREPARE')
        profile = prepared['profile']
        if not all(key in profile for key in ('field_completeness', 'records_with_quality_flags', 'schema')):
            records = prepared.get('records')
            if records is None:
                records = (record_read(row) for row in db.query(ResolutionRecord).filter_by(run_id=run_id).yield_per(512))
            profile = {**profile, **build_profile(records)}
        db.add(profile_artifact(parent, profile))
