"""Smoothed Fellegi–Sunter scoring with explicit fit scope and evidence trace."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, Sequence

from standalone.contracts.core import DataSplit, LogOdds, RecordFeatures
from standalone.contracts.interfaces import FieldComparison
from standalone.contracts.labels import PairLabel


class FitScopeError(ValueError):
    pass


@dataclass(frozen=True)
class ComparisonLevel:
    field: str
    level: str
    left: str | None
    right: str | None
    missing: bool = False
    conflict: bool = False
    quality_flags: tuple[str, ...] = ()
    reference_version: str | None = None


@dataclass(frozen=True)
class LevelParameters:
    m: float
    u: float

    def __post_init__(self) -> None:
        if not (0.0 < self.m <= 1.0 and 0.0 < self.u <= 1.0):
            raise ValueError("m and u probabilities must be in (0, 1]")


@dataclass(frozen=True)
class EvidenceEntry:
    field: str
    normalized_left: str | None
    normalized_right: str | None
    level: str
    m: float
    u: float
    contribution: float
    missing: bool
    conflict: bool
    reference_version: str | None
    quality_flags: tuple[str, ...]


@dataclass(frozen=True)
class FSResult:
    score: LogOdds
    raw_similarity: float
    trace: tuple[EvidenceEntry, ...]


def _levenshtein_ratio(a: str, b: str) -> float:
    if a == b: return 1.0
    if not a or not b: return 0.0
    previous = list(range(len(b)+1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(current[-1]+1, previous[j]+1, previous[j-1]+(ca != cb)))
        previous = current
    return 1.0 - previous[-1] / max(len(a), len(b))


def compare(left: RecordFeatures, right: RecordFeatures,
            quality_left: Sequence[str] = (), quality_right: Sequence[str] = (),
            reference_version: str | None = None,
            address_reference: str | None = None) -> tuple[ComparisonLevel, ...]:
    output = []
    for field in ("name", "phone", "email", "address"):
        a, b = left.fields.get(field), right.fields.get(field)
        quality = tuple(sorted(set(flag for flag in (*quality_left, *quality_right)
                                   if flag.startswith(field + "_") or flag in {"phone_invalid", "email_invalid"})))
        if field == "address" and address_reference in {
                "compatible", "different_reference_units", "ambiguous"}:
            ref_level = {"compatible": "reference_compatible",
                         "different_reference_units": "reference_different",
                         "ambiguous": "reference_ambiguous"}[address_reference]
            output.append(ComparisonLevel(field, ref_level, a, b, False, False,
                                          quality, reference_version)); continue
        if not a or not b:
            output.append(ComparisonLevel(field, "missing", a, b, True, False, quality, reference_version)); continue
        if a.casefold() == b.casefold(): level = "exact"
        elif field == "name" and _levenshtein_ratio(a.casefold(), b.casefold()) >= .9: level = "fuzzy_high"
        elif field == "name" and _levenshtein_ratio(a.casefold(), b.casefold()) >= .7: level = "fuzzy_medium"
        elif field == "address" and _levenshtein_ratio(a.casefold(), b.casefold()) >= .85: level = "fuzzy_high"
        else: level = "disagreement"
        conflict = field in {"phone", "email"} and level == "disagreement"
        output.append(ComparisonLevel(field, level, a, b, False, conflict, quality, reference_version))
    return tuple(output)


def _sigmoid(value: float) -> float:
    if value >= 0:
        z = math.exp(-min(value, 745.0)); return 1.0/(1.0+z)
    z = math.exp(max(value, -745.0)); return z/(1.0+z)


class FellegiSunterScorer:
    def __init__(self, parameters: Mapping[tuple[str, str], LevelParameters],
                 prior_match_probability: float, *, probability_floor: float = 1e-12,
                 term_frequency: bool = False, frequencies: Mapping[str, Mapping[str, int]] | None = None,
                 hub_threshold: int | None = None, quality_aware: bool = True) -> None:
        if not 0 < prior_match_probability < 1:
            raise ValueError("prior_match_probability must be in (0,1)")
        if not 0 < probability_floor < .5:
            raise ValueError("probability_floor must be in (0,.5)")
        self.parameters = dict(parameters); self.prior = prior_match_probability
        self.floor = probability_floor; self.term_frequency = term_frequency
        self.frequencies = {f: dict(v) for f, v in (frequencies or {}).items()}
        self.hub_threshold = hub_threshold; self.quality_aware = quality_aware

    @classmethod
    def fit(cls, examples: Sequence[tuple[RecordFeatures, RecordFeatures, PairLabel]],
            split: DataSplit, *, smoothing: float = 1.0,
            levels: Sequence[str] = ("exact", "fuzzy_high", "fuzzy_medium", "disagreement", "missing"),
            **kwargs) -> "FellegiSunterScorer":
        if split is not DataSplit.TRAIN:
            raise FitScopeError("Fellegi–Sunter fitting requires TRAIN labels")
        if smoothing <= 0 or not examples:
            raise ValueError("nonempty training examples and positive smoothing are required")
        counts = {klass: {field: {level: 0 for level in levels} for field in ("name", "phone", "email", "address")}
                  for klass in (PairLabel.POSITIVE, PairLabel.NEGATIVE)}
        class_counts = {PairLabel.POSITIVE: 0, PairLabel.NEGATIVE: 0}
        for a, b, label in examples:
            if label is PairLabel.UNKNOWN:
                continue
            class_counts[label] += 1
            for item in compare(a, b):
                if item.level in counts[label][item.field]: counts[label][item.field][item.level] += 1
        if not all(class_counts.values()):
            raise ValueError("training data must contain both positive and negative labeled pairs")
        parameters = {}
        for field in counts[PairLabel.POSITIVE]:
            denominator_m = sum(counts[PairLabel.POSITIVE][field].values()) + smoothing * len(levels)
            denominator_u = sum(counts[PairLabel.NEGATIVE][field].values()) + smoothing * len(levels)
            for level in levels:
                m = (counts[PairLabel.POSITIVE][field][level] + smoothing) / denominator_m
                u = (counts[PairLabel.NEGATIVE][field][level] + smoothing) / denominator_u
                parameters[(field, level)] = LevelParameters(m, u)
        return cls(parameters, class_counts[PairLabel.POSITIVE]/sum(class_counts.values()), **kwargs)

    def score(self, left: RecordFeatures, right: RecordFeatures, *,
              quality_left: Sequence[str] = (), quality_right: Sequence[str] = (),
              reference_version: str | None = None,
              address_reference: str | None = None) -> FSResult:
        comparisons = compare(left, right, quality_left, quality_right, reference_version,
                              address_reference)
        prior_odds = math.log(self.prior/(1-self.prior)); contributions = []
        trace = []
        for item in comparisons:
            params = self.parameters.get((item.field, item.level))
            if params is None and item.level.startswith("reference_"):
                continue  # Reference evidence is inert until its own m/u rates are fitted.
            params = params or self.parameters.get((item.field, "disagreement"))
            if params is None: continue
            m, u = max(self.floor, params.m), max(self.floor, params.u)
            contribution = math.log(m)-math.log(u)
            flags = item.quality_flags
            if self.quality_aware and flags and ("invalid" in " ".join(flags)):
                contribution = 0.0
            if self.hub_threshold and self.frequencies and not item.missing:
                freq = self.frequencies.get(item.field, {})
                count = max(freq.get(item.left or "", 0), freq.get(item.right or "", 0))
                if count >= self.hub_threshold:
                    contribution = 0.0
            if self.term_frequency and item.level == "exact" and not item.missing:
                count = max(1, self.frequencies.get(item.field, {}).get(item.left or "", 1))
                contribution -= math.log(count)
            contributions.append(contribution)
            trace.append(EvidenceEntry(item.field, item.left, item.right, item.level, m, u,
                                       contribution, item.missing, item.conflict,
                                       item.reference_version, flags))
        log_odds = prior_odds + math.fsum(contributions)
        if not math.isfinite(log_odds): log_odds = math.copysign(1e6, log_odds)
        similarities = [1.0 if item.level == "exact" else
                        _levenshtein_ratio(item.left, item.right) if item.left and item.right else 0.0
                        for item in comparisons]
        similarity = sum(similarities)/len(similarities) if similarities else 0.0
        return FSResult(LogOdds(log_odds), similarity, tuple(trace))


class FellegiSunterFieldScorer:
    """PairScorer protocol adapter for already-computed, gold-blind levels."""
    def __init__(self, parameters: Mapping[tuple[str, str], LevelParameters],
                 prior_match_probability: float) -> None:
        self.parameters = dict(parameters)
        if not 0 < prior_match_probability < 1:
            raise ValueError("prior_match_probability must be in (0,1)")
        self.prior = prior_match_probability

    def score(self, comparison: FieldComparison) -> LogOdds:
        parts = [math.log(self.prior/(1-self.prior))]
        for field, level in comparison.levels.items():
            params = self.parameters.get((field, level))
            if params is None:
                continue
            parts.append(math.log(params.m)-math.log(params.u))
        return LogOdds(math.fsum(parts))
