"""Counting-based cluster metrics; never builds a transitive pair closure."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from math import comb
from typing import Mapping

from standalone.evaluation.metrics import MetricValue


@dataclass(frozen=True)
class ClusterValidationEvidence:
    checked_cluster_ids: frozenset[str]
    verified_contradiction_cluster_ids: frozenset[str]
    total_cluster_ids: frozenset[str]

    def __post_init__(self) -> None:
        if not self.checked_cluster_ids <= self.total_cluster_ids:
            raise ValueError("checked cluster IDs must be a subset of total predicted cluster IDs")
        if not self.verified_contradiction_cluster_ids <= self.checked_cluster_ids:
            raise ValueError("verified contradictions must be among checked cluster IDs")


def cluster_metrics(
    predicted: Mapping[str, str],
    gold: Mapping[str, str] | None,
    *,
    expected_record_ids: frozenset[str],
    entity_gold_complete_verified: bool,
    validation: ClusterValidationEvidence | None = None,
) -> dict[str, MetricValue]:
    """Compute pairwise, B-cubed and purity scores by contingency counts.

    Exact membership coverage is required, and entity metrics are blocked unless
    complete independently verified gold has been declared.
    """
    if set(predicted) != set(expected_record_ids):
        missing, extra = expected_record_ids - set(predicted), set(predicted) - expected_record_ids
        raise ValueError(f"cluster membership coverage mismatch: missing={len(missing)}, extra={len(extra)}")
    if any(not cluster for cluster in predicted.values()):
        raise ValueError("predicted cluster IDs must be non-empty")
    pred_sizes = Counter(predicted.values())
    total_clusters = frozenset(pred_sizes)
    multi_pred = {cluster for cluster, size in pred_sizes.items() if size > 1}
    out: dict[str, MetricValue] = {}
    if gold is None or set(gold) != set(expected_record_ids) or not entity_gold_complete_verified:
        reason = "complete independently verified entity gold is unavailable"
        for name in ("pairwise_precision", "pairwise_recall", "pairwise_f1", "b3_precision", "b3_recall",
                     "b3_f1", "purity_micro", "over_merge_rate", "under_merge_rate"):
            out[name] = MetricValue.na(reason, blocked=True)
    else:
        if any(not isinstance(entity, str) or not entity for entity in gold.values()):
            raise ValueError("verified gold entity IDs must be non-empty strings")
        gold_sizes = Counter(gold.values())
        intersection = Counter((predicted[record], gold[record]) for record in expected_record_ids)
        tp = sum(comb(size, 2) for size in intersection.values())
        pred_pairs = sum(comb(size, 2) for size in pred_sizes.values())
        gold_pairs = sum(comb(size, 2) for size in gold_sizes.values())
        fp, fn = pred_pairs - tp, gold_pairs - tp
        f1_den = 2 * tp + fp + fn
        out.update({
            "pairwise_precision": MetricValue(tp / pred_pairs) if pred_pairs else MetricValue.na("no predicted co-cluster pairs"),
            "pairwise_recall": MetricValue(tp / gold_pairs) if gold_pairs else MetricValue.na("no gold co-cluster pairs"),
            "pairwise_f1": MetricValue(2 * tp / f1_den) if f1_den else MetricValue.na("no gold or predicted co-cluster pairs"),
        })
        n = len(expected_record_ids)
        if n:
            # One pass over records keeps memory proportional to records/entities.
            b3p_total = b3r_total = 0.0
            per_record_intersection = Counter((predicted[record], gold[record]) for record in expected_record_ids)
            for record in expected_record_ids:
                overlap = per_record_intersection[(predicted[record], gold[record])]
                b3p_total += overlap / pred_sizes[predicted[record]]
                b3r_total += overlap / gold_sizes[gold[record]]
            b3p, b3r = b3p_total / n, b3r_total / n
            b3f = 2 * b3p * b3r / (b3p + b3r) if b3p + b3r else None
            max_overlap_by_cluster: Counter[str] = Counter()
            for (cluster, _entity), size in intersection.items():
                max_overlap_by_cluster[cluster] = max(max_overlap_by_cluster[cluster], size)
            purity = sum(max_overlap_by_cluster.values()) / n
            gold_entities_by_pred: dict[str, set[str]] = {}
            pred_clusters_by_gold: dict[str, set[str]] = {}
            for record in expected_record_ids:
                gold_entities_by_pred.setdefault(predicted[record], set()).add(gold[record])
                pred_clusters_by_gold.setdefault(gold[record], set()).add(predicted[record])
            over_den = len(multi_pred)
            multi_gold = {entity for entity, size in gold_sizes.items() if size > 1}
            over = sum(len(gold_entities_by_pred[cluster]) > 1 for cluster in multi_pred)
            under = sum(len(pred_clusters_by_gold[entity]) > 1 for entity in multi_gold)
            out.update({
                "b3_precision": MetricValue(b3p), "b3_recall": MetricValue(b3r),
                "b3_f1": MetricValue(b3f) if b3f is not None else MetricValue.na("B-cubed precision and recall sum to zero"),
                "purity_micro": MetricValue(purity),
                "over_merge_rate": MetricValue(over / over_den) if over_den else MetricValue.na("no predicted multi-record clusters"),
                "under_merge_rate": MetricValue(under / len(multi_gold)) if multi_gold else MetricValue.na("no gold multi-record entities"),
            })
        else:
            for name in ("pairwise_precision", "pairwise_recall", "pairwise_f1", "b3_precision", "b3_recall",
                         "b3_f1", "purity_micro", "over_merge_rate", "under_merge_rate"):
                out[name] = MetricValue.na("empty record universe")
    if validation is None:
        out["validation_coverage"] = MetricValue.na("no cluster validation evidence supplied", blocked=True)
        out["unvalidated_cluster_count"] = MetricValue.na("no cluster validation evidence supplied", blocked=True)
        out["unsafe_cluster_rate"] = MetricValue.na("no verified contradiction validation evidence", blocked=True)
    else:
        if validation.total_cluster_ids != total_clusters:
            raise ValueError("validation total cluster IDs do not match predicted cluster IDs")
        checked_multi = validation.checked_cluster_ids & multi_pred
        validation_coverage = len(checked_multi) / len(multi_pred) if multi_pred else None
        out["validation_coverage"] = (MetricValue(validation_coverage) if validation_coverage is not None
                                      else MetricValue.na("no predicted multi-record clusters"))
        out["unvalidated_cluster_count"] = MetricValue(len(multi_pred - checked_multi))
        if not multi_pred:
            out["unsafe_cluster_rate"] = MetricValue.na("no predicted multi-record clusters")
        elif checked_multi != multi_pred:
            out["unsafe_cluster_rate"] = MetricValue.na("not every multi-record cluster has been validated", blocked=True)
        else:
            unsafe = validation.verified_contradiction_cluster_ids & multi_pred
            out["unsafe_cluster_rate"] = MetricValue(len(unsafe) / len(multi_pred))
    out["record_count"] = MetricValue(len(expected_record_ids))
    out["predicted_cluster_count"] = MetricValue(len(total_clusters))
    out["singleton_count"] = MetricValue(sum(size == 1 for size in pred_sizes.values()))
    return out
