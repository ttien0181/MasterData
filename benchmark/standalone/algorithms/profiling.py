"""Label-blind profiling with explicit fitting scope."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from enum import Enum
from math import comb
from types import MappingProxyType
from typing import Mapping, Sequence

from standalone.algorithms.normalization import NormalizedRecord
from standalone.contracts.core import DataSplit, RecordFeatures


class FitScope(str, Enum):
    TRAIN = "train"
    TRANSDUCTIVE_UNLABELED = "transductive_unlabeled"
    DEV = "dev"
    TEST = "test"


@dataclass(frozen=True)
class ProfileField:
    record_count: int
    missing_count: int
    cardinality: int
    value_frequencies: Mapping[str, int]
    shared_value_count: int
    hub_values: tuple[str, ...]
    exact_block_pair_count: int
    block_size_p50: float | None
    block_size_p95: float | None
    maximum_block_size: int


@dataclass(frozen=True)
class DatasetProfile:
    fit_scope: FitScope
    record_count: int
    source_distribution: Mapping[str, int]
    fields: Mapping[str, ProfileField]

    def __post_init__(self) -> None:
        if self.fit_scope in {FitScope.DEV, FitScope.TEST}:
            raise ValueError("profiling fit scope must be train or transductive_unlabeled")
        object.__setattr__(self, "source_distribution", MappingProxyType(dict(self.source_distribution)))
        object.__setattr__(self, "fields", MappingProxyType(dict(self.fields)))

    @property
    def field_statistics(self) -> Mapping[str, Mapping[str, int | float]]:
        return MappingProxyType({name: MappingProxyType({
            "record_count": stats.record_count,
            "missing_count": stats.missing_count,
            "cardinality": stats.cardinality,
            "shared_value_count": stats.shared_value_count,
            "exact_block_pair_count": stats.exact_block_pair_count,
            "block_size_p50": stats.block_size_p50 or 0.0,
            "block_size_p95": stats.block_size_p95 or 0.0,
            "maximum_block_size": stats.maximum_block_size,
        }) for name, stats in self.fields.items()})


def _quantile(values: list[int], q: float) -> float | None:
    if not values:
        return None
    values.sort()
    pos = (len(values) - 1) * q
    low, high = int(pos), min(int(pos) + 1, len(values) - 1)
    return values[low] + (values[high] - values[low]) * (pos - low)


class LabelBlindProfiler:
    def __init__(self, *, hub_min_count: int = 10) -> None:
        if hub_min_count < 2:
            raise ValueError("hub_min_count must be at least 2")
        self.hub_min_count = hub_min_count

    def fit(self, records: Sequence[NormalizedRecord | RecordFeatures],
            fit_scope: FitScope | DataSplit,
            sources: Sequence[str] | None = None) -> DatasetProfile:
        if isinstance(fit_scope, DataSplit):
            fit_scope = FitScope(fit_scope.value)
        if fit_scope not in {FitScope.TRAIN, FitScope.TRANSDUCTIVE_UNLABELED}:
            raise ValueError("profile fit scope must be train or transductive_unlabeled; dev/test forbidden")
        if sources is not None and len(sources) != len(records):
            raise ValueError("sources and records lengths differ")
        source_distribution = Counter(
            sources[index] if sources is not None else record.source if isinstance(record, NormalizedRecord)
            else "unspecified"
            for index, record in enumerate(records)
        )
        def value(record: NormalizedRecord | RecordFeatures, field: str) -> str | None:
            item = record.fields.get(field)
            if isinstance(record, NormalizedRecord):
                return item.normalized if item else None
            return item
        field_stats: dict[str, ProfileField] = {}
        for field in ("name", "phone", "email", "address"):
            counts = Counter(item for record in records if (item := value(record, field)))
            block_sizes = list(counts.values())
            field_stats[field] = ProfileField(
                record_count=len(records),
                missing_count=len(records) - sum(counts.values()),
                cardinality=len(counts),
                value_frequencies=MappingProxyType(dict(sorted(counts.items()))),
                shared_value_count=sum(count > 1 for count in counts.values()),
                hub_values=tuple(sorted(value for value, count in counts.items() if count >= self.hub_min_count)),
                exact_block_pair_count=sum(comb(count, 2) for count in counts.values() if count > 1),
                block_size_p50=_quantile(block_sizes[:], .50),
                block_size_p95=_quantile(block_sizes[:], .95),
                maximum_block_size=max(block_sizes, default=0),
            )
        return DatasetProfile(fit_scope, len(records), source_distribution, field_stats)
