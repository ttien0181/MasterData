"""Recall recovery remains retrieval evidence, with pinned opt-in and hard budgets."""
import random
import unicodedata

import pytest
from masterone.config import get_settings
from masterone.mdm.engine_adapter import CORE_FEATURES, engine_context
from masterone.mdm.pipeline.candidate_engine import CandidateConfig, CandidateEngine, CandidateWorkloadExceededError
from masterone.mdm.pipeline.standardization import standardize


def row(rid, name, dob='1987-08-23', kind='PERSON'):
    nf, flags = standardize({'name': name, 'dob': dob}, kind)
    return {'id': rid, 'entity_type': kind, 'normalized_fields': nf, 'raw_fields': {}, 'quality_flags': flags}


def isolated(**kw):
    return CandidateConfig(recall_v2=True, include_grams=False, include_tfidf=False, composites=(), **kw)


def test_recovers_shortened_name_across_chunks_and_unicode_forms():
    records = [row('z', unicodedata.normalize('NFD', 'Đặng Minh Khôi')), row('a', 'Khoi Minh')]
    engine = CandidateEngine(isolated())
    for r in records:
        engine.add_records([r])
    result = engine.finalize()
    assert result.pairs[('a', 'z')] == {'person_token_full_dob', 'person_tokens_month_day'}
    assert result.metrics['recall_v2']['enabled'] is True
    assert result.metrics['candidate_count'] == 1


def test_changed_year_retrieved_without_changing_normalized_dob():
    records = [row('a', 'Tran Ngoc Hanh'), row('b', 'Ngoc Hanh', '1988-08-23')]
    result = CandidateEngine(isolated()).generate(records)
    assert result.pairs == {('a', 'b'): {'person_tokens_month_day'}}
    assert records[1]['normalized_fields']['dob'] == '1988-08-23'


@pytest.mark.parametrize('kind', ['ORGANIZATION', 'UNKNOWN'])
def test_no_new_cross_type_or_nonperson_candidates(kind):
    records = [row('a', 'Le Thi Bich'), row('b', 'Thi Bich', kind=kind)]
    assert not CandidateEngine(isolated()).generate(records).pairs


@pytest.mark.parametrize('field,value', [('dob', None), ('dob', ''), ('dob', '1987'), ('name', None)])
def test_missing_and_partial_fields_do_not_expand_candidates(field, value):
    records = [row('a', 'Vo Thanh Tam'), row('b', 'Thanh Tam')]
    records[1]['normalized_fields'][field] = value
    assert not CandidateEngine(isolated()).generate(records).pairs


def test_oversized_common_name_blocks_are_reported_without_partial_output():
    records = [row(str(i), 'Tran Thanh Tam') for i in range(31)]
    result = CandidateEngine(isolated(name_block_size=1)).generate(records)
    assert not result.pairs
    recovery = [r for r in result.block_log if r['source'].startswith('person_')]
    assert recovery and all(r['reason'] == 'max_block_size' for r in recovery)
    assert result.metrics['recall_v2']['oversized_blocks'] == len(recovery)


def test_pair_budget_raises_and_never_returns_a_truncated_union():
    records = [row('a', 'Vo Thanh Tam'), row('b', 'Thanh Tam'), row('c', 'Tam Thanh')]
    with pytest.raises(CandidateWorkloadExceededError):
        CandidateEngine(isolated(max_unique_pairs=1)).generate(records)
    with pytest.raises(CandidateWorkloadExceededError):
        CandidateEngine(isolated(recall_v2_pair_budget=1)).generate(records)


def test_deterministic_provenance_and_rollback_preserve_legacy_pairs():
    records = [row('a', 'Vo Thanh Tam'), row('b', 'Thanh Tam'), row('c', 'Tran Ngoc Ha')]
    legacy = CandidateEngine(CandidateConfig(recall_v2=False)).generate(records)
    expected = CandidateEngine(CandidateConfig(recall_v2=True)).generate(records)
    assert set(legacy.pairs) <= set(expected.pairs)
    for seed in range(3):
        shuffled = records.copy()
        random.Random(seed).shuffle(shuffled)
        assert CandidateEngine(CandidateConfig(recall_v2=True)).generate(shuffled) == expected
    assert CandidateEngine(CandidateConfig(recall_v2=False)).generate(records) == legacy


def test_flag_is_off_by_default_and_old_pinned_profiles_stay_off(monkeypatch):
    monkeypatch.setenv('CANDIDATE_RECALL_V2', 'true')
    get_settings.cache_clear()
    try:
        assert CandidateConfig().recall_v2 is True
        with engine_context({name: True for name in CORE_FEATURES}):
            assert CandidateConfig().recall_v2 is False
        with engine_context({**{name: True for name in CORE_FEATURES}, 'candidate_recall_v2': True}):
            assert CandidateConfig().recall_v2 is True
    finally:
        get_settings.cache_clear()


def test_registry_snapshot_uses_pinned_flag_in_every_config_section(monkeypatch):
    from masterone.mdm.matching_foundation_service import baseline_config
    monkeypatch.setenv('CANDIDATE_RECALL_V2', 'true')
    get_settings.cache_clear()
    try:
        flags = {name: False for name in CORE_FEATURES}
        config = baseline_config(flags)
        assert config['blocking']['recall_v2'] is False
        assert config['runtime_identity']['candidate']['recall_v2'] is False
        assert baseline_config()['blocking']['recall_v2'] is True
        monkeypatch.setenv('CANDIDATE_RECALL_V2', 'false')
        get_settings.cache_clear()
        assert baseline_config({**flags, 'candidate_recall_v2': True})['blocking']['recall_v2'] is True
    finally:
        get_settings.cache_clear()


def test_untrusted_quality_and_long_names_are_not_recovery_keys():
    records = [row('a', 'Vo Thanh Tam'), row('b', 'Thanh Tam')]
    records[1]['normalized_fields']['_field_quality']['dob'] = 'INVALID'
    assert not CandidateEngine(isolated()).generate(records).pairs
    records[1] = row('b', 'Thanh Tam ' + ' '.join('token' + chr(97+i) for i in range(13)))
    assert not CandidateEngine(isolated()).generate(records).pairs


def test_hybrid_union_keeps_unique_pass_order_and_incremental_counts():
    from masterone.mdm.matching_agents.candidate_union import build_union
    records = [row('a', 'Vo Thanh Tam'), row('b', 'Thanh Tam')]
    cfg = {'experiment': 'E0', 'retrieval': {}, 'execution_budget': {
        'unique_pairs': 200000, 'warning_pairs': 150000, 'similarity_cells': 100000000,
        'workspace_bytes': 512 * 1024**2, 'run_seconds': 1800}}
    with engine_context({**{name: False for name in CORE_FEATURES}, 'candidate_recall_v2': True}):
        result = build_union(records, cfg, None, [], lambda *_: None)
    order = result.metrics['pass_order']
    assert len(order) == len(set(order))
    assert sum(s['new_unique'] for s in result.metrics['passes'].values()) == len(result.pairs)


def test_parquet_batches_keep_recovery_sources_and_entity_policy(tmp_path):
    import json

    import pyarrow as pa
    import pyarrow.parquet as pq
    from masterone.mdm.pipeline.stage_runner import StageRunner
    records = [row('a', 'Vo Thanh Tam'), row('b', 'Thanh Tam'), row('c', 'Tam Thanh', kind='ORGANIZATION')]
    source = tmp_path / 'normalized.parquet'
    pq.write_table(pa.Table.from_pylist([{'id':r['id'], 'entity_type':r['entity_type'],
        'normalized_fields':json.dumps(r['normalized_fields'])} for r in records]), source)
    cfg = isolated()
    runner = StageRunner('recall-test', 'attempt', output_dir=tmp_path)
    try:
        output = runner.build_candidates(str(source), str(tmp_path/'candidates.parquet'), cfg, batch_size=1)
        assert output.pairs == CandidateEngine(cfg).generate(records).pairs
        assert set(output.pairs) == {('a','b')}
    finally:
        runner.con.close()


def test_retrieved_pair_still_obeys_verified_dob_and_cannot_link_guards():
    from masterone.mdm.pipeline.pair_assessment import assess_pair
    records = [row('a', 'Vo Thanh Tam'), row('b', 'Thanh Tam', '1988-08-23')]
    for r in records:
        r['normalized_fields'].update(dob_verification_status='verified', _dob_verified=True)
    assert ('a','b') in CandidateEngine(isolated()).generate(records).pairs
    assert assess_pair(*records)['decision'] == 'NOT_MERGEABLE'
    records[1]['normalized_fields']['dob'] = records[0]['normalized_fields']['dob']
    assert assess_pair(*records, negative_pairs=frozenset({('a','b')}))['decision'] == 'NOT_MERGEABLE'
