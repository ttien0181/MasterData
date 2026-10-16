"""A spawned process must inherit the caller's pinned matching policy."""
import json
import sqlite3

import pytest
from masterone.mdm.engine_adapter import CORE_FEATURES, engine_context
from masterone.mdm.pipeline.scoring_engine import IndexReader, MemorySnapshot, ScoringEngine, iter_score_batches
from masterone.mdm.pipeline.standardization import standardize


@pytest.mark.parametrize('profile_v2', [True, False])
@pytest.mark.parametrize('indexed', [True, False])
def test_spawned_scoring_matches_pinned_serial_profile(tmp_path, monkeypatch, profile_v2, indexed):
    monkeypatch.setenv('MDM_SCORE_PARALLEL_MIN', '1')
    raw = {'name': 'Nguyen Van An', 'dob': '1982-02-03', 'phone': '0901234567', 'email': 'someone@example.test'}
    fields, quality = standardize(raw, 'PERSON')
    records = [{'id': rid, 'entity_type': 'PERSON', 'source_system': source, 'external_id': rid,
        'raw_fields': dict(raw), 'normalized_fields': fields, 'quality_flags': quality} for rid, source in [('a', 'CRM'), ('b', 'ERP')]]
    candidates = [{'left_id': 'a', 'right_id': 'b', 'reasons': ['exact_name', 'exact_phone', 'exact_email']}]
    outputs = []
    index_path = tmp_path / 'snapshot.sqlite'
    if indexed:
        with sqlite3.connect(index_path) as con:
            con.execute('CREATE TABLE records(id TEXT PRIMARY KEY, payload TEXT)')
            con.executemany('INSERT INTO records VALUES (?,?)', [(r['id'], json.dumps(r)) for r in records])
    with engine_context({f: profile_v2 for f in CORE_FEATURES}):
        for workers in ('1', '2'):
            monkeypatch.setenv('MDM_SCORE_WORKERS', workers)
            snapshot = IndexReader(index_path, cache_records=1) if indexed else MemorySnapshot(records)
            try:
                engine = ScoringEngine(snapshot)
                path = tmp_path / f'scores-{workers}.parquet'
                engine.write_scores(iter(candidates), path, expected_count=1)
                results = [r for batch in iter_score_batches(path, 10) for r in batch]
                decisions = [d for _, _, ds in engine.decided_batches([results], expected_count=1) for d in ds]
                outputs.append(([r.evidence_json for r in results], decisions))
            finally:
                if indexed:
                    snapshot.close()
    assert outputs[0] == outputs[1]
