"""Context-owned disk state for full snapshots and large exclusion regions."""
import json
import sqlite3
import tempfile
import zlib
from collections import Counter
from collections.abc import Mapping, Set
from itertools import islice
from uuid import uuid4

from masterone.mdm.pipeline.matching import update_value_frequencies
from masterone.mdm.workflow_snapshot_storage import WorkflowArtifactBudgetError, canonical_payload


def unpack(payload):
    return json.loads(zlib.decompress(payload))


class DiskMapping(Mapping):
    def __init__(self, index, table, field=None):
        self.index, self.table, self.field = index, table, field

    def __getitem__(self, key):
        if self.field is not None:
            row = self.index.connection.execute('SELECT count FROM frequencies WHERE field=? AND value=?', (self.field, key)).fetchone()
        else:
            row = self.index.connection.execute('SELECT payload FROM records WHERE id=?', (key,)).fetchone()
        if row is None:
            raise KeyError(key)
        return row[0] if self.field is not None else unpack(row[0])

    def __iter__(self):
        query, params = ('SELECT value FROM frequencies WHERE field=? ORDER BY value', (self.field,)) if self.field else ('SELECT id FROM records ORDER BY id', ())
        for row in self.index.connection.execute(query, params):
            yield row[0]

    def __len__(self):
        query, params = ('SELECT COUNT(*) FROM frequencies WHERE field=?', (self.field,)) if self.field else ('SELECT COUNT(*) FROM records', ())
        return self.index.connection.execute(query, params).fetchone()[0]


class DiskSet(Set):
    def __init__(self, index, token):
        self.index, self.token = index, token

    def __contains__(self, value):
        return self.index.connection.execute('SELECT 1 FROM regions WHERE token=? AND id=?', (self.token, value)).fetchone() is not None

    def __iter__(self):
        for row in self.index.connection.execute('SELECT id FROM regions WHERE token=? ORDER BY id', (self.token,)):
            yield row[0]

    def __len__(self):
        return self.index.connection.execute('SELECT COUNT(*) FROM regions WHERE token=?', (self.token,)).fetchone()[0]

    def summary(self, limit=25):
        if not 1 <= limit <= 100:
            raise ValueError('WORKFLOW_BATCH_BUDGET')
        return {'record_count': len(self), 'record_ids': list(islice(iter(self), limit)), 'approvable': len(self) <= 100}


class PairMapping(Mapping):
    def __init__(self, index):
        self.index = index

    def __getitem__(self, endpoints):
        a, b = sorted(endpoints)
        row = self.index.connection.execute('SELECT payload FROM pairs WHERE a=? AND b=? ORDER BY position DESC LIMIT 1', (a, b)).fetchone()
        if row is None:
            raise KeyError(endpoints)
        return unpack(row[0])

    def __iter__(self):
        yield from self.index.connection.execute('SELECT DISTINCT a,b FROM pairs ORDER BY a,b')

    def __len__(self):
        return self.index.connection.execute('SELECT COUNT(*) FROM (SELECT a,b FROM pairs GROUP BY a,b)').fetchone()[0]


class IncidentMapping:
    def __init__(self, index, *, negative=False, kind=None):
        self.index, self.negative, self.kind = index, negative, kind

    def __getitem__(self, record_id):
        if self.kind is not None:
            return NeighborSet(self.index, self.kind, record_id)
        def rows():
            if self.negative:
                yield from self.index.connection.execute('SELECT a,b FROM negatives WHERE a=? OR b=? ORDER BY a,b', (record_id, record_id))
            else:
                for (payload,) in self.index.connection.execute('SELECT payload FROM pairs WHERE a=? OR b=? ORDER BY position', (record_id, record_id)):
                    yield unpack(payload)
        return rows()


class NeighborSet(Set):
    def __init__(self, index, kind, record_id):
        self.index, self.kind, self.record_id = index, kind, record_id

    def __contains__(self, value):
        return self.index.connection.execute('SELECT 1 FROM edges WHERE kind=? AND src=? AND dst=?',
            (self.kind, self.record_id, value)).fetchone() is not None

    def __iter__(self):
        for (rid,) in self.index.connection.execute('SELECT dst FROM edges WHERE kind=? AND src=? ORDER BY dst', (self.kind, self.record_id)):
            yield rid

    def __len__(self):
        return self.index.connection.execute('SELECT COUNT(*) FROM edges WHERE kind=? AND src=?', (self.kind, self.record_id)).fetchone()[0]


class DiskSnapshotIndex:
    storage_kind = 'sqlite'

    def __init__(self, view, *, frequencies=None):
        self.snapshot = view
        self.directory = tempfile.TemporaryDirectory(prefix='working-', dir=view.directory)
        self.connection = sqlite3.connect(self.directory.name + '/index.sqlite')
        self.connection.executescript('''
            PRAGMA temp_store=FILE;
            PRAGMA cache_size=-8192;
            CREATE TABLE records(id TEXT PRIMARY KEY, payload BLOB NOT NULL);
            CREATE TABLE frequencies(field TEXT, value TEXT, count INTEGER, PRIMARY KEY(field,value));
            CREATE TABLE pairs(id TEXT PRIMARY KEY, a TEXT, b TEXT, position INTEGER, payload BLOB NOT NULL);
            CREATE INDEX pair_endpoints ON pairs(a,b);
            CREATE INDEX pair_right ON pairs(b);
            CREATE TABLE edges(kind TEXT, src TEXT, dst TEXT, PRIMARY KEY(kind,src,dst));
            CREATE TABLE negatives(a TEXT,b TEXT,PRIMARY KEY(a,b));
            CREATE INDEX negative_right ON negatives(b);
            CREATE TABLE metadata_rows(kind TEXT, id TEXT, payload BLOB, PRIMARY KEY(kind,id));
            CREATE TABLE metadata_members(kind TEXT, id TEXT, record_id TEXT, PRIMARY KEY(kind,id,record_id));
            CREATE INDEX metadata_record ON metadata_members(kind,record_id);
            CREATE TABLE regions(token TEXT, id TEXT, PRIMARY KEY(token,id));
        ''')
        view.resources.append(self)
        locators = view.directory / 'locators.sqlite'
        # A full-compute index reserves all referenced source parts up front;
        # repeated region checks must not stat every partition for every group.
        self._source_bytes = sum(p['byte_size'] for desc in view.descriptors.values() for p in desc['partitions'])
        self._source_bytes += locators.stat().st_size if locators.exists() else 0
        self._check_disk()
        self.by_id = DiskMapping(self, 'records')
        self.frequencies = frequencies if frequencies is not None else {field: DiskMapping(self, 'frequencies', field) for field in ('phone', 'email')}
        self.pairs = PairMapping(self)
        self.incident = IncidentMapping(self)
        self.negative_incident = IncidentMapping(self, negative=True)
        self.adjacency = IncidentMapping(self, kind='actionable')
        self.accepted = IncidentMapping(self, kind='accepted')
        for i, record in enumerate(view.iter_rows('records')):
            payload = canonical_payload(record).encode()
            self.connection.execute('INSERT INTO records VALUES (?,?)', (record['id'], zlib.compress(payload, 1)))
            counts = {field: Counter() for field in ('phone', 'email')}
            update_value_frequencies(counts, record)
            self.connection.executemany('INSERT INTO frequencies VALUES (?,?,?) ON CONFLICT(field,value) DO UPDATE SET count=count+excluded.count',
                ((field, value, count) for field, values in counts.items() for value, count in values.items()))
            if i % 512 == 511:
                self.connection.commit()
                self._check_disk()
        self.connection.executemany('INSERT OR IGNORE INTO negatives VALUES (?,?)', view.negative_pairs)
        for position, pair in enumerate(view.pairs):
            a, b = sorted((pair['left_id'], pair['right_id']))
            self.connection.execute('INSERT INTO pairs VALUES (?,?,?,?,?)',
                (pair['id'], a, b, position, zlib.compress(canonical_payload(pair).encode(), 1)))
            decision = view.overlays.get(pair['id'], 'UNDECIDED')
            accepted = decision == 'ACCEPTED'
            denied = self.connection.execute('SELECT 1 FROM negatives WHERE a=? AND b=?', (a, b)).fetchone()
            actionable = accepted or (decision not in ('REJECTED', 'NON_MATCH') and not denied
                and pair['decision'] not in ('BLOCKED', 'NOT_MERGEABLE', 'NON_MATCH')
                and (decision == 'DEFERRED' or pair['decision'] == 'MERGEABLE'))
            for kind, include in [('accepted', accepted), ('actionable', actionable)]:
                if include:
                    self.connection.executemany('INSERT OR IGNORE INTO edges VALUES (?,?,?)', [(kind, a, b), (kind, b, a)])
            if position % 512 == 511:
                self.connection.commit()
                self._check_disk()
        self.connection.commit()
        if self.connection.execute('SELECT 1 FROM pairs p WHERE NOT EXISTS (SELECT 1 FROM records r WHERE r.id=p.a) '
            'OR NOT EXISTS (SELECT 1 FROM records r WHERE r.id=p.b) LIMIT 1').fetchone():
            raise ValueError('WORKFLOW_PAIR_UNIVERSE_INVALID')
        self._check_disk()
        for kind, field in [('known_conflicts', 'record_ids'), ('group_approvals', 'record_ids')]:
            for row in view.iter_rows(kind):
                from masterone.mdm.workflow_snapshot_storage import row_key
                key = row_key(row)
                self.connection.execute('INSERT INTO metadata_rows VALUES (?,?,?)',
                    (kind, key, zlib.compress(canonical_payload(row).encode(), 1)))
                self.connection.executemany('INSERT OR IGNORE INTO metadata_members VALUES (?,?,?)',
                    ((kind, key, rid) for rid in row.get(field, [])))
        self.connection.commit()
        self._check_disk()

    def _check_disk(self):
        from masterone.config import get_settings
        pages = self.connection.execute('PRAGMA page_count').fetchone()[0]
        page_size = self.connection.execute('PRAGMA page_size').fetchone()[0]
        total = pages * page_size + self._source_bytes
        if total > get_settings().stream_disk_bytes:
            raise WorkflowArtifactBudgetError('WORKFLOW_INDEX_DISK_BUDGET', disk_bytes=total,
                                             max_bytes=get_settings().stream_disk_bytes)

    def _table(self, ids=()):
        name = 'members_' + uuid4().hex
        self.connection.execute(f'CREATE TABLE {name}(id TEXT PRIMARY KEY)')
        self.connection.executemany(f'INSERT OR IGNORE INTO {name} VALUES (?)', ((rid,) for rid in ids))
        return name

    def region(self, seeds, *, kind='actionable'):
        seed_token, selected = uuid4().hex, uuid4().hex
        self.connection.executemany('INSERT OR IGNORE INTO regions VALUES (?,?)', ((seed_token, rid) for rid in seeds))
        self.connection.execute('''WITH RECURSIVE walk(id) AS (
            SELECT s.id FROM regions s JOIN records r ON r.id=s.id WHERE s.token=?
            UNION SELECT e.dst FROM edges e JOIN walk w ON e.src=w.id WHERE e.kind=?
        ) INSERT INTO regions SELECT ?,w.id FROM walk w JOIN records r ON r.id=w.id''', (seed_token, kind, selected))
        self.connection.execute('DELETE FROM regions WHERE token=?', (seed_token,))
        self._check_disk()
        return DiskSet(self, selected)

    def accepted_components(self):
        seen = uuid4().hex
        for (seed,) in self.connection.execute("SELECT DISTINCT src FROM edges WHERE kind='accepted' ORDER BY src"):
            if self.connection.execute('SELECT 1 FROM regions WHERE token=? AND id=?', (seen, seed)).fetchone():
                continue
            component = self.region([seed], kind='accepted')
            self.connection.execute('INSERT OR IGNORE INTO regions SELECT ?,id FROM regions WHERE token=?', (seen, component.token))
            yield component

    def seeds(self, kind):
        """Release each cursor before consumers create/drop scoped SQL tables."""
        after = ''
        while rows := self.connection.execute('SELECT DISTINCT src FROM edges WHERE kind=? AND src>? '
                                              'ORDER BY src LIMIT 512', (kind, after)).fetchall():
            yield from (rid for (rid,) in rows)
            after = rows[-1][0]

    def subset(self, record_ids):
        ids = list(islice(iter(record_ids), 2049))
        if len(ids) > 2048:
            raise ValueError('WORKFLOW_BATCH_BUDGET')
        selected = self._table(ids)
        try:
            records = [unpack(payload) for (payload,) in self.connection.execute(
                f'SELECT r.payload FROM records r JOIN {selected} s ON s.id=r.id ORDER BY r.id')]
            pairs = [unpack(payload) for (payload,) in self.connection.execute(
                f'SELECT p.payload FROM pairs p JOIN {selected} a ON a.id=p.a JOIN {selected} b ON b.id=p.b ORDER BY p.id')]
            negatives = list(self.connection.execute(
                f'SELECT n.a,n.b FROM negatives n JOIN {selected} a ON a.id=n.a JOIN {selected} b ON b.id=n.b ORDER BY n.a,n.b'))
            conflicts = [unpack(payload) for (payload,) in self.connection.execute(
                f"SELECT r.payload FROM metadata_rows r WHERE kind='known_conflicts' AND EXISTS "
                f'(SELECT 1 FROM metadata_members m JOIN {selected} s ON m.record_id=s.id WHERE m.kind=r.kind AND m.id=r.id) ORDER BY r.id')]
            approvals = [unpack(payload) for (payload,) in self.connection.execute(
                "SELECT r.payload FROM metadata_rows r WHERE kind='group_approvals' AND NOT EXISTS "
                f'(SELECT 1 FROM metadata_members m WHERE m.kind=r.kind AND m.id=r.id AND NOT EXISTS (SELECT 1 FROM {selected} s WHERE s.id=m.record_id)) ORDER BY r.id')]
            from copy import deepcopy
            return {**deepcopy(self.snapshot.row.payload['metadata']), 'records': records, 'pairs': pairs,
                'negative_pairs': negatives, 'overlays': {p['id']: self.snapshot.overlays.get(p['id'], 'UNDECIDED') for p in pairs},
                'review_reasons': {p['id']: reason for p in pairs if (reason := self.snapshot.review_reasons.get(p['id']))},
                'group_approvals': approvals, 'known_conflicts': conflicts}
        finally:
            self.connection.execute(f'DROP TABLE {selected}')

    def close(self):
        self.connection.close()
        self.directory.cleanup()
