"""Coverage-aware pair and blocking metrics for benchmark evaluation.

All labeled-pair metrics use only known positive/negative labels in L. Unlabeled
legal pairs are unknown and are never inferred to be true negatives.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import isfinite
from typing import Mapping, Sequence

from standalone.contracts.core import Decision, GoldCoverage, GoldCoverageKind, Pair, PairPrediction, Probability
from standalone.contracts.labels import PairLabel
from standalone.datasets.universe import LegalPairUniverse
from standalone.contracts.core import Record


class MetricStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class MetricValue:
    value: float | int | None
    status: MetricStatus = MetricStatus.AVAILABLE
    reason: str | None = None

    @classmethod
    def na(cls, reason: str, *, blocked: bool = False) -> "MetricValue":
        return cls(None, MetricStatus.BLOCKED if blocked else MetricStatus.NOT_APPLICABLE, reason)


def _ratio(numerator: int | float, denominator: int | float, reason: str) -> MetricValue:
    if denominator == 0:
        return MetricValue.na(reason)
    return MetricValue(numerator / denominator)


def _check_known_labels(labels: Mapping[str, PairLabel]) -> dict[str, PairLabel]:
    known: dict[str, PairLabel] = {}
    for pair_id, label in labels.items():
        if not pair_id:
            raise ValueError("gold pair IDs must be non-empty")
        if not isinstance(label, PairLabel):
            raise TypeError("pair labels must use PairLabel")
        if label is not PairLabel.UNKNOWN:
            known[pair_id] = label
    return known


def pair_metrics(
    labels: Mapping[str, PairLabel],
    predictions: Mapping[str, PairPrediction] | None = None,
    *,
    predicted_positive: frozenset[str] | set[str] | None = None,
    coverage: GoldCoverage | None = None,
    calibrated_probabilities: bool = False,
    target_precision: float | None = None,
    coverage_threshold: float | None = None,
) -> dict[str, MetricValue]:
    """Evaluate predictions on known labeled IDs only.

    A missing prediction is an implicit negative for binary match metrics; a
    missing decision is not counted as an automatic or reviewed decision.
    Scores are ranked only when every known labeled pair has a finite score.
    """
    known = _check_known_labels(labels)
    predictions = predictions or {}
    if predicted_positive is None:
        predicted = {key for key, row in predictions.items()
                     if key in known and row.decision is Decision.AUTO_MATCH}
    else:
        # Predictions outside known L are unlabeled: retain their count for
        # diagnostics, but never use them as negative or positive gold examples.
        predicted = set(predicted_positive) & set(known)
    positives = {key for key, label in known.items() if label is PairLabel.POSITIVE}
    negatives = {key for key, label in known.items() if label is PairLabel.NEGATIVE}
    tp = len(predicted & positives)
    fp = len(predicted & negatives)
    fn = len(positives - predicted)
    tn = len(negatives - predicted)
    f1_den = 2 * tp + fp + fn
    known_n = len(known)
    out: dict[str, MetricValue] = {
        "tp": MetricValue(tp), "fp": MetricValue(fp), "fn": MetricValue(fn), "tn": MetricValue(tn),
        "precision": _ratio(tp, tp + fp, "no predicted positive pairs in labeled set"),
        "recall": _ratio(tp, tp + fn, "no positive gold pairs in labeled set"),
        "f1": MetricValue(2 * tp / f1_den) if f1_den else MetricValue.na("no positive gold or predicted pairs"),
        "false_merge_share": _ratio(fp, tp + fp, "no predicted positive pairs in labeled set"),
        "false_positive_rate": _ratio(fp, fp + tn, "no negative gold pairs in labeled set"),
        "false_non_match_rate": _ratio(fn, tp + fn, "no positive gold pairs in labeled set"),
        "labeled_pair_count": MetricValue(known_n),
        "unknown_pair_count": MetricValue(sum(label is PairLabel.UNKNOWN for label in labels.values())),
        "unlabeled_prediction_count": MetricValue(len(set(predictions) - set(known))),
    }

    known_predictions = {key: row for key, row in predictions.items() if key in known}
    auto = {key for key, row in known_predictions.items() if row.decision is Decision.AUTO_MATCH}
    reviewed = {key for key, row in known_predictions.items() if row.decision is Decision.REVIEW}
    auto_tp = len(auto & positives)
    out.update({
        "review_rate": _ratio(len(reviewed), known_n, "no known labeled pairs"),
        "auto_decision_rate": _ratio(sum(row.decision in {Decision.AUTO_MATCH, Decision.NO_MATCH}
                                          for row in known_predictions.values()), known_n,
                                      "no known labeled pairs"),
        "auto_match_precision": _ratio(auto_tp, len(auto), "no AUTO_MATCH decisions"),
        "auto_match_true_recall": _ratio(auto_tp, len(positives), "no positive gold pairs in labeled set"),
    })

    scored = {key: row.score.value for key, row in known_predictions.items()}
    if len(scored) != known_n or any(not isfinite(value) for value in scored.values()):
        ap = MetricValue.na("scores do not cover every known labeled pair")
        brier = MetricValue.na("probability scores do not cover every known labeled pair")
    else:
        # Tie-grouped average precision: each distinct score threshold adds the
        # precision at that threshold weighted by newly recovered positives.
        score_groups: dict[float, list[str]] = {}
        for key, value in scored.items():
            score_groups.setdefault(value, []).append(key)
        if not positives:
            ap = MetricValue.na("no positive gold pairs in labeled set")
        else:
            seen_tp = seen_fp = 0
            ap_sum = 0.0
            for score in sorted(score_groups, reverse=True):
                group = score_groups[score]
                group_tp = sum(key in positives for key in group)
                group_fp = len(group) - group_tp
                seen_tp += group_tp
                seen_fp += group_fp
                ap_sum += (group_tp / len(positives)) * (seen_tp / (seen_tp + seen_fp))
            ap = MetricValue(ap_sum)
        if (calibrated_probabilities
                and all(isinstance(row.score, Probability) for row in known_predictions.values())):
            brier = MetricValue(sum((scored[key] - (1.0 if key in positives else 0.0)) ** 2
                                    for key in known) / known_n) if known_n else MetricValue.na("no known labels")
        else:
            brier = MetricValue.na("Brier score requires Probability scores explicitly declared calibrated")
    out["average_precision"] = ap
    out["brier_score"] = brier

    if target_precision is None or coverage_threshold is None:
        out["coverage_at_target_precision"] = MetricValue.na(
            "requires a target precision and a score threshold frozen on an independent dev set", blocked=True)
    elif not 0 < target_precision <= 1 or not isfinite(coverage_threshold):
        raise ValueError("target_precision must be in (0,1] and coverage_threshold must be finite")
    elif len(scored) != known_n:
        out["coverage_at_target_precision"] = MetricValue.na("scores do not cover every known labeled pair")
    else:
        selected = {key for key, score in scored.items() if score >= coverage_threshold}
        selected_precision = len(selected & positives) / len(selected) if selected else None
        if selected_precision is None:
            out["coverage_at_target_precision"] = MetricValue.na("frozen threshold selects no pairs")
        elif selected_precision < target_precision:
            out["coverage_at_target_precision"] = MetricValue.na(
                f"observed labeled-set precision {selected_precision:.6g} is below requested target {target_precision:.6g}")
        else:
            out["coverage_at_target_precision"] = MetricValue(len(selected) / known_n)
    if coverage is not None and coverage.pair_labels is GoldCoverageKind.SAMPLED_LABELED_PAIRS:
        out["label_scope"] = MetricValue.na("sampled labeled-pair scope; not a full-universe estimate")
    return out


def blocking_metrics(
    candidates: Sequence[Pair],
    positive_gold: Sequence[Pair],
    universe: LegalPairUniverse,
    records: Sequence[Record],
    *,
    positive_gold_coverage: GoldCoverageKind = GoldCoverageKind.SAMPLED_LABELED_PAIRS,
) -> dict[str, MetricValue]:
    """Evaluate a unique, legal candidate set and report labeled-gold recall."""
    by_id = {record.record_id: record for record in records}
    if len(by_id) != len(records):
        raise ValueError("blocking metric records contain duplicate IDs")
    record_ids = set(by_id)
    candidate_ids: set[str] = set()
    for pair in candidates:
        if pair.pair_id in candidate_ids:
            raise ValueError(f"duplicate candidate pair: {pair.pair_id}")
        candidate_ids.add(pair.pair_id)
        if pair.left_record_id not in record_ids or pair.right_record_id not in record_ids:
            raise ValueError("candidate pair references a record outside the dataset")
        if not universe.allows(by_id[pair.left_record_id].source, by_id[pair.right_record_id].source):
            raise ValueError("candidate pair is outside the legal pair universe")
    gold_ids: set[str] = set()
    for pair in positive_gold:
        if pair.pair_id in gold_ids:
            raise ValueError(f"duplicate positive gold pair: {pair.pair_id}")
        if pair.left_record_id not in record_ids or pair.right_record_id not in record_ids:
            raise ValueError("positive gold pair references a record outside the dataset")
        if not universe.allows(by_id[pair.left_record_id].source, by_id[pair.right_record_id].source):
            raise ValueError("positive gold pair is outside the legal pair universe")
        gold_ids.add(pair.pair_id)
    if len(candidate_ids) > universe.pair_count:
        raise ValueError("candidate count exceeds legal pair universe")
    if positive_gold_coverage not in {GoldCoverageKind.SAMPLED_LABELED_PAIRS,
                                     GoldCoverageKind.COMPLETE_PAIR_LABELS}:
        raise ValueError("positive gold must be declared sampled or complete pair labels")
    degrees = {record_id: 0 for record_id in by_id}
    for pair in candidates:
        degrees[pair.left_record_id] += 1
        degrees[pair.right_record_id] += 1
    degree_values = sorted(degrees.values())

    def quantile(q: float) -> float:
        if not degree_values:
            return 0.0
        pos = (len(degree_values) - 1) * q
        lo, hi = int(pos), min(int(pos) + 1, len(degree_values) - 1)
        return degree_values[lo] + (degree_values[hi] - degree_values[lo]) * (pos - lo)

    candidate_fraction = len(candidate_ids) / universe.pair_count if universe.pair_count else None
    reason = "empty legal pair universe" if not universe.pair_count else None
    reduction = MetricValue.na(reason) if reason else MetricValue(1.0 - candidate_fraction)
    recall = _ratio(len(candidate_ids & gold_ids), len(gold_ids), "no positive gold pairs in supplied labeled set")
    if positive_gold_coverage is GoldCoverageKind.SAMPLED_LABELED_PAIRS and recall.status is MetricStatus.AVAILABLE:
        recall = MetricValue(recall.value, reason="sampled labeled-positive recall; not complete-gold blocking recall")
    recall_name = ("sampled_labeled_positive_candidate_recall"
                   if positive_gold_coverage is GoldCoverageKind.SAMPLED_LABELED_PAIRS
                   else "blocking_recall")
    return {
        recall_name: recall,
        "reduction_ratio": reduction,
        "candidate_count": MetricValue(len(candidate_ids)),
        "candidate_degree_mean": MetricValue(sum(degree_values) / len(degree_values)) if degree_values else MetricValue.na("no records"),
        "candidate_degree_p50": MetricValue(quantile(.50)) if degree_values else MetricValue.na("no records"),
        "candidate_degree_p95": MetricValue(quantile(.95)) if degree_values else MetricValue.na("no records"),
        "candidate_degree_max": MetricValue(max(degree_values)) if degree_values else MetricValue.na("no records"),
        "candidate_fraction": MetricValue(candidate_fraction) if candidate_fraction is not None else MetricValue.na("empty legal pair universe"),
    }
