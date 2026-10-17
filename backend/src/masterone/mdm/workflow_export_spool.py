"""Spill a legacy frozen JSON result to disk without ORM JSON hydration.

The database expands the immutable JSON; Python verifies the original canonical
hash one group/record at a time. CSV uses the frozen values, never live Golden.
"""
import json
from hashlib import sha256

from sqlalchemy import text

from masterone.mdm.generation_staging import _canonical_chunks
from masterone.mdm.workflow_models import WorkflowGeneration
from masterone.mdm.workflow_service import conflict


class SpoolView:
    def __init__(self, state, receipt, coverage):
        self.state, self.receipt, self.coverage = state, receipt, coverage

    def iter_groups(self):
        for (value,) in self.state.execute('SELECT value FROM frozen_groups ORDER BY position'):
            yield json.loads(value)

    def record_ids(self):
        for (key,) in self.state.execute('SELECT id FROM frozen_records ORDER BY id'):
            yield key

    def record(self, record_id):
        row = self.state.execute('SELECT value FROM frozen_records WHERE id=?', (record_id,)).fetchone()
        if row is None:
            conflict('FINAL_RESULT_INVALID')
        return json.loads(row[0])


def spool_result(db, generation_id, result_hash, golden_revision, state, budget=None):
    state.executescript('CREATE TABLE frozen_groups(position INTEGER PRIMARY KEY, value TEXT); '
                       'CREATE TABLE frozen_records(id TEXT PRIMARY KEY, value TEXT);')
    postgres = db.get_bind().dialect.name == 'postgresql'
    params = {'gid': generation_id}
    if postgres:
        keys_sql = 'SELECT json_object_keys(finalized_result) FROM mdm_workflow_generations WHERE id=:gid'
    else:
        keys_sql = ('SELECT j.key FROM mdm_workflow_generations g, json_each(g.finalized_result) j '
                    'WHERE g.id=:gid')
    keys = sorted(db.execute(text(keys_sql), params).scalars())
    if not {'clusters', 'records', 'golden_revision'} <= set(keys):
        conflict('FINAL_RESULT_INVALID')
    checksum, receipt = sha256(b'{'), {}
    counts = {'clusters': 0, 'singletons': 0, 'multi_record_groups': 0, 'total_records': 0, 'covered_records': 0}
    for key_index, key in enumerate(keys):
        if key_index:
            checksum.update(b',')
        for chunk in _canonical_chunks(key):
            checksum.update(chunk)
        checksum.update(b':')
        if key not in ('clusters', 'records'):
            value = db.query(WorkflowGeneration.finalized_result[key]).filter_by(id=generation_id).scalar()
            receipt[key] = value
            for chunk in _canonical_chunks(value):
                checksum.update(chunk)
            continue
        records = key == 'records'
        checksum.update(b'{' if records else b'[')
        if postgres:
            function = 'json_each' if records else 'json_array_elements'
            columns = 'j.key, j.value::text' if records else 'j.ordinality, j.value::text'
            order = 'j.key COLLATE "C"' if records else 'j.ordinality'
            sql = (f'SELECT {columns} FROM mdm_workflow_generations g, '
                   f'{function}(g.finalized_result -> :field) WITH ORDINALITY j '
                   f'WHERE g.id=:gid ORDER BY {order}')
        else:
            sql = ('SELECT j.key, j.value FROM mdm_workflow_generations g, '
                   'json_each(g.finalized_result, :path) j WHERE g.id=:gid ORDER BY j.key')
        rows = db.execute(text(sql), {**params, 'field': key, 'path': '$.' + key},
                          execution_options={'yield_per': 64})
        try:
            for position, (row_key, encoded) in enumerate(rows):
                value = json.loads(encoded)
                if position:
                    checksum.update(b',')
                if records:
                    for chunk in _canonical_chunks(row_key):
                        checksum.update(chunk)
                    checksum.update(b':')
                    state.execute('INSERT INTO frozen_records VALUES (?,?)', (row_key, encoded))
                    counts['total_records'] += 1
                else:
                    state.execute('INSERT INTO frozen_groups VALUES (?,?)', (position, encoded))
                    counts['clusters'] += 1
                    counts['singletons'] += len(value['member_record_ids']) == 1
                for chunk in _canonical_chunks(value):
                    checksum.update(chunk)
                if budget and position % 64 == 0:
                    budget.check()
        finally:
            rows.close()
        checksum.update(b'}' if records else b']')
    checksum.update(b'}')
    if checksum.hexdigest() != result_hash or receipt['golden_revision'] != golden_revision:
        conflict('FINAL_RESULT_INVALID')
    counts['covered_records'] = counts['total_records']
    counts['multi_record_groups'] = counts['clusters'] - counts['singletons']
    return SpoolView(state, receipt, counts)
