"""Deterministic candidate generation over prediction-only feature rows."""
from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations
from typing import Iterable, Mapping, Sequence

from standalone.contracts.core import DatasetMetadata, RecordFeatures, TaskType
from standalone.contracts.interfaces import CandidateRecord, IndexPair


@dataclass(frozen=True)
class CandidatePair:
    left_index: int
    right_index: int
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.left_index >= self.right_index:
            raise ValueError("candidate endpoints must be canonical and distinct")

    @property
    def blocking_reasons(self) -> tuple[str, ...]:
        return self.reasons


def _dispatch(records, sources_or_task, metadata):
    protocol_mode = isinstance(sources_or_task, TaskType)
    if protocol_mode:
        if metadata is not None or any(not isinstance(record, CandidateRecord) for record in records):
            raise ValueError("CandidateGenerator protocol accepts CandidateRecord rows and TaskType")
        sources = [record.source for record in records]
        rows = [record.features for record in records]
        metadata = DatasetMetadata("candidate-input", "1", sources_or_task,
                                   tuple(dict.fromkeys(sources)))
    else:
        rows = list(records)
        sources = list(sources_or_task)
        if metadata is None:
            raise ValueError("dataset metadata is required for benchmark candidate generation")
    if len(rows) != len(sources):
        raise ValueError("records and sources lengths differ")
    return rows, sources, metadata, protocol_mode


def _protocol_result(candidates, protocol_mode):
    if protocol_mode:
        return tuple(IndexPair(item.left_index, item.right_index, item.reasons) for item in candidates)
    return candidates


def _legal(i: int, j: int, sources: Sequence[str], metadata: DatasetMetadata) -> bool:
    if i == j:
        return False
    a, b = sources[i], sources[j]
    if metadata.task is TaskType.DEDUPE:
        return a == b
    if metadata.task is TaskType.CLEAN_CLEAN:
        return a != b
    if metadata.allowed_source_pairs:
        return tuple(sorted((a, b))) in metadata.allowed_source_pairs
    return a != b


def _pair_set(groups: Iterable[tuple[str, Iterable[int]]], sources: Sequence[str],
              metadata: DatasetMetadata) -> dict[tuple[int, int], set[str]]:
    out: dict[tuple[int, int], set[str]] = defaultdict(set)
    for reason, indexes in groups:
        unique = sorted(set(indexes))
        for i, j in combinations(unique, 2):
            if _legal(i, j, sources, metadata):
                out[(i, j)].add(reason)
    return out


def _finish(pairs: Mapping[tuple[int, int], set[str]]) -> tuple[CandidatePair, ...]:
    return tuple(CandidatePair(i, j, tuple(sorted(reasons)))
                 for (i, j), reasons in sorted(pairs.items()))


class ExactBlocker:
    def __init__(self, fields: Sequence[str] = ("name", "phone", "email", "address")) -> None:
        self.fields = tuple(fields)

    def generate(self, records: Sequence[RecordFeatures | CandidateRecord],
                 sources: Sequence[str] | TaskType, metadata: DatasetMetadata | None = None):
        records, sources, metadata, protocol_mode = _dispatch(records, sources, metadata)
        groups = []
        for field in self.fields:
            buckets: dict[str, list[int]] = defaultdict(list)
            for i, record in enumerate(records):
                value = record.fields.get(field)
                if value and value.strip():
                    buckets[value.casefold()].append(i)
            groups.extend((f"exact:{field}", indexes) for indexes in buckets.values())
        return _protocol_result(_finish(_pair_set(groups, sources, metadata)), protocol_mode)


class MultiRuleUnion:
    def __init__(self, rules: Sequence[tuple[str, str]]) -> None:
        if not rules or any(not name or not field for name, field in rules):
            raise ValueError("at least one named field rule is required")
        self.rules = tuple(rules)

    def generate(self, records: Sequence[RecordFeatures | CandidateRecord],
                 sources: Sequence[str] | TaskType, metadata: DatasetMetadata | None = None):
        records, sources, metadata, protocol_mode = _dispatch(records, sources, metadata)
        groups = []
        for rule, field in self.rules:
            buckets: dict[str, list[int]] = defaultdict(list)
            for i, record in enumerate(records):
                value = record.fields.get(field)
                if value and value.strip():
                    buckets[value.casefold()].append(i)
            groups.extend((rule, indexes) for indexes in buckets.values())
        return _protocol_result(_finish(_pair_set(groups, sources, metadata)), protocol_mode)


def _ngrams(text: str, n: int) -> set[str]:
    padded = f"  {text.casefold()}  "
    return {padded[i:i+n] for i in range(max(0, len(padded)-n+1))}


class CharNgramTfidfTopK:
    """Small standard-library sparse char TF-IDF; no dense all-pairs matrix."""
    def __init__(self, fields: Sequence[str] = ("name",), ngram_range: tuple[int, int] = (2, 4),
                 top_k: int = 10, min_similarity: float = 0.0) -> None:
        if top_k < 1 or ngram_range[0] < 1 or ngram_range[1] < ngram_range[0]:
            raise ValueError("invalid TF-IDF settings")
        self.fields, self.ngram_range = tuple(fields), ngram_range
        self.top_k, self.min_similarity = top_k, min_similarity

    def generate(self, records: Sequence[RecordFeatures | CandidateRecord],
                 sources: Sequence[str] | TaskType, metadata: DatasetMetadata | None = None):
        records, sources, metadata, protocol_mode = _dispatch(records, sources, metadata)
        collected: dict[tuple[int, int], set[str]] = defaultdict(set)
        for field in self.fields:
            docs = [record.fields.get(field) or "" for record in records]
            terms = [set().union(*(_ngrams(value, n) for n in range(self.ngram_range[0], self.ngram_range[1]+1)))
                     if value.strip() else set() for value in docs]
            df: dict[str, int] = defaultdict(int)
            for row in terms:
                for term in row: df[term] += 1
            vectors: list[dict[str, float]] = []
            for row in terms:
                v = {term: (1.0 + math.log(max(1, len(records))/(1+df[term]))) for term in row}
                norm = math.sqrt(sum(x*x for x in v.values()))
                vectors.append({term: value/norm for term, value in v.items()} if norm else {})
            postings: dict[str, list[int]] = defaultdict(list)
            for i, row in enumerate(vectors):
                for term in row: postings[term].append(i)
            for i, vector in enumerate(vectors):
                sims: dict[int, float] = defaultdict(float)
                for term, weight in vector.items():
                    for j in postings[term]:
                        if j > i and _legal(i, j, sources, metadata):
                            sims[j] += weight * vectors[j][term]
                eligible = sorted(((score, j) for j, score in sims.items() if score >= self.min_similarity),
                                  key=lambda item: (-item[0], item[1]))[:self.top_k]
                for _, j in eligible:
                    pair = (i, j)
                    collected.setdefault(pair, set()).add(f"tfidf:{field}")
        return _protocol_result(_finish(collected), protocol_mode)


class HybridBlocker:
    def __init__(self, exact: ExactBlocker | MultiRuleUnion | None = None,
                 tfidf: CharNgramTfidfTopK | None = None) -> None:
        self.exact = exact or ExactBlocker()
        self.tfidf = tfidf or CharNgramTfidfTopK()

    def generate(self, records: Sequence[RecordFeatures | CandidateRecord],
                 sources: Sequence[str] | TaskType, metadata: DatasetMetadata | None = None):
        records, sources, metadata, protocol_mode = _dispatch(records, sources, metadata)
        combined: dict[tuple[int, int], set[str]] = defaultdict(set)
        for candidate in (*self.exact.generate(records, sources, metadata),
                          *self.tfidf.generate(records, sources, metadata)):
            combined[(candidate.left_index, candidate.right_index)].update(candidate.reasons)
        return _protocol_result(_finish(combined), protocol_mode)
