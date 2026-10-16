"""M0 metric adapter; matching algorithms and application decisions are untouched.

Reuse the existing coverage-aware metric implementation. Callers must freeze
their routing-to-MATCH adapter before reading labels. Missing candidates remain
in the labeled universe and UNKNOWN never becomes negative gold.
"""
from dataclasses import asdict

from standalone.contracts.labels import PairLabel
from standalone.evaluation.metrics import pair_metrics


def ratio(numerator, denominator, reason):
    return {"value": numerator / denominator if denominator else None,
            "reason": None if denominator else reason}


def evaluate_pairs(labels, decisions, candidate_ids):
    known = {key: value for key, value in labels.items() if value is not PairLabel.UNKNOWN}
    available = set(decisions) & set(candidate_ids)
    matched = {key for key in available if decisions[key] == "MATCH"}
    binary = {key: asdict(value) for key, value in pair_metrics(
        labels, predicted_positive=matched).items()
        if key in {"tp", "fp", "fn", "tn", "precision", "recall", "f1",
                   "labeled_pair_count", "unknown_pair_count"}}
    reviewed = {key for key in available if decisions[key] == "REVIEW"}
    abstained = {key for key in available if decisions[key] in {"REVIEW", "INSUFFICIENT_EVIDENCE"}}
    decided = {key for key in available if decisions[key] in {"MATCH", "NON_MATCH", "BLOCKED"}}
    positive = {key for key, value in known.items() if value is PairLabel.POSITIVE}
    selective = {key: asdict(value) for key, value in pair_metrics(
        {key: value for key, value in known.items() if key in decided},
        predicted_positive=matched).items()
        if key in {"tp", "fp", "fn", "tn", "precision", "recall", "f1"}}
    return {
        "binary_end_to_end": binary,
        "candidate_conditioned": {key: asdict(value) for key, value in pair_metrics(
            {key: value for key, value in known.items() if key in candidate_ids},
            predicted_positive=matched).items()
            if key in {"tp", "fp", "fn", "tn", "precision", "recall", "f1"}},
        "selective": selective,
        "coverage": {
            "known_labeled_pairs": len(known),
            "known_labeled_candidates": len(set(known) & set(candidate_ids)),
            "review_rate_on_known_labels": ratio(len(reviewed & set(known)), len(known), "no known labels"),
            "abstain_rate_on_known_labels": ratio(len(abstained & set(known)), len(known), "no known labels"),
            "decision_coverage_on_known_labels": ratio(len(decided & set(known)), len(known), "no known labels"),
            "sampled_positive_candidate_recall": ratio(len(positive & set(candidate_ids)), len(positive), "no known positives"),
            "positive_blocking_misses": len(positive - set(candidate_ids)),
            "unknown_predictions_excluded": len((set(labels) - set(known)) & available),
            "unlabeled_predictions": len(available - set(labels)),
        },
    }
