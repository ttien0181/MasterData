"""Small original bundle QA never claims unrun scales or uses truth to review."""
import importlib.util
from pathlib import Path


def helper():
    path = Path(__file__).resolve().parents[3] / 'scripts/verify_cluster_workflow.py'
    spec = importlib.util.spec_from_file_location('cluster_workflow_qa', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_zero_predicted_pairs_has_undefined_precision_and_junk_is_not_one_owner():
    result = helper().quality({'a': None, 'b': None, 'c': 'owner', 'd': 'owner'}, [], [['a'], ['b'], ['c'], ['d']])
    assert result['pair_precision'] is None
    assert result['positive_pairs'] == 1
    result = helper().quality({'a': None, 'b': None, 'c': 'owner', 'd': 'owner'}, [], [['a', 'b'], ['c', 'd']])
    assert result['pair_tp'] == 1
    assert result['pair_fp'] == 1
    assert result['junk_co_cluster_pairs'] == 1


def test_review_fixture_uses_only_effective_source_evidence():
    chosen = helper().fixture_choice({'decision': 'UNDECIDED', 'eligibility': {'manual_allowed': True},
        'baseline': {'decision': 'MERGEABLE', 'primary_group': 'A'}})
    assert chosen == 'merge'
    assert helper().fixture_choice({'decision': 'UNDECIDED', 'eligibility': {'manual_allowed': True},
        'baseline': {'decision': 'REVIEW', 'primary_group': 'D'}}) == 'no_merge'


def test_small_workflow_cannot_report_full_scale_or_end_to_end_without_export():
    status = helper().completion_status(actual=10022, expected=10022, finalized=False, exported=False, integrity=True)
    assert status == 'REVIEW_PENDING'
    assert helper().completion_status(actual=10021, expected=10022, finalized=True, exported=True, integrity=True) == 'INCOMPLETE_INPUT'
