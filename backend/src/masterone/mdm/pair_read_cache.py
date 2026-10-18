"""Bounded revision-addressed read cache; callers must authorize before lookup.

Only summaries and status counters persist in R2. Pair source pages stay in RAM.
The optional PAIR_CACHE_R2_* connection is separate from source/export storage,
so enabling this cache never changes how historical artifacts are resolved.
"""
import json
import logging
import os
from collections import OrderedDict
from copy import deepcopy
from hashlib import sha256
from threading import RLock
from time import monotonic

from masterone.mdm.workflow_assessment_reads import active_assessment

log = logging.getLogger(__name__)
MAX_BYTES = 16 * 1024**2
MAX_ENTRIES = 128
TTL = 1800
_entries = OrderedDict()
_lock = RLock()
_bytes = 0
_remote = None


def remote_store():
    global _remote
    from masterone.storage.demo_scope import local_only
    if local_only():
        return None
    fields = [os.getenv('PAIR_CACHE_R2_' + key, '') for key in
              ('ENDPOINT_URL', 'ACCESS_KEY_ID', 'SECRET_ACCESS_KEY', 'BUCKET')]
    if not all(fields):
        return None
    if _remote is None:
        from masterone.storage.object_store import R2ObjectStore
        _remote = R2ObjectStore(*fields)
    return _remote


def identity(db, run, resource, params):
    assessment = active_assessment(db, run.id)
    # DB namespace prevents collisions between independent local/QA deployments.
    value = [str(db.bind.url), run.owner_user_id, run.id,
             run.workflow_revision, run.decision_revision, run.cluster_revision,
             assessment.id if assessment else None, 'step2-taxonomies-summary-v3' if resource == 'summary' else 'step2-taxonomies-cache-v2', resource, params]
    return sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def cached_read(db, run, resource, params, compute, *, operation_active=None):
    global _bytes
    # Workers may be publishing partially committed batches. Never store them.
    from masterone.mdm.workflow_models import WorkflowOperation
    if db.new or db.dirty or db.deleted:
        return compute()
    if operation_active is None:
        operation_active = db.query(WorkflowOperation.id).filter(
            WorkflowOperation.run_id == run.id,
            WorkflowOperation.status.in_(('QUEUED', 'RUNNING'))).first() is not None
    if operation_active:
        return compute()
    key = identity(db, run, resource, params)
    with _lock:
        for old, (at, _, size) in list(_entries.items()):
            if monotonic() - at >= TTL:
                del _entries[old]
                _bytes -= size
        if key in _entries:
            _entries.move_to_end(key)
            return deepcopy(_entries[key][1])
        # Serialize cache misses to avoid parallel copies of the expensive scan.
        # This also puts a hard bound on memory used by concurrent cache fills.
        try:
            store = remote_store() if resource in ('summary', 'status-counts') else None
        except Exception:
            log.warning('PAIR_CACHE_R2_CONFIG_FAILED')
            store = None
        object_key = f'pair-read-cache/v1/{key}.json'
        value = None
        if store:
            try:
                if store.exists(object_key):
                    chunks, size = [], 0
                    for chunk in store.iter_chunks(object_key, chunk_size=65536):
                        size += len(chunk)
                        if size > 1024**2:
                            raise ValueError('PAIR_CACHE_SUMMARY_TOO_LARGE')
                        chunks.append(chunk)
                    raw = b''.join(chunks)
                    envelope = json.loads(raw)
                    payload = envelope['value']
                    checksum = sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
                    if envelope.get('key') == key and checksum == envelope.get('sha256'):
                        value = payload
            except Exception:
                log.warning('PAIR_CACHE_R2_READ_FAILED')
        if value is None:
            value = compute()
            if store:
                try:
                    checksum = sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
                    store.upload_bytes(json.dumps(dict(key=key, sha256=checksum, value=value)).encode(),
                                       object_key, 'application/json')
                except Exception:
                    log.warning('PAIR_CACHE_R2_WRITE_FAILED')
        size = len(json.dumps(value, default=str).encode())
        if size <= MAX_BYTES:
            while _entries and (_bytes + size > MAX_BYTES or len(_entries) >= MAX_ENTRIES):
                _, (_, _, removed) = _entries.popitem(last=False)
                _bytes -= removed
            _entries[key] = (monotonic(), deepcopy(value), size)
            _bytes += size
        return value
