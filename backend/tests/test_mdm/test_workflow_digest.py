"""Canonical hashes remain compatible while large strings and snapshots stream."""
import json
import tracemalloc
from hashlib import sha256

import pytest
from masterone.mdm import generation_staging
from masterone.mdm.engine_adapter import engine_context
from masterone.mdm.generation_staging import digest, encode
from masterone.mdm.pipeline.pair_assessment import assess_pair
from masterone.mdm.workflow_finalize import compute_publication
from masterone.mdm.workflow_resolution import repair_preview_groups

from tests.test_mdm.test_decision_semantics import record


@pytest.mark.parametrize('value', [None, True, False, 0, -123, 1.125, -0.0, 1e-8,
    '', 'Tiếng Việt \u2028', '\"\\\n\b\t\x00', [], (), {}, [1, 'x', None],
    {'b': 1, 'a': [True, {'name': 'Nguyễn'}]}, {2: 'b', 1: 'a'}, {None: 'empty'},
    {False: 'no', True: 'yes'}, {'text': 'a' * 10000}, [[[[]]]]])
def test_canonical_digest_matches_existing_encoding(value):
    assert digest(value) == sha256(encode(value)).hexdigest()


def test_canonical_digest_matches_independent_literal():
    expected = sha256('{"a":1,"b":"Tiếng Việt"}'.encode()).hexdigest()
    assert digest({'b': 'Tiếng Việt', 'a': 1}) == expected


@pytest.mark.parametrize('value', [float('nan'), float('inf'), -float('inf'),
                                 {'bad': float('nan')}, {float('inf'): 1}])
def test_nonfinite_values_remain_rejected(value):
    with pytest.raises(ValueError):
        digest(value)


def test_circular_data_remains_rejected():
    value = []
    value.append(value)
    with pytest.raises(ValueError, match='Circular'):
        digest(value)


def test_unsupported_values_and_mixed_key_types_remain_rejected():
    with pytest.raises(TypeError):
        digest(object())
    with pytest.raises(TypeError):
        digest({'a': 1, None: 2})


def test_digest_does_not_encode_whole_snapshot(monkeypatch):
    value = {'records': [{'id': str(i), 'name': 'Nguyễn'} for i in range(1100)]}
    expected = sha256(encode(value)).hexdigest()
    def no_full_encode(_):
        raise AssertionError('Whole snapshot encoding must not be used')
    monkeypatch.setattr(generation_staging, 'encode', no_full_encode)
    assert digest(value) == expected


def test_large_string_hash_allocation_is_bounded():
    value = '\"\nNguyễn' * 200000
    expected = sha256(json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()
    tracemalloc.start()
    try:
        actual = digest(value)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert actual == expected
    assert peak < 1024 * 1024


@pytest.mark.parametrize('partial', [False, True])
def test_repair_preview_matches_publication_when_pair_still_pending(partial):
    a, b, c = record('a'), record('b'), record('c', name='Other person', phone='0987654321')
    profile = dict(decision_semantics_v2_enabled=True, cluster_safety_v2_enabled=True)
    pair = {**assess_pair(a, b), 'id': 'ab'}
    snapshot = dict(records=[a, b, c], pairs=[pair], overlays={}, review_reasons={},
                    negative_pairs=[], policy_version='person-v3', allow_partial=partial)
    with engine_context(profile):
        _, groups = repair_preview_groups(snapshot, 'run', 2)
        publication = compute_publication(snapshot, 'run', 2)
    assert [g['member_record_ids'] for g in groups] == [['c']]
    assert groups == publication['clusters']


def test_repair_preserves_disconnected_safe_group_despite_conflict():
    a, b, c, d = [record(rid) for rid in 'abcd']
    pairs = [{**assess_pair(a, b), 'id': 'ab'}, {**assess_pair(c, d), 'id': 'cd'}]
    snapshot = dict(records=[a, b, c, d], pairs=pairs, overlays={'cd': 'ACCEPTED'},
                    review_reasons={}, negative_pairs=[], policy_version='person-v3')
    with engine_context(dict(decision_semantics_v2_enabled=True, cluster_safety_v2_enabled=True)):
        _, groups = repair_preview_groups(snapshot, 'run', 2)
    assert [g['member_record_ids'] for g in groups] == [['c', 'd']]
def test_sequence_digest_matches_array_without_materialization():
    from masterone.mdm.generation_staging import digest, digest_sequence
    values = [{'name': 'Nguyễn An', 'empty': None}, [0, 1.25, False], 'quote"']
    for items in ([], values):
        assert digest_sequence(iter(items)) == digest(items)


def test_canonical_chunks_encode_small_subtrees_in_bounded_blocks():
    value = [{'id': str(i), 'fields': {'name': 'Nguyễn\n"', 'score': 1.25}} for i in range(1100)]
    chunks = list(generation_staging._canonical_chunks(value))
    assert b''.join(chunks) == encode(value)
    assert len(chunks) < 4 * len(value)
    assert max(map(len, chunks)) <= 32768
