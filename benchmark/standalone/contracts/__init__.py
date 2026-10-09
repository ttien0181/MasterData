"""Standalone record, pair, score, gold and evaluation contracts."""

from standalone.contracts.core import (
    DataSplit, DatasetBundle, DatasetMetadata, Decision, GoldAnnotations, GoldCoverage,
    GoldCoverageKind, LabeledPair, LogLikelihoodRatio, Pair, PairPrediction, RecordFeatures,
    Probability, Record, Similarity, TaskType, canonical_pair_id,
    global_record_id,
)
from standalone.contracts.labels import PairLabel, parse_pair_label

__all__ = [
    "DataSplit", "DatasetBundle", "DatasetMetadata", "Decision", "GoldAnnotations", "GoldCoverage",
    "GoldCoverageKind", "LabeledPair", "LogLikelihoodRatio", "Pair", "PairPrediction",
    "PairLabel", "Probability", "Record", "RecordFeatures", "Similarity", "TaskType", "canonical_pair_id",
    "global_record_id", "parse_pair_label",
]
