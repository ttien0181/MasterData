"""Three-way decision routing with thresholds carrying their DEV provenance."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Sequence

from standalone.contracts.core import DataSplit, Decision, Score
from standalone.contracts.labels import PairLabel


class RoutingBlockedError(ValueError):
    pass


@dataclass(frozen=True)
class RoutingThresholds:
    auto_match: float
    no_match: float
    source: str

    def __post_init__(self) -> None:
        if not self.source or self.source != "dev":
            raise ValueError("threshold source must be dev")
        if not math.isfinite(self.auto_match) or not math.isfinite(self.no_match):
            raise ValueError("routing thresholds must be finite")
        if self.no_match > self.auto_match:
            raise ValueError("no-match threshold must not exceed auto-match threshold")


def choose_thresholds_dev(scores: Sequence[float], labels: Sequence[PairLabel], split: DataSplit,
                          *, target_auto_precision: float = .99) -> RoutingThresholds:
    if split is not DataSplit.DEV:
        raise RoutingBlockedError("threshold selection requires DEV")
    if len(scores) != len(labels) or not scores:
        raise RoutingBlockedError("nonempty aligned dev scores/labels required")
    rows = [(float(s), y) for s, y in zip(scores, labels) if y is not PairLabel.UNKNOWN]
    if not rows or not any(y is PairLabel.POSITIVE for _, y in rows):
        raise RoutingBlockedError("dev requires labeled positive examples to select auto-match threshold")
    candidates = sorted({s for s, _ in rows})
    valid = []
    for threshold in candidates:
        selected = [y for score, y in rows if score >= threshold]
        precision = sum(y is PairLabel.POSITIVE for y in selected)/len(selected) if selected else 0.0
        if selected and precision >= target_auto_precision:
            valid.append(threshold)
    if not valid:
        raise RoutingBlockedError("dev has no auto-match threshold meeting target precision")
    high = min(valid)  # lowest score meeting the target preserves the most auto matches.
    negative_cutoffs = sorted({s for s, _ in rows if s <= high})
    valid_low = []
    for threshold in negative_cutoffs:
        selected = [y for score, y in rows if score <= threshold]
        if selected and all(y is PairLabel.NEGATIVE for y in selected):
            valid_low.append(threshold)
    if not valid_low:
        raise RoutingBlockedError("dev has no no-match threshold with negative-only coverage")
    low = max(valid_low)
    return RoutingThresholds(high, min(low, high), "dev")


def route(score: float, thresholds: RoutingThresholds, *, hard_conflict: bool = False,
          sufficient_evidence: bool = True) -> Decision:
    if hard_conflict or not sufficient_evidence:
        return Decision.REVIEW
    if score >= thresholds.auto_match:
        return Decision.AUTO_MATCH
    if score <= thresholds.no_match:
        return Decision.NO_MATCH
    return Decision.REVIEW


@dataclass(frozen=True)
class ThresholdPairRouter:
    thresholds: RoutingThresholds

    def route(self, score: Score, *, hard_conflict: bool = False,
              sufficient_evidence: bool = True) -> Decision:
        return route(score.value, self.thresholds, hard_conflict=hard_conflict,
                     sufficient_evidence=sufficient_evidence)
