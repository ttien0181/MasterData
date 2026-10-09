"""Coverage-aware evaluation gates; unknown and unlisted pairs are never negatives."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from standalone.contracts.core import DataSplit, GoldCoverage, GoldCoverageKind, LabeledPair
from standalone.contracts.labels import PairLabel


class EvaluationStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    SKIPPED = "SKIPPED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class CoverageDecision:
    status: EvaluationStatus
    reason: str


def validate_fit_split(split: DataSplit) -> None:
    if split is not DataSplit.TRAIN:
        raise ValueError(f"fit/training may use train only, not {split!s}")


def validate_tuning_split(split: DataSplit) -> None:
    if split is not DataSplit.DEV:
        raise ValueError(f"threshold/config tuning may use dev only, not {split!s}")


def pair_universe_coverage(coverage: GoldCoverage, labeled_pair_count: int,
                           legal_pair_count: int, *,
                           labeled_pairs: Iterable[LabeledPair] | None = None,
                           legal_pair_ids: Iterable[str] | None = None) -> CoverageDecision:
    if labeled_pair_count < 0 or legal_pair_count < 0 or labeled_pair_count > legal_pair_count:
        return CoverageDecision(EvaluationStatus.FAIL, "invalid labeled/legal pair counts")
    if coverage.pair_labels is not GoldCoverageKind.COMPLETE_PAIR_LABELS:
        return CoverageDecision(EvaluationStatus.BLOCKED,
                                 "gold labels do not cover the complete legal pair universe; unlisted pairs remain unknown")
    if labeled_pair_count != legal_pair_count:
        return CoverageDecision(EvaluationStatus.BLOCKED,
                                 "declared complete pair labels do not match the legal universe size")
    if labeled_pairs is None or legal_pair_ids is None:
        return CoverageDecision(EvaluationStatus.BLOCKED,
                                "equal counts do not prove complete coverage; exact labeled and legal pair keys are required")
    labeled = tuple(labeled_pairs)
    legal = tuple(legal_pair_ids)
    if len(labeled) != labeled_pair_count or len(legal) != legal_pair_count:
        return CoverageDecision(EvaluationStatus.BLOCKED, "pair key counts differ from declared counts")
    labeled_ids = {row.pair.pair_id for row in labeled}
    legal_ids = set(legal)
    if any(row.label is PairLabel.UNKNOWN for row in labeled):
        return CoverageDecision(EvaluationStatus.BLOCKED, "complete pair gold contains unknown labels")
    if len(labeled_ids) != len(labeled) or len(legal_ids) != len(legal):
        return CoverageDecision(EvaluationStatus.BLOCKED, "duplicate pair keys do not prove complete coverage")
    if labeled_ids != legal_ids:
        return CoverageDecision(EvaluationStatus.BLOCKED, "labeled pair keys do not equal legal pair keys")
    return CoverageDecision(EvaluationStatus.PASS, "all legal pairs have labels")


def cluster_gold_coverage(coverage: GoldCoverage) -> CoverageDecision:
    if (coverage.entity_labels is GoldCoverageKind.COMPLETE_ENTITY_LABELS
            and coverage.entity_labels_verified):
        return CoverageDecision(EvaluationStatus.PASS, "complete entity gold is verified")
    return CoverageDecision(EvaluationStatus.BLOCKED,
                            "cluster metrics require independently verified complete entity labels")
