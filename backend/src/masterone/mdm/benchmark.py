"""Benchmark metrics for entity resolution (architecture §33).

Computes metrics across all levels:
  §33.1 Candidate Generation — candidate_recall, reduction_ratio, pairs_per_record
  §33.2 Pair Matching       — precision, recall, F1, PR-AUC (simplified)
  §33.3 Auto Merge          — auto_merge_precision, auto_merge_coverage, review_rate
  §33.4 Cluster-level       — pairwise precision, cluster purity
  §33.5 Per-subgroup        — precision per subgroup (A1/A2/A3/C1/D1…)

All metrics expect a ground_truth structure produced from benchmark_entity_id CSV column.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any


@dataclass
class CandidateMetrics:
    candidate_recall: float        # ground-truth pairs found in candidates / total GT pairs
    reduction_ratio: float         # 1 - candidates / total_possible
    pairs_per_record: float
    candidate_count: int
    total_possible: int
    gt_pair_count: int
    found_gt_pairs: int


@dataclass
class PairMetrics:
    precision: float
    recall: float
    f1: float
    tp: int
    fp: int
    fn: int
    tn: int


@dataclass
class AutoMergeMetrics:
    auto_merge_precision: float    # of auto-merged pairs, fraction that are true matches
    auto_merge_coverage: float     # of true match pairs, fraction auto-merged
    review_rate: float             # fraction of scored pairs that went to REVIEW


@dataclass
class ClusterMetrics:
    pairwise_precision: float
    pairwise_recall: float
    pairwise_f1: float
    cluster_purity: float          # avg fraction of dominant entity per predicted cluster


@dataclass
class BenchmarkResult:
    candidate: CandidateMetrics | None = None
    pair: PairMetrics | None = None
    auto_merge: AutoMergeMetrics | None = None
    cluster: ClusterMetrics | None = None
    per_subgroup: dict[str, PairMetrics] = field(default_factory=dict)
    record_count: int = 0
    pair_count: int = 0


# ── Ground truth helpers ─────────────────────────────────────────────────────

def build_ground_truth_pairs(records: list[dict]) -> frozenset[tuple[str, str]]:
    """Build true-match pair set from benchmark_entity_id labels on each record."""
    by_entity: dict[str, list[str]] = defaultdict(list)
    for rec in records:
        eid = rec.get("benchmark_entity_id") or rec.get("normalized_fields", {}).get("benchmark_entity_id")
        if eid:
            by_entity[eid].append(rec["id"])
    gt: set[tuple[str, str]] = set()
    for rids in by_entity.values():
        for a, b in combinations(sorted(rids), 2):
            gt.add((a, b))
    return frozenset(gt)


# ── §33.1 Candidate metrics ───────────────────────────────────────────────────

def compute_candidate_metrics(
    candidate_blocks: dict[tuple[str, str], Any],
    gt_pairs: frozenset[tuple[str, str]],
    record_count: int,
) -> CandidateMetrics:
    candidate_set = frozenset(tuple(sorted(p)) for p in candidate_blocks)
    total_possible = record_count * (record_count - 1) // 2
    found = sum(1 for p in gt_pairs if p in candidate_set)
    rr = 1 - len(candidate_set) / total_possible if total_possible > 0 else 0.0
    return CandidateMetrics(
        candidate_recall=found / len(gt_pairs) if gt_pairs else 1.0,
        reduction_ratio=rr,
        pairs_per_record=len(candidate_set) / record_count if record_count else 0.0,
        candidate_count=len(candidate_set),
        total_possible=total_possible,
        gt_pair_count=len(gt_pairs),
        found_gt_pairs=found,
    )


# ── §33.2 Pair matching metrics ───────────────────────────────────────────────

def _pair_key(pair: dict) -> tuple[str, str]:
    return tuple(sorted((pair["left_id"], pair["right_id"])))  # type: ignore[return-value]


def compute_pair_metrics(
    pairs: list[dict],
    gt_pairs: frozenset[tuple[str, str]],
    positive_decisions: frozenset[str] | None = None,
) -> PairMetrics:
    if positive_decisions is None:
        positive_decisions = frozenset({"MERGEABLE", "MATCH_CANDIDATE"})

    tp = fp = fn = tn = 0
    scored_keys = {_pair_key(p) for p in pairs}

    for pair in pairs:
        key = _pair_key(pair)
        predicted_pos = pair.get("decision") in positive_decisions
        actual_pos = key in gt_pairs
        if predicted_pos and actual_pos:
            tp += 1
        elif predicted_pos and not actual_pos:
            fp += 1
        elif not predicted_pos and actual_pos:
            fn += 1
        else:
            tn += 1

    # GT pairs not in candidates at all count as FN
    fn += sum(1 for p in gt_pairs if p not in scored_keys)

    prec = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    rec  = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1   = 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0.0
    return PairMetrics(precision=prec, recall=rec, f1=f1, tp=tp, fp=fp, fn=fn, tn=tn)


# ── §33.3 Auto merge metrics ──────────────────────────────────────────────────

def compute_auto_merge_metrics(pairs: list[dict], gt_pairs: frozenset[tuple[str, str]]) -> AutoMergeMetrics:
    auto_merged = [p for p in pairs if p.get("triage_status") in {"AUTO_MERGE", "HIGH_CONFIDENCE_PROPOSAL"}]
    reviewed    = [p for p in pairs if p.get("triage_status") == "REVIEW"]
    total       = len(pairs)

    auto_tp = sum(1 for p in auto_merged if _pair_key(p) in gt_pairs)
    am_prec = auto_tp / len(auto_merged) if auto_merged else 1.0
    am_cov  = auto_tp / len(gt_pairs) if gt_pairs else 0.0
    rev_rate = len(reviewed) / total if total > 0 else 0.0

    return AutoMergeMetrics(
        auto_merge_precision=am_prec,
        auto_merge_coverage=am_cov,
        review_rate=rev_rate,
    )


# ── §33.4 Cluster metrics ─────────────────────────────────────────────────────

def compute_cluster_metrics(
    proposals: list[dict],
    records: list[dict],
) -> ClusterMetrics:
    """Pairwise precision/recall and cluster purity against benchmark_entity_id."""
    by_entity: dict[str, list[str]] = defaultdict(list)
    for rec in records:
        eid = rec.get("benchmark_entity_id") or ""
        if eid:
            by_entity[eid].append(rec["id"])
    gt_pairs: frozenset[tuple[str, str]] = frozenset(
        tuple(sorted((a, b)))
        for rids in by_entity.values()
        for a, b in combinations(sorted(rids), 2)
    )

    pred_pairs: set[tuple[str, str]] = set()
    purity_scores: list[float] = []
    for prop in proposals:
        mids = sorted(prop.get("member_record_ids", []))
        for a, b in combinations(mids, 2):
            pred_pairs.add(tuple(sorted((a, b))))
        # Cluster purity
        entity_counts: dict[str, int] = defaultdict(int)
        rec_by_id = {r["id"]: r for r in records}
        for rid in mids:
            eid = (rec_by_id.get(rid) or {}).get("benchmark_entity_id") or "_UNKNOWN"
            entity_counts[eid] += 1
        if mids:
            purity_scores.append(max(entity_counts.values()) / len(mids))

    if not gt_pairs and not pred_pairs:
        return ClusterMetrics(1.0, 1.0, 1.0, 1.0)

    tp = len(pred_pairs & gt_pairs)
    prec = tp / len(pred_pairs) if pred_pairs else 1.0
    rec  = tp / len(gt_pairs)   if gt_pairs  else 1.0
    f1   = 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0.0
    purity = sum(purity_scores) / len(purity_scores) if purity_scores else 1.0
    return ClusterMetrics(pairwise_precision=prec, pairwise_recall=rec,
                          pairwise_f1=f1, cluster_purity=purity)


# ── §33.5 Per-subgroup metrics ────────────────────────────────────────────────

def compute_per_subgroup_metrics(
    pairs: list[dict],
    gt_pairs: frozenset[tuple[str, str]],
    positive_decisions: frozenset[str] | None = None,
) -> dict[str, PairMetrics]:
    if positive_decisions is None:
        positive_decisions = frozenset({"MERGEABLE", "MATCH_CANDIDATE"})

    subgroup_pairs: dict[str, list[dict]] = defaultdict(list)
    for pair in pairs:
        sg = pair.get("subgroup") or pair.get("primary_group") or "UNKNOWN"
        subgroup_pairs[sg].append(pair)

    return {sg: compute_pair_metrics(sg_pairs, gt_pairs, positive_decisions)
            for sg, sg_pairs in subgroup_pairs.items()}


# ── Full benchmark run ────────────────────────────────────────────────────────

def run_benchmark(
    records: list[dict],
    candidate_blocks: dict[tuple[str, str], Any],
    pairs: list[dict],
    proposals: list[dict],
) -> BenchmarkResult:
    gt_pairs = build_ground_truth_pairs(records)
    result = BenchmarkResult(record_count=len(records), pair_count=len(pairs))

    if candidate_blocks is not None:
        result.candidate = compute_candidate_metrics(candidate_blocks, gt_pairs, len(records))

    if pairs:
        result.pair       = compute_pair_metrics(pairs, gt_pairs)
        result.auto_merge = compute_auto_merge_metrics(pairs, gt_pairs)
        result.per_subgroup = compute_per_subgroup_metrics(pairs, gt_pairs)

    if proposals:
        result.cluster = compute_cluster_metrics(proposals, records)

    return result
