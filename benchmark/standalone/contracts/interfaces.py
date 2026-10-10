"""Protocols for stage boundaries. No protocol exposes gold to prediction stages."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from types import MappingProxyType
from typing import Iterable, Mapping, Protocol, Sequence

from standalone.contracts.core import (
    FEATURE_ALLOWLIST, DataSplit, DatasetBundle, DatasetMetadata, Decision, GoldCoverage,
    LabeledPair, Pair, PairPrediction, Record, RecordFeatures, Score, TaskType,
)


class DatasetAdapter(Protocol):
    def load(self) -> DatasetBundle: ...


@dataclass(frozen=True)
class MappingCandidate:
    unit_id: str
    unit_name: str
    unit_level: str | None = None
    admin_version: str | None = None


@dataclass(frozen=True)
class MappingResolution:
    status: str
    candidates: tuple[MappingCandidate, ...]
    evidence: tuple[str, ...] = ()


class ReferenceProvider(Protocol):
    @property
    def version(self) -> str: ...
    def resolve_column_alias(self, alias: str, version: str | None = None) -> tuple[str, ...]: ...
    def resolve_alias(self, alias: str, admin_version: str | None = None) -> tuple[MappingCandidate, ...]: ...
    def map_old_to_new(self, source_unit_id: str, source_admin_version: str | None = None,
                       as_of: date | None = None) -> MappingResolution: ...


@dataclass(frozen=True)
class Profile:
    fit_scope: str
    record_count: int
    field_statistics: Mapping[str, Mapping[str, int | float]]


class Profiler(Protocol):
    def fit(self, records: Sequence[RecordFeatures], fit_scope: DataSplit) -> Profile: ...


class Normalizer(Protocol):
    def transform(self, record: RecordFeatures, source: str) -> "NormalizationResult": ...


@dataclass(frozen=True)
class NormalizationResult:
    features: RecordFeatures
    field_provenance: Mapping[str, str]
    quality_flags: tuple[str, ...]


@dataclass(frozen=True)
class CandidateRecord:
    """Non-identifying candidate context; contains no dataset/raw record ID."""
    source: str
    features: RecordFeatures

    def __post_init__(self) -> None:
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("candidate source must be a non-empty string")
        if not isinstance(self.features, RecordFeatures):
            raise ValueError("candidate features must be RecordFeatures")


class CandidateGenerator(Protocol):
    def generate(self, records: Sequence[CandidateRecord], task: TaskType) -> Iterable[IndexPair]: ...


@dataclass(frozen=True)
class FieldComparison:
    levels: Mapping[str, str]
    missing_fields: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        unknown = (set(self.levels) | set(self.missing_fields) | set(self.conflicts)) - FEATURE_ALLOWLIST
        if unknown:
            raise ValueError(f"comparison references non-feature fields: {sorted(unknown)}")
        if any(not isinstance(level, str) for level in self.levels.values()):
            raise ValueError("comparison levels must be strings")
        object.__setattr__(self, "levels", MappingProxyType(dict(self.levels)))
        object.__setattr__(self, "missing_fields", tuple(self.missing_fields))
        object.__setattr__(self, "conflicts", tuple(self.conflicts))


@dataclass(frozen=True)
class IndexPair:
    """Ephemeral positions, not record IDs, returned by a candidate generator."""
    left_index: int
    right_index: int
    blocking_reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.left_index == self.right_index or min(self.left_index, self.right_index) < 0:
            raise ValueError("candidate positions must be distinct non-negative indexes")
        if self.left_index > self.right_index:
            a, b = self.left_index, self.right_index
            object.__setattr__(self, "left_index", b)
            object.__setattr__(self, "right_index", a)


@dataclass(frozen=True)
class PredictionInputs:
    """Inputs visible to candidate/comparison/scoring stages; no IDs or gold."""
    records: tuple[CandidateRecord, ...]
    pairs: tuple[IndexPair, ...]


@dataclass(frozen=True)
class PredictionTrace:
    """Orchestrator-only join keys, separate from prediction-stage inputs."""
    record_ids: tuple[str, ...]
    pair_ids: tuple[str, ...]


def build_prediction_inputs(bundle: DatasetBundle) -> tuple[PredictionInputs, PredictionTrace]:
    """Strip all annotations and labels before a prediction stage receives data."""
    positions = {record.record_id: index for index, record in enumerate(bundle.records)}
    if len(positions) != len(bundle.records):
        raise ValueError("dataset contains duplicate record IDs")
    records = tuple(CandidateRecord(record.source, record.feature_view()) for record in bundle.records)
    pairs = tuple(IndexPair(positions[labeled.pair.left_record_id],
                            positions[labeled.pair.right_record_id], ()) for labeled in bundle.pairs)
    trace = PredictionTrace(tuple(record.record_id for record in bundle.records),
                            tuple(labeled.pair.pair_id for labeled in bundle.pairs))
    return PredictionInputs(records, pairs), trace


class FieldComparator(Protocol):
    def compare(self, left: RecordFeatures, right: RecordFeatures) -> FieldComparison: ...


class PairScorer(Protocol):
    def score(self, comparison: FieldComparison) -> Score: ...


class PairRouter(Protocol):
    def route(self, score: Score) -> Decision: ...


@dataclass(frozen=True)
class ConstraintSnapshot:
    revision: str
    must_link: frozenset[tuple[str, str]]
    cannot_link: frozenset[tuple[str, str]]


class ConstraintStore(Protocol):
    def snapshot(self) -> ConstraintSnapshot: ...


@dataclass(frozen=True)
class ClusterResult:
    memberships: Mapping[str, str]
    checked_constraints: tuple[str, ...]
    validation_coverage: float
    status: str


class Clusterer(Protocol):
    def cluster(self, records: Sequence[Record], predictions: Sequence[PairPrediction],
                constraints: ConstraintSnapshot) -> ClusterResult: ...


@dataclass(frozen=True)
class SystemPrediction:
    pair_predictions: tuple[PairPrediction, ...]
    clusters: ClusterResult | None = None
    stage_timings: Mapping[str, float] | None = None


class BenchmarkSystem(Protocol):
    def predict(self, records: Sequence[Record], pairs: Sequence[Pair]) -> SystemPrediction: ...


@dataclass(frozen=True)
class EvaluationResult:
    status: str
    metrics: Mapping[str, float | None]
    reason: str | None = None


class Evaluator(Protocol):
    def evaluate_pairs(self, predictions: Sequence[PairPrediction], gold: Sequence[LabeledPair],
                       coverage: GoldCoverage) -> EvaluationResult: ...
    def evaluate_clusters(self, predicted: ClusterResult, gold_entities: Mapping[str, str],
                          metadata: DatasetMetadata) -> EvaluationResult: ...
