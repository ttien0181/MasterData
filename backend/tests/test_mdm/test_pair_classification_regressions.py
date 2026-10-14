"""Regression contracts for pair evidence, grouping and manual-only assessment."""
from copy import deepcopy
import cProfile

import pytest

from masterone.mdm.engine_adapter import engine_context
from masterone.mdm.pipeline.matching import _dob_state, compare_and_score_rule, value_frequencies
from masterone.mdm.pipeline.pair_assessment import assess_pair, assessment_profile
from masterone.mdm.pipeline.review_grouping import _derive_group, classify_pair
from masterone.mdm.pipeline.standardization import standardize
from masterone.mdm.pipeline.scoring_engine import MemorySnapshot, ParquetSnapshot, ScoringEngine
from masterone.mdm.workflow_assessment import compute_assessment
from masterone.mdm.workflow_models import WorkflowPair
from masterone.mdm.workflow_review import eligibility
from masterone.mdm.workflow_review_rules import review_required


def record(rid, **fields):
    raw = dict(name='Nguyen Van An', phone='0912345678', email='an.nguyen@example.net',
               dob='1990-05-15', address='123 Nguyen Trai, Thanh Xuan, Ha Noi')
    raw.update(fields)
    nf, flags = standardize(raw, 'PERSON')
    return dict(id=rid, external_id=rid, source_system=rid, entity_type='PERSON',
                raw_fields=raw, normalized_fields=nf, quality_flags=flags)


@pytest.fixture(autouse=True)
def assessment_context():
    with engine_context(assessment_profile()):
        yield


@pytest.mark.parametrize('field,first,shared', [
    ('phone', '0911111111', '0922222222'),
    ('email', 'first@example.net', 'shared.user@example.net'),
])
def test_weight_uses_the_contact_actually_matched_in_both_pair_orders(field, first, shared):
    a = record('a', **{field: f'{first};{shared}'})
    b = record('b', **{field: shared})
    extra = record('extra', **{field: first})
    frequencies = value_frequencies([a, b, extra])
    ab = compare_and_score_rule(a, b, set(), frequencies)
    ba = compare_and_score_rule(b, a, set(), frequencies)
    assert ab['score'] == ba['score']
    ev = next(e for e in ab['field_evidence'] if e['field'] == field)
    assert ev.get('matched_values') == [shared]
    assert ev.get('matched_frequency') == 2


@pytest.mark.parametrize('field,value', [('phone', '0944444444'), ('email', 'shared.user@example.net')])
def test_frequency_counts_valid_secondary_values_once_per_record(field, value):
    first = '0911111111' if field == 'phone' else 'first@example.net'
    records = [record(str(i), **{field: f'{first};{value};{value}'}) for i in range(6)]
    frequencies = value_frequencies(records)
    assert frequencies[field][value] == 6
    comparison = compare_and_score_rule(records[0], records[1], set(), frequencies)
    assert comparison['comparisons'][field]['state'] == 'SH'
    assert assess_pair(records[0], records[1], frequencies)['decision'] != 'MERGEABLE'


def test_invalid_placeholder_and_role_contacts_are_not_counted_as_valid_contacts():
    records = [record('a', phone='0000000000;0911111111;short', email='info@example.net;broken;a@example.net')]
    frequencies = value_frequencies(records)
    assert dict(frequencies['phone']) == {'0911111111': 1}
    assert dict(frequencies['email']) == {'a@example.net': 1}


@pytest.mark.parametrize('snapshot_kind', ['memory', 'parquet'])
def test_production_census_uses_secondary_contacts_for_classification(tmp_path, snapshot_kind):
    import duckdb
    from tests.test_mdm.test_scoring_engine import write_snapshot

    records = [record(str(i), phone=f'091000000{i};0944444444',
                      email='an.nguyen@example.net' if i < 2 else f'user{i}@example.net')
               for i in range(6)]
    with duckdb.connect() as con:
        snapshot = MemorySnapshot(records)
        if snapshot_kind == 'parquet':
            path = tmp_path / 'records.parquet'
            write_snapshot(path, records)
            snapshot = ParquetSnapshot(path, con)
        try:
            engine = ScoringEngine(snapshot)
            assert engine.frequencies == value_frequencies(records)
            assert engine.frequencies['phone']['0944444444'] == 6
            scored = engine.score_batch([dict(left_id='0', right_id='1', reasons=['exact_name'])])[0]
            decided = engine.decide(scored, {r['id']: r for r in records})
            assert decided['comparisons']['phone']['state'] == 'SH'
            assert decided['decision'] == 'REVIEW'
            assert decided['subgroup'] == 'A2_NAME_EMAIL'
        finally:
            if snapshot_kind == 'parquet':
                snapshot.close()


def test_scoring_output_is_fenced_when_grouping_rules_change(tmp_path, monkeypatch):
    from masterone.mdm.pipeline import pair_grouping, scoring_engine
    from masterone.mdm.pipeline.scoring_engine import ScoringInputChangedError
    from pathlib import Path

    engine = ScoringEngine(MemorySnapshot([record('a'), record('b')]))
    original_hash = scoring_engine.file_hash
    grouping_path = Path(pair_grouping.__file__)
    monkeypatch.setattr(scoring_engine, 'file_hash', lambda path:
                        'changed-grouping' if Path(path) == grouping_path else original_hash(path))
    with pytest.raises(ScoringInputChangedError):
        engine.write_scores([dict(left_id='a', right_id='b', reasons=['exact_name'])],
                            tmp_path / 'scores.parquet', expected_count=1)


def test_current_scoring_assessment_and_fallback_agree_on_unverified_id_conflict():
    from masterone.mdm.pipeline.matching import score_pair
    records = [record('a', government_id='001090123456'),
               record('b', government_id='001090123457')]
    scored = score_pair(*records, set())
    assessed = assess_pair(*records)
    expected = ('D', 'D_UNVERIFIED_ID_CONFLICT')
    assert (scored['primary_group'], scored['subgroup']) == expected
    assert (assessed['primary_group'], assessed['subgroup']) == expected
    assert _derive_group(scored) == _derive_group(assessed) == expected
    assert scored['decision'] == assessed['decision'] == 'REVIEW'
    assert scored['unverified_identifier_relation'] == 'DIFFERENT_VALUE'


def test_multiple_contact_matches_choose_one_symmetric_anchor_without_double_counting():
    a = record('a', phone='0911111111;0922222222')
    b = record('b', phone='0922222222;0911111111')
    other = record('other', phone='0911111111')
    frequencies = value_frequencies([a, b, other])
    ab = compare_and_score_rule(a, b, set(), frequencies)
    ba = compare_and_score_rule(b, a, set(), frequencies)
    assert ab['score'] == ba['score']
    phone = next(e for e in ab['field_evidence'] if e['field'] == 'phone')
    assert phone.get('matched_values') == ['0911111111', '0922222222']
    assert phone.get('matched_frequency') == 2


@pytest.mark.parametrize('left,right,want', [
    ('1990-05', '1990-05-15', 'CL'),
    ('1990-05', '1990-05', 'CL'),
    ('1990', '1990', 'CL'),
    ('1990', '1990-05-15', 'CL'),
    ('1990-05', '1990-06-15', 'CF'),
    ('1990-05', '1991-05-15', 'CF'),
    ('1990-05-15', '1990-05-15', 'EX'),
    ('1990-05-15', '1990-05-16', 'CF'),
    ('1990-05-06', '1990-06-05', 'CL'),
])
def test_dob_uses_shared_known_components_and_full_date_exactness(left, right, want):
    a, b = record('a', dob=left), record('b', dob=right)
    assert _dob_state(a['normalized_fields'], b['normalized_fields'])[0] == want
    assert _dob_state(b['normalized_fields'], a['normalized_fields'])[0] == want


def test_compatible_partial_verified_dob_does_not_block_manual_review():
    a = record('a', dob='1990-05', dob_verified='true')
    b = record('b', dob_verified='true')
    result = assess_pair(a, b)
    assert result['primary_group'] == 'A'
    assert 'VERIFIED_DOB_CONFLICT' not in result['constraint_codes']


def test_clear_mergeable_assessment_still_requires_a_human_and_disables_auto_merge():
    result = assess_pair(record('a'), record('b'))
    assert result['decision'] == 'MERGEABLE'
    assert result['triage_status'] == 'REVIEW'
    pair = WorkflowPair(id='pair', run_id='r', baseline={}, assessment=result, decision='UNDECIDED')
    assert eligibility(pair) == dict(manual_allowed=True, auto_allowed=False, blocked_reasons=[])
    assert review_required(pair.decision, result)


def test_unverified_id_conflict_is_review_in_both_rule_and_assessment():
    a = record('a', government_id='001090123456')
    b = record('b', government_id='001090123457')
    result = assess_pair(a, b)
    assert result['rule_conclusion'] == 'REVIEW'
    assert (result['primary_group'], result['decision']) == ('D', 'REVIEW')


def test_new_assessment_and_fallback_share_specific_subgroup_but_stored_history_is_retained():
    result = assess_pair(record('a'), record('b'))
    assert _derive_group(result) == ('A', 'A3_NAME_PHONE_DOB')
    historical = {**result, 'primary_group': 'A', 'subgroup': 'A1_NAME_PHONE', 'triage_status': 'REVIEW'}
    assert classify_pair(historical, {})['subgroup'] == 'A1_NAME_PHONE'


def test_batch_assessment_preserves_inputs_and_prepares_each_record_only_once():
    records = [record('a'), record('b')]
    before = deepcopy(records)
    pairs = [dict(id=str(i), left_id='a', right_id='b') for i in range(3)]
    profiler = cProfile.Profile()
    profiler.enable()
    result = compute_assessment(records, pairs, {}, frozenset())
    profiler.disable()
    assert records == before
    assert all(p['decision'] == 'MERGEABLE' and p['triage_status'] == 'REVIEW' for p in result['pairs'])
    stats = profiler.getstats()
    calls = {name:sum(s.callcount for s in stats if getattr(s.code, 'co_name', None) == name)
             for name in ('assessment_record', 'person_constraints')}
    assert calls['assessment_record'] == 2
    assert calls['person_constraints'] == 3


def test_missing_anchor_without_support_remains_insufficient_after_batch_assessment():
    result = assess_pair(record('a', phone='', email='', dob='', address='Ha Noi'),
                         record('b', phone='', email='', dob='', address='Ha Noi'))
    assert (result['primary_group'], result['decision']) == ('C', 'INSUFFICIENT_EVIDENCE')


def test_manual_only_mergeable_is_not_auto_eligible_in_the_shared_decision_contract():
    from masterone.mdm.pipeline.decision_semantics import adapt_evaluation
    result = assess_pair(record('a'), record('b'))
    _, evaluation, permission = adapt_evaluation(result)
    assert permission.manual_allowed and not permission.auto_allowed
    assert evaluation.routing == 'HUMAN_REVIEW'


def test_cluster_snapshot_waits_for_accepting_a_manual_only_mergeable_pair():
    from masterone.mdm.workflow_finalize import cluster_snapshot
    records = [record('a'), record('b')]
    pair = {**assess_pair(*records), 'id': 'p'}
    snapshot = dict(records=records, pairs=[pair], overlays={}, negative_pairs=[], policy_version='test')
    result = cluster_snapshot(snapshot, 'test')
    assert result['safe_clusters'] == []
    assert result['conflicts'] and result['conflicts'][0]['pair_id'] == 'p'
    accepted = cluster_snapshot({**snapshot, 'overlays': {'p': 'ACCEPTED'}}, 'test')
    assert not accepted['conflicts']
    assert [c['member_record_ids'] for c in accepted['clusters']] == [['a', 'b']]


def test_verified_id_conflict_reports_only_the_verified_constraint():
    common = dict(government_id_verified='true', government_id_namespace='VN')
    result = assess_pair(record('a', government_id='001090123456', **common),
                         record('b', government_id='001090123457', **common))
    assert (result['primary_group'], result['decision']) == ('BLOCKED', 'NOT_MERGEABLE')
    assert result['constraint_codes'] == ['VERIFIED_GOV_ID_CONFLICT']
