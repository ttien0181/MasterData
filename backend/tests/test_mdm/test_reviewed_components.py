"""Reviewed connectivity is atomic; evidence exceptions cannot leak into strict checks."""
from contextlib import closing
from copy import deepcopy
from dataclasses import replace
from itertools import permutations

import pytest
from masterone.mdm.engine_adapter import engine_context
from masterone.mdm.pipeline.cluster_guards import check_component
from masterone.mdm.pipeline.cluster_policy import GuardBudget, load_policy
from masterone.mdm.pipeline.decision_store import DecisionStore
from masterone.mdm.pipeline.matching import value_frequencies
from masterone.mdm.pipeline.pair_assessment import assess_pair
from masterone.mdm.workflow_finalize import cluster_snapshot, compute_publication

from tests.test_mdm.test_pair_classification_regressions import record
from tests.test_mdm.test_safe_clustering import chain, edge


@pytest.fixture(autouse=True)
def context():
    with engine_context(dict(decision_semantics_v2_enabled=True, cluster_safety_v2_enabled=True)):
        yield


def snapshot(records, pairs):
    return dict(records=records, pairs=pairs, overlays={p['id']: 'ACCEPTED' for p in pairs},
                negative_pairs=[], review_reasons={}, policy_version='person-v3')


def test_conflicting_accepted_component_retains_all_members_for_every_edge_order():
    records = [record(rid) for rid in 'abcd']
    pairs = [{**assess_pair(records[i], records[i + 1]), 'id': str(i),
              'decision': 'BLOCKED' if i == 0 else 'REVIEW'} for i in range(3)]
    for order in permutations(pairs):
        source = snapshot(list(reversed(records)), list(order))
        result = cluster_snapshot(source, 'run')
        assert result['safe_clusters'] == []
        assert result['conflicts']
        assert all(c['record_ids'] == list('abcd') for c in result['conflicts'])
        assert sorted(result['conflicts'][0]['pair_ids']) == ['0', '1', '2']


def test_missing_bridge_exposes_exact_review_group_without_mutating_inputs():
    records = chain()
    pairs = [edge(records[0], records[1], None), edge(records[1], records[2], None)]
    for pair in pairs:
        pair['decision'] = 'REVIEW'
    source = snapshot(records, pairs)
    before = deepcopy(source)
    result = cluster_snapshot(source, 'run')
    group = result['review_groups'][0]
    assert group['member_record_ids'] == ['a', 'b', 'c']
    assert group['reason'] == 'CLUSTER_BRIDGE_MISSING_EVIDENCE'
    assert group['fingerprint']
    assert group['pair_ids'] == sorted(p['id'] for p in pairs)
    assert source == before


def test_strict_guard_does_not_reuse_missing_evidence_exception():
    records = chain()
    by_id = {r['id']: r for r in records}
    with closing(DecisionStore()) as store:
        store.seal()
        frequencies = value_frequencies(records)
        policy = load_policy()
        assert check_component(by_id, by_id, store, policy, GuardBudget(), frequencies,
                               allow_missing_evidence=True) == ''
        assert check_component(by_id, by_id, store, policy, GuardBudget(), frequencies) == 'CLUSTER_BRIDGE_MISSING_EVIDENCE'


def test_guard_cache_canonicalizes_endpoints_and_separates_policy_modes():
    with closing(DecisionStore()) as store:
        store.save_check('b', 'a', '', mode='exception')
        assert store.check('a', 'b', mode='exception') == ''
        assert store.check('a', 'b') is None


def test_snapshot_index_reuses_global_census_and_ignores_weak_region_bridge():
    from masterone.mdm.workflow_cluster_index import SnapshotIndex
    records = [record(rid) for rid in 'abcdef']
    pairs = [{**assess_pair(records[i], records[j]), 'id': f'{i}{j}', 'decision': decision}
             for i, j, decision in [(0, 1, 'REVIEW'), (2, 3, 'REVIEW'), (1, 2, 'INSUFFICIENT_EVIDENCE')]]
    source = snapshot(records, pairs)
    source['overlays']['12'] = 'UNDECIDED'
    index = SnapshotIndex(source)
    assert index.region({'a'}) == {'a', 'b'}
    assert list(index.accepted_components()) == [['a', 'b'], ['c', 'd']]
    subset = index.subset({'a', 'b'})
    assert subset['records'] == records[:2]
    assert SnapshotIndex(subset, frequencies=index.frequencies).frequencies is index.frequencies
    source['allow_partial'] = True
    assert compute_publication(source, 'run', 1)['publication']['covered_records'] == 6


@pytest.mark.parametrize('weights', [(None, None), (20, 1), (1, 20)])
def test_accepted_chain_is_independent_of_record_pair_id_and_weight_order(weights):
    records = [record(rid, government_id='001090123456') for rid in 'abc']
    for item in records:
        item['normalized_fields'].update(_government_id_verified=True, government_id_namespace='VN')
    pairs = [{**assess_pair(records[i], records[i + 1]), 'id': str(9 - i),
              'decision': 'REVIEW', 'match_weight': weights[i]} for i in range(2)]
    for order in permutations(pairs):
        result = cluster_snapshot(snapshot(list(reversed(records)), list(order)), 'run')
        assert [g['member_record_ids'] for g in result['safe_clusters']] == [['a', 'b', 'c']]
        assert result['conflicts'] == []


@pytest.mark.parametrize('constraint', ['deny', 'veto', 'source_unique'])
def test_whole_accepted_component_cannot_bypass_constraints(constraint):
    records = [record(rid) for rid in 'abc']
    pairs = [{**assess_pair(records[i], records[j]), 'id': f'{i}{j}', 'decision': 'REVIEW'}
             for i, j in [(0, 1), (1, 2), (0, 2)]]
    source = snapshot(records, pairs)
    if constraint == 'deny':
        source['negative_pairs'] = [['a', 'c']]
    elif constraint == 'veto':
        source['overlays']['02'] = 'REJECTED'
    else:
        for r in records:
            r['source_system'] = 'CRM'
    with engine_context(dict(decision_semantics_v2_enabled=True, cluster_safety_v2_enabled=constraint != 'source_unique')):
        result = cluster_snapshot(source, 'run')
    assert result['safe_clusters'] == []
    assert result['conflicts'][0]['record_ids'] == list('abc')


def test_guard_budget_and_policy_cache_are_not_bypassed():
    records = [record(rid, government_id='001090123456') for rid in 'abc']
    by_id = {r['id']: r for r in records}
    policy = load_policy()
    with closing(DecisionStore()) as store:
        store.seal()
        frequency = value_frequencies(records)
        budget = GuardBudget()
        assert check_component(by_id, by_id, store, replace(policy, max_pair_checks_per_run=1),
                               budget, frequency) == 'CLUSTER_GUARD_RUN_BUDGET'
        assert budget.checked == 1
        assert check_component(by_id, by_id, store, policy, GuardBudget(), frequency) == ''
        assert check_component(by_id, by_id, store, policy, GuardBudget(), frequency,
                               review_vetoes=frozenset({('a', 'b')})) == 'CLUSTER_REVIEW_VETO'


def test_prior_conflict_partial_component_is_evaluated_only_once(monkeypatch):
    from masterone.mdm.pipeline import matching
    records = [record(rid, government_id='001090123456') for rid in 'abc']
    pairs = [{**assess_pair(records[i], records[i + 1]), 'id': str(i), 'decision': 'REVIEW'} for i in range(2)]
    source = snapshot(records, pairs)
    source.update(allow_partial=True, known_conflicts=[{'id': 'conflict', 'pair_id': '0',
                                                     'record_ids': list('abc'), 'reason': 'OLD'}])
    calls = []
    compare = matching.compare_and_score_rule
    def counted(*args, **kwargs):
        calls.append(tuple(sorted((args[0]['id'], args[1]['id']))))
        return compare(*args, **kwargs)
    monkeypatch.setattr(matching, 'compare_and_score_rule', counted)
    result = compute_publication(source, 'run', 1)
    assert result['publication']['covered_records'] == 3
    assert len(calls) <= 6


@pytest.mark.parametrize('constraint', ['blocked', 'cannot_link'])
def test_pending_edge_excludes_all_accepted_dependencies_even_if_forbidden(constraint):
    records = [record(rid) for rid in 'abc']
    pairs = [{**assess_pair(records[i], records[i + 1]), 'id': str(i),
              'decision': 'BLOCKED' if i == 0 and constraint == 'blocked' else 'REVIEW'} for i in range(2)]
    source = snapshot(records, pairs)
    source['overlays']['1'] = 'DEFERRED'
    source['allow_partial'] = True
    if constraint == 'cannot_link':
        source['negative_pairs'] = [['a', 'b']]
    result = compute_publication(source, 'run', 1)
    assert result['publication']['covered_records'] == 0
    assert result['publication']['unresolved_records'] == 3


def test_accepted_mergeable_star_checks_consistency_once_per_identity_pair(monkeypatch):
    from masterone.mdm.pipeline import matching
    records = [record(f'{i:02}', government_id='001090123456') for i in range(20)]
    for r in records:
        r['normalized_fields'].update(_government_id_verified=True, government_id_namespace='VN')
    pairs = [{**assess_pair(records[0], r), 'id': r['id'], 'decision': 'MERGEABLE'} for r in records[1:]]
    calls = []
    compare = matching.compare_and_score_rule
    def counted(*args, **kwargs):
        calls.append((args[0]['id'], args[1]['id']))
        return compare(*args, **kwargs)
    monkeypatch.setattr(matching, 'compare_and_score_rule', counted)
    result = cluster_snapshot(snapshot(records, pairs), 'run')
    assert [c['member_record_ids'] for c in result['safe_clusters']] == [[r['id'] for r in records]]
    assert len(calls) <= 380


def test_auto_and_reviewed_memberships_share_strict_consistency_cache(monkeypatch):
    records = [record(rid, government_id='001090123456') for rid in 'abc']
    pairs = [{**assess_pair(records[i], records[i + 1]), 'id': str(i),
              'decision': 'MERGEABLE' if i == 0 else 'REVIEW'} for i in range(2)]
    source = snapshot(records, pairs)
    pairs[0]['triage_status'] = 'HIGH_CONFIDENCE_PROPOSAL'
    source['overlays']['0'] = 'UNDECIDED'
    calls = []
    lookup = DecisionStore.get
    def counted(self, a, b):
        calls.append(tuple(sorted((a, b))))
        return lookup(self, a, b)
    monkeypatch.setattr(DecisionStore, 'get', counted)
    result = cluster_snapshot(source, 'run')
    assert [c['member_record_ids'] for c in result['safe_clusters']] == [list('abc')]
    assert all(calls.count(pair) == 1 for pair in set(calls))
