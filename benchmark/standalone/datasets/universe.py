"""Task-aware legal pair universes; no within-source uniqueness is assumed."""
from __future__ import annotations

from dataclasses import dataclass
from collections import Counter
from types import MappingProxyType
from itertools import combinations
from math import comb
from typing import Iterable, Mapping, Sequence

from standalone.contracts.core import DatasetMetadata, Record, TaskType


def _normalized_source_pairs(pairs: Iterable[tuple[str, str]]) -> frozenset[tuple[str, str]]:
    result = set()
    for pair in pairs:
        if not isinstance(pair, tuple) or len(pair) != 2 or any(
                not isinstance(source, str) or not source for source in pair):
            raise ValueError("allowed source pairs must contain two non-empty source names")
        a, b = pair
        result.add(tuple(sorted((a, b))))
    return frozenset(result)


@dataclass(frozen=True)
class LegalPairUniverse:
    task: TaskType
    source_counts: Mapping[str, int]
    allowed_source_pairs: frozenset[tuple[str, str]] = frozenset()

    def __post_init__(self) -> None:
        if not isinstance(self.task, TaskType):
            raise ValueError("legal universe task must be a TaskType")
        if any(not isinstance(source, str) or not source for source in self.source_counts):
            raise ValueError("source names must be non-empty strings")
        if any(not isinstance(count, int) or isinstance(count, bool) or count < 0
               for count in self.source_counts.values()):
            raise ValueError("source counts must be non-negative integers")
        object.__setattr__(self, "source_counts", MappingProxyType(dict(self.source_counts)))
        object.__setattr__(self, "allowed_source_pairs", _normalized_source_pairs(self.allowed_source_pairs))
        if self.task is TaskType.CLEAN_CLEAN and len(self.source_counts) != 2:
            raise ValueError("clean_clean universe requires exactly two sources")
        if self.task is TaskType.MULTI_SOURCE:
            unknown = set().union(*map(set, self.allowed_source_pairs)) - set(self.source_counts) if self.allowed_source_pairs else set()
            if unknown:
                raise ValueError(f"allowed source pair references unknown source(s): {sorted(unknown)}")
        elif self.allowed_source_pairs:
            raise ValueError("allowed_source_pairs is only used for multi_source tasks")

    @classmethod
    def from_records(cls, records: Sequence[Record], metadata: DatasetMetadata) -> "LegalPairUniverse":
        counts = {source: 0 for source in metadata.sources}
        for record in records:
            if record.source not in counts:
                raise ValueError(f"record source {record.source!r} is absent from dataset metadata")
            counts[record.source] += 1
        return cls(metadata.task, counts, metadata.allowed_source_pairs)

    @property
    def pair_count(self) -> int:
        if self.task is TaskType.DEDUPE:
            n = sum(self.source_counts.values())
            return comb(n, 2)
        if self.task is TaskType.CLEAN_CLEAN:
            a, b = self.source_counts.values()
            return a * b
        return sum(self.source_counts[a] * self.source_counts[b]
                   if a != b else comb(self.source_counts[a], 2)
                   for a, b in self.allowed_source_pairs)

    def allows(self, source_a: str, source_b: str) -> bool:
        if source_a not in self.source_counts or source_b not in self.source_counts:
            return False
        if self.task is TaskType.DEDUPE:
            return True
        if self.task is TaskType.CLEAN_CLEAN:
            if source_a == source_b:
                return False
            return True
        return tuple(sorted((source_a, source_b))) in self.allowed_source_pairs

    def iter_pairs(self, records: Sequence[Record]):
        """Yield legal pairs lazily; callers remain responsible for resource budgets."""
        observed = Counter(record.source for record in records)
        if any(observed.get(source, 0) != count for source, count in self.source_counts.items()) or (
                set(observed) - set(self.source_counts)):
            raise ValueError("record source counts differ from the declared legal universe")
        if len({record.record_id for record in records}) != len(records):
            raise ValueError("legal universe records contain duplicate IDs")
        if self.task is TaskType.DEDUPE:
            yield from combinations(records, 2)
            return
        by_source: dict[str, list[Record]] = {source: [] for source in self.source_counts}
        for record in records:
            by_source.setdefault(record.source, []).append(record)
        source_pairs = (list(combinations(self.source_counts, 2)) if self.task is TaskType.CLEAN_CLEAN
                        else sorted(self.allowed_source_pairs))
        for source_a, source_b in source_pairs:
            if source_a == source_b:
                yield from combinations(by_source.get(source_a, ()), 2)
                continue
            for left in by_source.get(source_a, ()):
                for right in by_source.get(source_b, ()):
                    yield left, right
