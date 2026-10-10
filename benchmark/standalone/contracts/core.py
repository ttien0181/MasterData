"""Typed standalone contracts with a hard boundary between features and gold."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Mapping
from urllib.parse import quote

from standalone.contracts.labels import PairLabel

FEATURE_ALLOWLIST = frozenset({"name", "phone", "email", "address"})
RECORD_ANNOTATION_DENYLIST = frozenset({
    "entity_id", "is_match", "noise_type", "difficulty", "case_type",
    "generator_label", "split", "address_id", "entity_a", "entity_b",
    "hard_negative_type", "temporal_case",
})


class TaskType(str, Enum):
    DEDUPE = "dedupe"
    CLEAN_CLEAN = "clean_clean"
    MULTI_SOURCE = "multi_source"


class DataSplit(str, Enum):
    TRAIN = "train"
    DEV = "dev"
    TEST = "test"


class GoldCoverageKind(str, Enum):
    SAMPLED_LABELED_PAIRS = "sampled_labeled_pairs"
    COMPLETE_PAIR_LABELS = "complete_pair_labels"
    COMPLETE_ENTITY_LABELS = "complete_entity_labels"


@dataclass(frozen=True)
class Record:
    record_id: str
    source: str
    fields: Mapping[str, str | None]

    def __post_init__(self) -> None:
        if not self.record_id.strip() or not self.source.strip():
            raise ValueError("record_id and source must be non-empty")
        unknown = set(self.fields) - FEATURE_ALLOWLIST
        if unknown:
            raise ValueError(f"non-feature fields are forbidden in Record: {', '.join(sorted(unknown))}")
        if any(value is not None and not isinstance(value, str) for value in self.fields.values()):
            raise ValueError("Record feature values must be strings or None")
        object.__setattr__(self, "fields", MappingProxyType({
            key: (None if value is None or not value.strip() else value)
            for key, value in self.fields.items()
        }))

    def feature_view(self) -> "RecordFeatures":
        """Only allowlisted values; IDs/source/annotations cannot enter similarity."""
        return RecordFeatures(self.fields)


@dataclass(frozen=True)
class RecordFeatures:
    fields: Mapping[str, str | None]

    def __post_init__(self) -> None:
        unknown = set(self.fields) - FEATURE_ALLOWLIST
        if unknown:
            raise ValueError(f"non-feature fields are forbidden in RecordFeatures: {', '.join(sorted(unknown))}")
        if any(value is not None and not isinstance(value, str) for value in self.fields.values()):
            raise ValueError("RecordFeatures values must be strings or None")
        object.__setattr__(self, "fields", MappingProxyType(dict(self.fields)))


def global_record_id(dataset: str, source: str, raw_id: str) -> str:
    if any(not part.strip() for part in (dataset, source, raw_id)):
        raise ValueError("dataset, source, and raw ID must all be non-empty")
    # Percent-encode each component so embedded delimiters cannot alias another ID.
    return "::".join(quote(part, safe="") for part in (dataset, source, raw_id))


def canonical_pair_id(record_id_a: str, record_id_b: str) -> str:
    if not record_id_a or not record_id_b or record_id_a == record_id_b:
        raise ValueError("self-pairs are not allowed")
    first, second = sorted((record_id_a, record_id_b))
    # JSON array framing is unambiguous even if an endpoint contains NUL.
    digest = hashlib.sha256(json.dumps([first, second], ensure_ascii=False,
                                       separators=(",", ":")).encode("utf-8")).hexdigest()
    return f"pair:{digest}"


@dataclass(frozen=True)
class Pair:
    pair_id: str
    left_record_id: str
    right_record_id: str
    source_orientation: tuple[str, str] | None = None
    input_order: tuple[str, str] | None = None

    @classmethod
    def create(cls, record_id_a: str, record_id_b: str,
               source_a: str | None = None, source_b: str | None = None) -> "Pair":
        if record_id_a == record_id_b:
            raise ValueError("self-pairs are not allowed")
        left, right = sorted((record_id_a, record_id_b))
        orientation = (source_a, source_b) if source_a is not None and source_b is not None else None
        return cls(canonical_pair_id(left, right), left, right, orientation, (record_id_a, record_id_b))

    def __post_init__(self) -> None:
        if self.left_record_id >= self.right_record_id:
            raise ValueError("Pair endpoints must be canonical (left < right)")
        if self.pair_id != canonical_pair_id(self.left_record_id, self.right_record_id):
            raise ValueError("pair_id does not match canonical endpoints")


@dataclass(frozen=True)
class LabeledPair:
    pair: Pair
    label: PairLabel
    split: str
    annotations: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.label, PairLabel):
            raise TypeError("LabeledPair.label must be a PairLabel")
        if not self.split.strip():
            raise ValueError("LabeledPair.split must be non-empty")
        object.__setattr__(self, "annotations", MappingProxyType(dict(self.annotations)))


@dataclass(frozen=True)
class GoldCoverage:
    pair_labels: GoldCoverageKind | None = None
    entity_labels: GoldCoverageKind | None = None
    entity_labels_verified: bool = False
    provenance: str = "unspecified"

    def __post_init__(self) -> None:
        if self.pair_labels is not None and not isinstance(self.pair_labels, GoldCoverageKind):
            raise ValueError("pair label coverage must be a GoldCoverageKind")
        if self.entity_labels is not None and not isinstance(self.entity_labels, GoldCoverageKind):
            raise ValueError("entity label coverage must be a GoldCoverageKind")
        if self.pair_labels not in (None, GoldCoverageKind.SAMPLED_LABELED_PAIRS,
                                    GoldCoverageKind.COMPLETE_PAIR_LABELS):
            raise ValueError("pair label coverage must be sampled_labeled_pairs or complete_pair_labels")
        if self.entity_labels not in (None, GoldCoverageKind.COMPLETE_ENTITY_LABELS):
            raise ValueError("entity label coverage must be complete_entity_labels")
        if self.entity_labels_verified and self.entity_labels is not GoldCoverageKind.COMPLETE_ENTITY_LABELS:
            raise ValueError("verified entity labels must declare complete_entity_labels coverage")


@dataclass(frozen=True)
class DatasetMetadata:
    dataset_id: str
    version: str
    task: TaskType
    sources: tuple[str, ...]
    allowed_source_pairs: frozenset[tuple[str, str]] = frozenset()
    schema_version: str = "1"
    provenance: str = "unspecified"
    split_names: tuple[str, ...] = ()
    source_uniqueness_verified: bool = False

    def __post_init__(self) -> None:
        if not self.dataset_id.strip() or not self.version.strip():
            raise ValueError("dataset_id and version must be non-empty")
        if not isinstance(self.task, TaskType):
            raise ValueError("dataset task must be a TaskType")
        if len(self.sources) != len(set(self.sources)) or any(
                not isinstance(source, str) or not source for source in self.sources):
            raise ValueError("metadata sources must be unique non-empty names")
        normalized_pairs: set[tuple[str, str]] = set()
        for pair in self.allowed_source_pairs:
            if not isinstance(pair, tuple) or len(pair) != 2 or any(
                    not isinstance(source, str) or not source for source in pair):
                raise ValueError("allowed_source_pairs entries must contain two source names")
            normalized_pairs.add(tuple(sorted(pair)))
        normalized = frozenset(normalized_pairs)
        unknown = set().union(*map(set, normalized)) - set(self.sources) if normalized else set()
        if unknown:
            raise ValueError(f"allowed source pair references unknown source(s): {sorted(unknown)}")
        object.__setattr__(self, "allowed_source_pairs", normalized)


@dataclass(frozen=True)
class GoldAnnotations:
    entity_by_record_id: Mapping[str, str]
    record_annotations: Mapping[str, Mapping[str, str]]

    def __post_init__(self) -> None:
        object.__setattr__(self, "entity_by_record_id", MappingProxyType(dict(self.entity_by_record_id)))
        object.__setattr__(self, "record_annotations", MappingProxyType({
            key: MappingProxyType(dict(value)) for key, value in self.record_annotations.items()
        }))


@dataclass(frozen=True)
class DatasetBundle:
    records: tuple[Record, ...]
    pairs: tuple[LabeledPair, ...]
    metadata: DatasetMetadata
    gold_coverage: GoldCoverage
    gold: GoldAnnotations

    def __post_init__(self) -> None:
        allowed = set(self.metadata.sources)
        if any(record.source not in allowed for record in self.records):
            raise ValueError("record source is absent from dataset metadata")
        record_ids = {record.record_id for record in self.records}
        if len(record_ids) != len(self.records):
            raise ValueError("dataset contains duplicate record IDs")
        if len({labeled.pair.pair_id for labeled in self.pairs}) != len(self.pairs):
            raise ValueError("dataset contains duplicate pair IDs")
        if any(key not in record_ids
               for pair in self.pairs for key in (pair.pair.left_record_id, pair.pair.right_record_id)):
            raise ValueError("pair references a record outside the dataset")


@dataclass(frozen=True)
class Similarity:
    value: float
    kind: str = field(default="similarity", init=False)

    def __post_init__(self) -> None:
        if not math.isfinite(self.value):
            raise ValueError("similarity must be finite")


@dataclass(frozen=True)
class LogLikelihoodRatio:
    value: float
    kind: str = field(default="log_likelihood_ratio", init=False)

    def __post_init__(self) -> None:
        if not math.isfinite(self.value):
            raise ValueError("log likelihood ratio must be finite")


@dataclass(frozen=True)
class LogOdds:
    value: float
    kind: str = field(default="log_odds", init=False)

    def __post_init__(self) -> None:
        if not math.isfinite(self.value):
            raise ValueError("log odds must be finite")


@dataclass(frozen=True)
class Probability:
    value: float
    kind: str = field(default="probability", init=False)

    def __post_init__(self) -> None:
        if not 0.0 <= self.value <= 1.0:
            raise ValueError("probability must be in [0, 1]")


Score = Similarity | LogLikelihoodRatio | LogOdds | Probability


class Decision(str, Enum):
    AUTO_MATCH = "AUTO_MATCH"
    REVIEW = "REVIEW"
    NO_MATCH = "NO_MATCH"


@dataclass(frozen=True)
class PairPrediction:
    pair_id: str
    score: Score
    decision: Decision
    evidence: tuple[str, ...] = ()
    stage_seconds: float | None = None

    def __post_init__(self) -> None:
        if not self.pair_id:
            raise ValueError("prediction pair_id must be non-empty")
        if not isinstance(self.score, (Similarity, LogLikelihoodRatio, LogOdds, Probability)):
            raise ValueError("prediction score must have an explicit score type")
        if not isinstance(self.decision, Decision):
            raise ValueError("prediction decision must be a Decision")
        if self.stage_seconds is not None and (not math.isfinite(self.stage_seconds) or self.stage_seconds < 0):
            raise ValueError("stage_seconds must be finite and non-negative")


@dataclass(frozen=True)
class GoldPairPredictions:
    """Evaluation-only labels, separated from scorer-facing PairPrediction."""
    predictions: tuple[PairPrediction, ...]
    gold: tuple[LabeledPair, ...]
