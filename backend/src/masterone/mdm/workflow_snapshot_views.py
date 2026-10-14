"""Bounded readers; derived locators never replace verified sealed evidence."""
import json
import sqlite3
from collections import OrderedDict
from collections.abc import Mapping
from types import MappingProxyType

import pyarrow.parquet as pq

from masterone.mdm.workflow_models import WorkflowOperation
from masterone.mdm.workflow_snapshot_storage import (
    FORMAT,
    MAX_BATCH_ROWS,
    PART_BYTES,
    SCHEMA,
    WorkflowArtifactBudgetError,
    file_identity,
    object_store,
    row_key,
    workspace,
)
from masterone.storage.artifact import ArtifactManifest, DatasetManifest, partition_lease, schema_version


def freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({key: freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(freeze(item) for item in value)
    return value


class RecordMapping(Mapping):
    def __init__(self, view, name):
        self.view, self.name = view, name

    def __getitem__(self, key):
        return self.view.lookup(self.name, key)

    def __len__(self):
        if self.view.legacy:
            return len(self.view.payload.get(self.name, []))
        return self.view.descriptors.get(self.name, {}).get('count', 0)

    def __iter__(self):
        for row in self.view.iter_rows(self.name):
            yield row_key(row)


class ValueMapping(RecordMapping):
    def __getitem__(self, key):
        if self.view.legacy:
            return self.view.payload.get(self.name, {})[key]
        row = super().__getitem__(key)
        return row.get('value', row.get('decision', row.get('reason')))

    def __iter__(self):
        if self.view.legacy:
            yield from self.view.payload.get(self.name, {})
        else:
            yield from super().__iter__()


class PairRows:
    def __init__(self, view):
        self.view = view

    def __len__(self):
        return len(RecordMapping(self.view, 'pairs'))

    def __iter__(self):
        yield from self.view.iter_rows('pairs')

    def __getitem__(self, endpoints):
        a, b = sorted(endpoints)
        if not self.view.legacy and self.view.locators is not None:
            location = self.view.locators.execute("SELECT id FROM locations WHERE stream='pairs' AND "
                '((left_id=? AND right_id=?) OR (left_id=? AND right_id=?)) '
                'ORDER BY partition_id DESC, ordinal DESC LIMIT 1', (a, b, b, a)).fetchone()
            if location:
                pair = self.view.lookup('pairs', location[0])
                if tuple(sorted((pair['left_id'], pair['right_id']))) != (a, b):
                    raise ValueError('WORKFLOW_INDEX_INVALID')
                return pair
            raise KeyError(endpoints)
        if not self.view.legacy:
            raise ValueError('WORKFLOW_INDEX_UNAVAILABLE')
        result = None
        for pair in self:
            if tuple(sorted((pair['left_id'], pair['right_id']))) == (a, b):
                result = pair
        if result is None:
            raise KeyError(endpoints)
        return result


class SnapshotView:
    def __init__(self, db, row):
        self.closed = False
        self.row = row
        self.metrics = {'source_rows_read': 0, 'max_batch_rows': 0, 'parts_read': 0}
        self.cache = OrderedDict()
        self.locators = None
        self.resources = []
        self.legacy = row.payload.get('format') != FORMAT
        if self.legacy:
            from masterone.mdm.workflow_artifacts import load
            self.payload = load(db, row.id, row.run_id, row.phase)
            self.metadata = freeze({k: v for k, v in self.payload.items() if k not in
                ('records', 'pairs', 'overlays', 'review_reasons', 'negative_pairs', 'group_approvals')})
            self.descriptors = {}
        else:
            payload = row.payload
            op = db.get(WorkflowOperation, payload.get('operation_id'))
            if (payload.get('run_id') != row.run_id or payload.get('artifact_id') != row.id
                    or payload.get('phase') != row.phase or op is None or op.run_id != row.run_id
                    or op.actor_user_id != payload.get('actor_user_id')):
                raise ValueError('WORKFLOW_ARTIFACT_INVALID')
            self.metadata = freeze(payload['metadata'])
            self.descriptors = payload['streams']
            for name, descriptor in self.descriptors.items():
                identity = ArtifactManifest.model_validate({k: v for k, v in descriptor.items() if k in ArtifactManifest.model_fields})
                if (identity.run_id != row.run_id or identity.attempt_id != payload['attempt_id']
                        or identity.artifact_id != row.id + '_' + name or not identity.local_complete
                        or identity.schema_version != schema_version(SCHEMA)
                        or any(p.row_count > MAX_BATCH_ROWS or p.byte_size > PART_BYTES for p in identity.partitions)
                        or sum(p.row_count for p in identity.partitions) != descriptor['count']):
                    raise ValueError('WORKFLOW_ARTIFACT_INVALID')
            self.directory = workspace(row.id)
            locator_path = self.directory / 'locators.sqlite'
            receipt = payload.get('locator_receipt')
            if receipt:
                from masterone.config import get_settings
                expected_key = f'workflow/{row.run_id}/{row.id}/locators/{receipt["sha256"]}.sqlite'
                if receipt['key'] != expected_key or not 0 < receipt['byte_size'] <= get_settings().stream_disk_bytes:
                    raise ValueError('WORKFLOW_INDEX_INVALID')
                if not locator_path.exists():
                    from uuid import uuid4
                    self.directory.mkdir(parents=True, exist_ok=True)
                    temporary = self.directory / ('locator-download-' + uuid4().hex + '.sqlite')
                    object_store().download_file(receipt['key'], temporary)
                    if file_identity(temporary) != (receipt['sha256'], receipt['byte_size']):
                        temporary.unlink(missing_ok=True)
                        raise ValueError('WORKFLOW_INDEX_INVALID')
                    temporary.replace(locator_path)
                if locator_path.is_symlink() or file_identity(locator_path) != (receipt['sha256'], receipt['byte_size']):
                    raise ValueError('WORKFLOW_INDEX_INVALID')
            if locator_path.exists():
                if locator_path.is_symlink():
                    raise ValueError('WORKFLOW_ARTIFACT_UNSAFE_PATH')
                self.locators = sqlite3.connect(str(locator_path))
                self.locators.execute('PRAGMA query_only=ON')
        self.records = RecordMapping(self, 'records')
        self.pairs = PairRows(self)
        self.overlays = ValueMapping(self, 'overlays')
        self.review_reasons = ValueMapping(self, 'review_reasons')

    @property
    def negative_pairs(self):
        for row in self.iter_rows('negative_pairs'):
            yield tuple(sorted((row['left_id'], row['right_id']))) if isinstance(row, dict) else tuple(row)

    @property
    def group_approvals(self):
        yield from self.iter_rows('group_approvals')

    def __getitem__(self, name):
        if name in ('records', 'pairs', 'overlays', 'review_reasons', 'negative_pairs', 'group_approvals'):
            return getattr(self, name)
        if name in self.metadata:
            from copy import deepcopy
            return deepcopy(self.row.payload.get('metadata', self.payload if self.legacy else {})[name])
        if name in self.descriptors:
            return self.iter_rows(name)
        raise KeyError(name)

    def get(self, name, default=None):
        try:
            return self[name]
        except KeyError:
            return default

    def __deepcopy__(self, _memo):
        raise TypeError('Workflow snapshot must be read by bounded views')

    def _check(self):
        if self.closed:
            raise ValueError('WORKFLOW_VIEW_CLOSED')

    def _manifest(self, name):
        descriptor = self.descriptors[name]
        identity = ArtifactManifest.model_validate({k: v for k, v in descriptor.items() if k in ArtifactManifest.model_fields})
        directory = self.directory / name
        if directory.is_symlink() or not directory.resolve().is_relative_to(self.directory.resolve()):
            raise ValueError('WORKFLOW_ARTIFACT_UNSAFE_PATH')
        directory.mkdir(parents=True, exist_ok=True)
        manifest = DatasetManifest(directory, identity)
        # The sealed DB descriptor is authority; a local readiness file is not.
        from masterone.storage.artifact import MANIFEST, atomic_json
        if not (directory / MANIFEST).exists():
            atomic_json(directory / MANIFEST, identity.model_dump(mode='json'))
        if manifest.read() != identity:
            raise ValueError('WORKFLOW_ARTIFACT_INVALID')
        return manifest, identity

    def _part(self, name, pid):
        key = name, pid
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        manifest, identity = self._manifest(name)
        item = next((p for p in identity.partitions if p.partition_id == pid), None)
        if item is None:
            raise ValueError('WORKFLOW_INDEX_INVALID')
        path = manifest.path(item)
        if not path.exists():
            store = object_store()
            from uuid import uuid4
            temporary = path.with_suffix('.download-' + uuid4().hex)
            try:
                store.download_file(item.key, temporary)
                DatasetManifest.verify_file(temporary, item)
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
        rows = []
        with partition_lease(path):
            manifest.verify(item)
            with pq.ParquetFile(path) as footer:
                size = sum(footer.metadata.row_group(i).column(j).total_uncompressed_size
                    for i in range(footer.metadata.num_row_groups) for j in range(footer.metadata.num_columns))
                if size > PART_BYTES:
                    raise WorkflowArtifactBudgetError('WORKFLOW_ARTIFACT_BUDGET', uncompressed_bytes=size, max_bytes=PART_BYTES)
            for batch in manifest.iter_batches(item, batch_size=512):
                self.metrics['max_batch_rows'] = max(self.metrics['max_batch_rows'], batch.num_rows)
                rows.extend(batch.to_pylist())
        self.metrics['source_rows_read'] += len(rows)
        self.metrics['parts_read'] += 1
        self.cache[key] = rows
        while len(self.cache) > 2:
            self.cache.popitem(last=False)
        return rows

    def lookup(self, name, key):
        self._check()
        if self.legacy:
            for row in self.payload.get(name, []):
                if row_key(row) == key:
                    return row
            raise KeyError(key)
        if name not in self.descriptors:
            raise KeyError(key)
        location = self.locators.execute('SELECT partition_id, ordinal FROM locations WHERE stream=? AND id=?',
            (name, key)).fetchone() if self.locators is not None else None
        if location:
            rows = self._part(name, location[0])
            if not 0 <= location[1] < len(rows) or rows[location[1]]['id'] != key:
                raise ValueError('WORKFLOW_INDEX_INVALID')
            return json.loads(rows[location[1]]['payload'])
        descriptor = self.descriptors[name]
        matches = [pid for pid, bounds in descriptor['bounds'].items() if bounds['minimum'] <= key <= bounds['maximum']]
        if len(matches) > 1 and not descriptor['ordered']:
            raise ValueError('WORKFLOW_INDEX_UNAVAILABLE')
        for pid in matches:
            for row in self._part(name, pid):
                if row['id'] == key:
                    return json.loads(row['payload'])
        raise KeyError(key)

    def iter_rows(self, name, *, batch_size=512):
        self._check()
        if not 1 <= batch_size <= MAX_BATCH_ROWS:
            raise ValueError('WORKFLOW_BATCH_BUDGET')
        if self.legacy:
            yield from self.payload.get(name, [])
            return
        if name not in self.descriptors:
            return
        manifest, identity = self._manifest(name)
        for item in sorted(identity.partitions, key=lambda p: p.partition_id):
            # _part bounds each closed partition to <=512 rows and 16 MiB.
            for row in self._part(name, item.partition_id):
                yield json.loads(row['payload'])

    def record_page(self, ids):
        from itertools import islice
        ids = list(islice(iter(ids), MAX_BATCH_ROWS + 1))
        if len(ids) > MAX_BATCH_ROWS:
            raise ValueError('WORKFLOW_BATCH_BUDGET')
        return [self.records[rid] for rid in ids]

    def iter_rows_after(self, name, after):
        """Skip sealed ordered partitions before the cursor without decoding."""
        self._check()
        if self.legacy:
            yield from (row for row in self.iter_rows(name) if row_key(row) > after)
            return
        descriptor = self.descriptors.get(name)
        if descriptor is None:
            return
        if not descriptor['ordered']:
            raise ValueError('WORKFLOW_INDEX_ORDER_REQUIRED')
        for item in sorted(descriptor['partitions'], key=lambda p: p['partition_id']):
            pid = item['partition_id']
            if descriptor['bounds'][pid]['maximum'] <= after:
                continue
            for row in self._part(name, pid):
                if row['id'] > after:
                    yield json.loads(row['payload'])

    def list_page(self, name, offset, limit):
        self._check()
        if offset < 0 or not 1 <= limit <= 100:
            raise ValueError('WORKFLOW_PAGE_INVALID')
        if self.legacy:
            rows = self.payload.get(name, [])
            total = len(rows)
            items = rows[offset:offset + limit]
        else:
            descriptor = self.descriptors.get(name, {'count': 0, 'partitions': []})
            total, skip, items = descriptor['count'], offset, []
            for item in sorted(descriptor['partitions'], key=lambda p: p['partition_id']):
                if skip >= item['row_count']:
                    skip -= item['row_count']
                    continue
                rows = self._part(name, item['partition_id'])
                items.extend(json.loads(row['payload']) for row in rows[skip:skip + limit - len(items)])
                skip = 0
                if len(items) == limit:
                    break
        return {'items': items, 'total': total, 'next_cursor': str(offset + len(items)) if offset + len(items) < total else None}

    def close(self):
        for resource in reversed(self.resources):
            resource.close()
        if self.locators is not None:
            self.locators.close()
        self.cache.clear()
        self.closed = True
