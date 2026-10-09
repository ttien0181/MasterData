from __future__ import annotations

from pathlib import Path

import pytest

from standalone.baselines.exact import ExactRuleBaseline
from standalone.baselines.weighted_v1 import WeightedV1Reimplementation
from standalone.contracts.core import (
    Decision, DatasetMetadata, GoldCoverage, GoldCoverageKind, Pair, PairPrediction,
    Probability, Record, Similarity, TaskType,
)
from standalone.contracts.labels import PairLabel
from standalone.datasets.universe import LegalPairUniverse
from standalone.evaluation.clusters import ClusterValidationEvidence, cluster_metrics
from standalone.evaluation.metrics import MetricStatus, blocking_metrics, pair_metrics
from standalone.runtime.resources import ResourceTracker


def pred(pair_id: str, score: float = .5, decision: Decision = Decision.NO_MATCH) -> PairPrediction:
    return PairPrediction(pair_id, Similarity(score), decision)


def test_hand_computed_pair_metrics_perfect_all_wrong_and_no_predictions() -> None:
    labels = {"p1": PairLabel.POSITIVE, "p2": PairLabel.POSITIVE,
              "n1": PairLabel.NEGATIVE, "n2": PairLabel.NEGATIVE}
    perfect = pair_metrics(labels, predicted_positive={"p1", "p2"})
    assert (perfect["tp"].value, perfect["fp"].value, perfect["fn"].value, perfect["tn"].value) == (2, 0, 0, 2)
    assert perfect["precision"].value == perfect["recall"].value == perfect["f1"].value == 1

    wrong = pair_metrics(labels, predicted_positive={"n1", "n2"})
    assert (wrong["tp"].value, wrong["fp"].value, wrong["fn"].value, wrong["tn"].value) == (0, 2, 2, 0)
    assert wrong["precision"].value == wrong["recall"].value == wrong["f1"].value == 0
    assert wrong["false_positive_rate"].value == wrong["false_non_match_rate"].value == 1

    none = pair_metrics(labels)
    assert (none["tp"].value, none["fp"].value, none["fn"].value, none["tn"].value) == (0, 0, 2, 2)
    assert none["precision"].status is MetricStatus.NOT_APPLICABLE
    assert none["recall"].value == 0 and none["f1"].value == 0


def test_hand_computed_no_positive_gold_all_negative_and_unknown_pairs() -> None:
    no_positive = pair_metrics({"a": PairLabel.NEGATIVE, "b": PairLabel.NEGATIVE},
                               predicted_positive={"a"})
    assert no_positive["precision"].value == 0
    assert no_positive["recall"].status is MetricStatus.NOT_APPLICABLE
    assert no_positive["f1"].value == 0
    assert no_positive["false_positive_rate"].value == .5

    all_negative = pair_metrics({"a": PairLabel.NEGATIVE, "b": PairLabel.NEGATIVE})
    assert all_negative["tn"].value == 2
    assert all_negative["false_positive_rate"].value == 0
    assert all_negative["average_precision"].status is MetricStatus.NOT_APPLICABLE

    partial = pair_metrics({"p": PairLabel.POSITIVE, "n": PairLabel.NEGATIVE, "u": PairLabel.UNKNOWN},
                           predicted_positive={"p", "u"})
    assert (partial["tp"].value, partial["fp"].value, partial["fn"].value, partial["tn"].value) == (1, 0, 0, 1)
    assert partial["labeled_pair_count"].value == 2
    assert partial["unknown_pair_count"].value == 1


def test_hand_computed_average_precision_brier_and_calibration_gate() -> None:
    labels = {"p1": PairLabel.POSITIVE, "p2": PairLabel.POSITIVE,
              "n1": PairLabel.NEGATIVE, "n2": PairLabel.NEGATIVE}
    scores = {"p1": .9, "n1": .8, "p2": .7, "n2": .1}
    preds = {key: PairPrediction(key, Similarity(value), Decision.NO_MATCH) for key, value in scores.items()}
    report = pair_metrics(labels, preds)
    assert report["average_precision"].value == pytest.approx(5 / 6)
    assert report["brier_score"].status is MetricStatus.NOT_APPLICABLE
    probabilities = {"p": PairPrediction("p", Probability(.8), Decision.AUTO_MATCH),
                     "n": PairPrediction("n", Probability(.4), Decision.NO_MATCH)}
    brier_unverified = pair_metrics({"p": PairLabel.POSITIVE, "n": PairLabel.NEGATIVE}, probabilities)
    assert brier_unverified["brier_score"].status is MetricStatus.NOT_APPLICABLE
    brier = pair_metrics({"p": PairLabel.POSITIVE, "n": PairLabel.NEGATIVE}, probabilities,
                         calibrated_probabilities=True)
    assert brier["brier_score"].value == pytest.approx(.1)
    no_pred = pair_metrics({"p": PairLabel.POSITIVE, "n": PairLabel.NEGATIVE})
    assert no_pred["average_precision"].status is MetricStatus.NOT_APPLICABLE


def test_partial_labels_never_infer_cartesian_true_negatives() -> None:
    result = pair_metrics({"labeled_positive": PairLabel.POSITIVE}, predicted_positive=set(),
                          coverage=GoldCoverage(pair_labels=GoldCoverageKind.SAMPLED_LABELED_PAIRS))
    assert result["tn"].value == 0
    assert result["label_scope"].status is MetricStatus.NOT_APPLICABLE
    assert result["coverage_at_target_precision"].status is MetricStatus.BLOCKED


def test_coverage_at_target_precision_uses_frozen_threshold_without_search() -> None:
    labels = {"p": PairLabel.POSITIVE, "n": PairLabel.NEGATIVE}
    predictions = {"p": pred("p", .9), "n": pred("n", .1)}
    met = pair_metrics(labels, predictions, target_precision=.95, coverage_threshold=.8)
    assert met["coverage_at_target_precision"].value == .5
    failed = pair_metrics(labels, predictions, target_precision=.95, coverage_threshold=.05)
    assert failed["coverage_at_target_precision"].status is MetricStatus.NOT_APPLICABLE


def test_predictions_outside_labeled_universe_are_ignored_as_unknown() -> None:
    result = pair_metrics({"known": PairLabel.POSITIVE},
                          {"known": pred("known"), "unlisted": pred("unlisted", .9, Decision.AUTO_MATCH)})
    assert result["tp"].value == result["fp"].value == result["tn"].value == 0
    assert result["fn"].value == 1
    assert result["unlabeled_prediction_count"].value == 1


def test_hand_computed_blocking_recall_and_reduction_ratio() -> None:
    records = [Record(str(i), "A", {"name": str(i)}) for i in range(4)]
    meta = DatasetMetadata("fixture", "1", TaskType.DEDUPE, ("A",))
    universe = LegalPairUniverse.from_records(records, meta)  # six possible pairs
    gold = Pair.create("0", "1")
    candidates = [gold, Pair.create("2", "3")]
    result = blocking_metrics(candidates, [gold], universe, records)
    assert result["candidate_count"].value == 2
    assert result["sampled_labeled_positive_candidate_recall"].value == 1
    assert "sampled" in result["sampled_labeled_positive_candidate_recall"].reason
    assert result["reduction_ratio"].value == pytest.approx(2 / 3)
    assert result["candidate_degree_mean"].value == 1
    with pytest.raises(ValueError, match="duplicate candidate"):
        blocking_metrics([gold, gold], [gold], universe, records)
    clean_meta = DatasetMetadata("link", "1", TaskType.CLEAN_CLEAN, ("A", "B"))
    clean_universe = LegalPairUniverse.from_records(records, clean_meta)
    with pytest.raises(ValueError, match="outside the legal pair universe"):
        blocking_metrics([Pair.create("0", "1")], [], clean_universe, records)


def test_hand_computed_cluster_metrics_split_merge_and_singletons() -> None:
    ids = frozenset("abcd")
    gold = {"a": "A", "b": "A", "c": "B", "d": "B"}
    perfect = cluster_metrics(gold, gold, expected_record_ids=ids, entity_gold_complete_verified=True)
    assert perfect["pairwise_precision"].value == perfect["pairwise_recall"].value == 1
    assert perfect["b3_precision"].value == perfect["b3_recall"].value == perfect["purity_micro"].value == 1
    assert perfect["over_merge_rate"].value == perfect["under_merge_rate"].value == 0

    split = {"a": "A1", "b": "A2", "c": "B", "d": "B"}
    split_report = cluster_metrics(split, gold, expected_record_ids=ids, entity_gold_complete_verified=True)
    assert split_report["pairwise_precision"].value == 1
    assert split_report["pairwise_recall"].value == .5
    assert split_report["pairwise_f1"].value == pytest.approx(2 / 3)
    assert split_report["b3_precision"].value == 1
    assert split_report["b3_recall"].value == .75
    assert split_report["under_merge_rate"].value == .5

    merged = {record: "one" for record in ids}
    merge_report = cluster_metrics(merged, gold, expected_record_ids=ids, entity_gold_complete_verified=True)
    assert merge_report["pairwise_precision"].value == pytest.approx(1 / 3)
    assert merge_report["pairwise_recall"].value == 1
    assert merge_report["pairwise_f1"].value == .5
    assert merge_report["b3_precision"].value == .5
    assert merge_report["b3_recall"].value == 1
    assert merge_report["purity_micro"].value == .5
    assert merge_report["over_merge_rate"].value == 1

    singletons = {record: record for record in ids}
    singleton_report = cluster_metrics(singletons, gold, expected_record_ids=ids, entity_gold_complete_verified=True)
    assert singleton_report["pairwise_precision"].status is MetricStatus.NOT_APPLICABLE
    assert singleton_report["pairwise_recall"].value == 0
    assert singleton_report["singleton_count"].value == 4
    assert singleton_report["under_merge_rate"].value == 1


def test_cluster_gold_gate_validation_coverage_and_membership_contract() -> None:
    ids = frozenset("abcd")
    predicted = {"a": "x", "b": "x", "c": "y", "d": "y"}
    unavailable = cluster_metrics(predicted, {"a": "a", "b": "a", "c": "b"},
                                  expected_record_ids=ids, entity_gold_complete_verified=False)
    assert unavailable["pairwise_f1"].status is MetricStatus.BLOCKED
    validation = ClusterValidationEvidence(frozenset({"x"}), frozenset(), frozenset({"x", "y"}))
    partial = cluster_metrics(predicted, None, expected_record_ids=ids,
                              entity_gold_complete_verified=False, validation=validation)
    assert partial["validation_coverage"].value == .5
    assert partial["unvalidated_cluster_count"].value == 1
    assert partial["unsafe_cluster_rate"].status is MetricStatus.BLOCKED
    with pytest.raises(ValueError, match="coverage mismatch"):
        cluster_metrics({"a": "x"}, None, expected_record_ids=ids, entity_gold_complete_verified=False)


def test_empty_cluster_universe_returns_explicit_na_metrics() -> None:
    report = cluster_metrics({}, {}, expected_record_ids=frozenset(), entity_gold_complete_verified=True)
    assert report["b3_f1"].value is None
    assert report["b3_f1"].status is MetricStatus.NOT_APPLICABLE
    assert report["record_count"].value == 0


def test_baselines_are_deterministic_and_weighted_score_is_not_probability() -> None:
    left = Record("left", "A", {"name": "Nguyen Van An", "email": "an@example.com",
                                  "phone": "0901-234-567", "address": "Hanoi"})
    right = Record("right", "B", {"name": "nguyen van an", "email": "an@example.com",
                                    "phone": "0901234567", "address": "hanoi"})
    exact = ExactRuleBaseline()
    assert exact.score(left, right) == exact.score(left, right)
    assert exact.route(*exact.score(left, right)) is Decision.AUTO_MATCH
    weighted = WeightedV1Reimplementation()
    score, evidence = weighted.score(left, right)
    assert score.value == 1
    assert "Weighted V1 reimplementation v1.0" in evidence
    assert weighted.route(score, evidence) is Decision.AUTO_MATCH
    assert not isinstance(score, Probability)

    missing = Record("missing", "C", {"name": None, "email": None, "phone": None, "address": None})
    partial = Record("partial", "D", {"name": "same name", "email": None, "phone": None, "address": None})
    partial_score, _ = weighted.score(partial, partial)
    missing_score, missing_evidence = weighted.score(missing, missing)
    assert partial_score.value == 1  # remaining name weight is renormalized
    assert missing_score.value == 0
    assert weighted.route(missing_score, missing_evidence) is Decision.REVIEW


def test_runtime_measurements_reject_double_counting() -> None:
    tracker = ResourceTracker()
    assert tracker.measure("load", lambda: 3, item_count=1) == 3
    with pytest.raises(ValueError, match="already measured"):
        tracker.measure("load", lambda: None)
    snapshot = tracker.snapshot()
    assert snapshot["stages"]["load"]["item_count"] == 1


def test_g2_cli_runs_real_pair_track_and_blocks_unavailable_track() -> None:
    import os
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
           "TMP": str(root / ".cache"), "TEMP": str(root / ".cache"), "TMPDIR": str(root / ".cache")}
    run = subprocess.run([sys.executable, str(root / "run.py"), "run", "--dataset", "masterone_test",
                          "--systems", "exact,weighted", "--track", "pairs"], cwd=root,
                         env=env, capture_output=True, text=True, timeout=20)
    assert run.returncode == 0, run.stderr
    assert "Descriptive internal test run only" in run.stdout
    assert "Brier=N/A (Brier score requires Probability scores explicitly declared calibrated)" in run.stdout
    assert "exact |" in run.stdout and "weighted |" in run.stdout
    assert "Loading masterone_test" in run.stderr
    root_entry = subprocess.run([sys.executable, str(root / "run.py"), "datasets", "list"],
                                cwd=root.parent, env=env, capture_output=True, text=True, timeout=20)
    assert root_entry.returncode == 0, root_entry.stderr
    assert "masterone_test" in root_entry.stdout
    blocked = subprocess.run([sys.executable, str(root / "run.py"), "run", "--dataset", "masterone_test",
                              "--systems", "exact", "--track", "end-to-end"], cwd=root,
                            env=env, capture_output=True, text=True, timeout=20)
    assert blocked.returncode == 3
    assert "no result was produced" in blocked.stderr
