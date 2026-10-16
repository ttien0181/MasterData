"""Hand-calculated protocol examples, independent of matcher implementation."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from m0_evaluation import evaluate_pairs  # noqa: E402
from standalone.contracts.labels import PairLabel  # noqa: E402
from standalone.evaluation.clusters import cluster_metrics  # noqa: E402


def test_spec_six_pair_example_counts_review_and_blocking_miss_as_fn():
    labels = {"P1": PairLabel.POSITIVE, "P2": PairLabel.NEGATIVE,
              "P3": PairLabel.POSITIVE, "P4": PairLabel.POSITIVE,
              "P5": PairLabel.NEGATIVE, "P6": PairLabel.UNKNOWN}
    decisions = {"P1": "MATCH", "P2": "MATCH", "P3": "REVIEW",
                 "P5": "NON_MATCH", "P6": "MATCH"}
    result = evaluate_pairs(labels, decisions, set(decisions))
    binary = result["binary_end_to_end"]
    assert [binary[key]["value"] for key in ("tp", "fp", "fn", "tn")] == [1, 1, 2, 1]
    assert binary["precision"]["value"] == .5
    assert binary["recall"]["value"] == pytest.approx(1 / 3)
    assert binary["f1"]["value"] == .4
    assert result["coverage"]["positive_blocking_misses"] == 1
    assert result["coverage"]["sampled_positive_candidate_recall"]["value"] == pytest.approx(2 / 3)
    assert result["coverage"]["unknown_predictions_excluded"] == 1
    assert result["candidate_conditioned"]["recall"]["value"] == .5
    assert result["coverage"]["decision_coverage_on_known_labels"]["value"] == .6


def test_zero_denominators_are_null_with_reasons():
    result = evaluate_pairs({"unknown": PairLabel.UNKNOWN}, {"unknown": "MATCH"}, {"unknown"})
    for key in ("precision", "recall", "f1"):
        assert result["binary_end_to_end"][key]["value"] is None
        assert result["binary_end_to_end"][key]["reason"]
    assert result["coverage"]["sampled_positive_candidate_recall"]["value"] is None


def test_no_prediction_does_not_turn_unknown_into_negative():
    result = evaluate_pairs({"p": PairLabel.POSITIVE, "n": PairLabel.NEGATIVE,
                             "u": PairLabel.UNKNOWN}, {}, set())
    binary = result["binary_end_to_end"]
    assert [binary[key]["value"] for key in ("tp", "fp", "fn", "tn")] == [0, 0, 1, 1]
    assert binary["precision"]["value"] is None
    assert binary["recall"]["value"] == 0
    assert binary["f1"]["value"] == 0


def test_predictions_outside_candidate_universe_cannot_improve_recall():
    result = evaluate_pairs({"miss": PairLabel.POSITIVE}, {"miss": "MATCH"}, set())
    assert result["binary_end_to_end"]["fn"]["value"] == 1
    assert result["binary_end_to_end"]["tp"]["value"] == 0


def test_cluster_metrics_block_unverified_entity_gold():
    result = cluster_metrics({"a": "c", "b": "c"}, {"a": "e", "b": "e"},
                             expected_record_ids=frozenset({"a", "b"}),
                             entity_gold_complete_verified=False)
    assert result["pairwise_precision"].value is None
    assert result["b3_recall"].value is None


def test_verified_toy_cluster_counts_and_bcubed():
    # Gold AB/C; prediction A/BC: no true predicted link.
    result = cluster_metrics({"a": "x", "b": "y", "c": "y"},
                             {"a": "e", "b": "e", "c": "f"},
                             expected_record_ids=frozenset({"a", "b", "c"}),
                             entity_gold_complete_verified=True)
    assert result["pairwise_precision"].value == 0
    assert result["pairwise_recall"].value == 0
    assert result["pairwise_f1"].value == 0
    assert result["b3_precision"].value == pytest.approx(2 / 3)
    assert result["b3_recall"].value == pytest.approx(2 / 3)
