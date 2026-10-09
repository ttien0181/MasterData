"""Validation must not invent negative labels or hide business differences."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


def harness():
    path = Path(__file__).resolve().parents[3] / "scripts/merge_shadow_validation.py"
    spec = importlib.util.spec_from_file_location("shadow_validation", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_unknown_predictions_do_not_become_false_positives():
    module = harness()
    labels = {("a", "b"): 1, ("a", "c"): 0}
    result = module.labeled_metrics({("a", "b")}, {("a", "b"), ("x", "y")}, labels, [["a", "b", "c"]])
    assert result["tp"] == 1 and result["fp"] == 0 and result["tn"] == 1
    assert result["unknown_predictions"] == 1 and result["precision"] == 1
    assert result["clusters_violating_known_negatives"] == 1
    assert result["known_negative_cluster_pairs"] == 1


def test_candidate_misses_count_as_end_to_end_false_negatives():
    module = harness()
    result = module.labeled_metrics(set(), set(), {("a", "b"): 1}, [])
    assert result["candidate_recall"] == 0 and result["fn"] == 1
    assert result["recall"] == 0 and result["precision"] is None
    assert result["f1"] == 0


def test_canonicalization_preserves_golden_and_decision_changes():
    module = harness()
    first = {"pairs": [{"id": "b", "decision": "REVIEW"}, {"id": "a", "decision": "MERGEABLE"}],
        "proposals": [{"id": "p", "golden_fields": {"name": "source-value"}}], "review_cases": []}
    second = {**first, "pairs": list(reversed(first["pairs"]))}
    assert module.digest(module.canonical_business(first)) == module.digest(module.canonical_business(second))
    second = {**second, "proposals": [{"id": "p", "golden_fields": {"name": "override"}}]}
    assert module.digest(module.canonical_business(first)) != module.digest(module.canonical_business(second))


def test_batch_config_pin_is_explicit_but_decision_changes_fail_equivalence():
    module = harness()
    def measured(pin, decision):
        business = {"pairs": [{"id": "p", "left_id": "a", "right_id": "b", "decision": decision,
            "scoring_config_hash": pin}], "proposals": [], "review_cases": []}
        return {"name": pin, "business": business, "business_hash": module.digest(business),
            "candidates": [["a", "b", ["exact_name"]]], "scores": {"a|b": {"rule_score": 1.0}}}
    result = module.compare(measured("batch-17", "REVIEW"), measured("batch-512", "REVIEW"))
    assert result["business_equal"] is False
    assert result["business_equal_except_scoring_config_hash"] is True
    result = module.compare(measured("batch-17", "REVIEW"), measured("batch-512", "MERGEABLE"))
    assert result["business_equal_except_scoring_config_hash"] is False


@pytest.mark.parametrize("exit_code,business_equal", [(1, True), (0, False)])
def test_matrix_does_not_report_success_for_failed_or_divergent_runs(tmp_path, monkeypatch, exit_code, business_equal):
    module = harness()
    monkeypatch.syspath_prepend(str(module.ROOT / "scripts"))
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=exit_code))
    monkeypatch.setattr(module, "summarize", lambda _: {"comparisons": [{
        "comparison_kind": "invariance", "business_equal_except_scoring_config_hash": business_equal}]})
    assert module.matrix(tmp_path, timeout=1) == 1
