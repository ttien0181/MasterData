"""TF-IDF candidate generation for entity resolution (A STG-03-FR-006 - A STG-03-FR-009)."""
import json
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from hashlib import sha256
from heapq import nsmallest

import numpy as np

from masterone.mdm.pipeline.candidate_planner import WorkloadPolicy, plan_workload


@dataclass(frozen=True)
class SparseTfidfConfig:
    fields: tuple[str, ...] = ("name",)
    ngram_range: tuple[int, int] = (3, 4)
    top_k: int = 10
    min_similarity: float = 0.30
    query_batch_size: int = 64
    target_batch_size: int = 256

    def __post_init__(self):
        if not self.fields or len(set(self.fields)) != len(self.fields) or set(self.fields) - {"name", "address"}:
            raise ValueError("TF-IDF fields must be distinct name/address fields")
        if any(type(v) is not int or v < 1 for v in (*self.ngram_range, self.top_k,
                                                    self.query_batch_size, self.target_batch_size)):
            raise ValueError("TF-IDF sizes must be positive integers")
        if len(self.ngram_range) != 2 or self.ngram_range[0] > self.ngram_range[1]:
            raise ValueError("Invalid ngram range")
        if not 0 < self.min_similarity <= 1:
            raise ValueError("Similarity threshold must be in (0, 1]")


def fold_text(value):
    if not isinstance(value, str):
        return ""
    text = unicodedata.normalize("NFKD", value.casefold().replace("đ", "d"))
    return " ".join("".join(c for c in text if not unicodedata.combining(c)).split())


class GlobalTfidfIndex:
    """Full-record IDF, unique text vectors, bounded sparse similarity tiles.

    Empty records count in corpus N. Compression happens before vectorization,
    with weighted document frequencies preserving repeated record semantics.
    No DOB, province, source or ingest-chunk filter is used.
    """

    def __init__(self, records, *, field="name", config=None, policy=None, approved_plan_id=None):
        from sklearn.feature_extraction.text import CountVectorizer
        from sklearn.preprocessing import normalize

        self.config = config or SparseTfidfConfig()
        self.policy = policy or WorkloadPolicy()
        self.field = field
        if field not in {"name", "address"}:
            raise ValueError("Unsupported TF-IDF field")
        self.postings = defaultdict(list)
        rows = [(r["id"], fold_text(r.get("normalized_fields", {}).get(field))) for r in records]
        if any(not isinstance(rid, str) or not rid for rid, _ in rows) or len({rid for rid, _ in rows}) != len(rows):
            raise ValueError("Snapshot IDs must be distinct nonempty strings")
        rows.sort()
        self.snapshot_digest = sha256(json.dumps(rows, ensure_ascii=False).encode()).hexdigest()
        self.record_count = len(rows)
        for rid, text in rows:
            if text:
                self.postings[text].append(rid)
        self.documents = sorted(self.postings)
        cfg = self.config
        # Conservative allocation estimate before sklearn builds the vocabulary.
        gram_bound = sum(len(text) * (cfg.ngram_range[1] - cfg.ngram_range[0] + 1) for text in self.documents)
        self.memory_estimate = (gram_bound * 512 + len(rows) * 256
                                + cfg.query_batch_size * cfg.target_batch_size * 24
                                + cfg.query_batch_size * (cfg.top_k + 1) * 128)
        self.plan = plan_workload({"source": f"tfidf_{field}", "record_count": len(rows),
                                  "unique_vectors": len(self.documents),
                                  "estimated_similarity_cells": len(self.documents) ** 2,
                                  "estimated_memory_bytes": self.memory_estimate}, self.policy,
                                 snapshot_digest=self.snapshot_digest)
        self.matrix = None
        self.vectorizer = CountVectorizer(analyzer="char_wb", ngram_range=cfg.ngram_range,
                                          lowercase=False, dtype=np.float64)
        self.idf = np.array([])
        self.dot_products = 0
        if self.documents:
            try:
                counts = self.vectorizer.fit_transform(self.documents).tocsr()
            except ValueError as error:
                if "empty vocabulary" not in str(error):
                    raise
            else:
                weights = np.array([len(self.postings[text]) for text in self.documents])
                present = counts.copy()
                present.data[:] = 1
                df = np.asarray(present.T @ weights).ravel()
                self.idf = np.log((1 + len(rows)) / (1 + df)) + 1
                counts.data = 1 + np.log(counts.data)
                self.matrix = normalize(counts.multiply(self.idf), norm="l2").tocsr()
                unique_df = np.bincount(present.indices, minlength=counts.shape[1])
                self.dot_products = sum(int(n) ** 2 for n in unique_df)
        self.plan = plan_workload({"source": f"tfidf_{field}", "record_count": len(rows),
                                  "unique_vectors": len(self.documents),
                                  "max_duplicate_block_size": max(map(len, self.postings.values()), default=0),
                                  "estimated_pairs": sum(map(len, self.postings.values())) * min(cfg.top_k, max(0, len(rows) - 1)),
                                  "estimated_memory_bytes": self.memory_estimate,
                                  "estimated_similarity_cells": len(self.documents) ** 2,
                                  "estimated_dot_products": self.dot_products}, self.policy,
                                 snapshot_digest=self.snapshot_digest, approved_plan_id=approved_plan_id)
        self.metrics = {**self.plan, "matrix_nnz": self.matrix.nnz if self.matrix is not None else 0,
                        "max_similarity_tile_nnz": 0, "directed_observations": 0,
                        "top_k": cfg.top_k, "query_batch_size": cfg.query_batch_size,
                        "target_batch_size": cfg.target_batch_size, "corpus_records": len(rows)}

    def search(self):
        if self.matrix is None:
            return
        cfg = self.config
        size = len(self.documents)
        target = self.matrix.T.tocsc()
        for start in range(0, size, cfg.query_batch_size):
            end = min(size, start + cfg.query_batch_size)
            best = [[] for _ in range(end - start)]
            for offset in range(0, size, cfg.target_batch_size):
                tile = (self.matrix[start:end] @ target[:, offset:offset + cfg.target_batch_size]).tocsr()
                self.metrics["max_similarity_tile_nnz"] = max(self.metrics["max_similarity_tile_nnz"], tile.nnz)
                for local in range(end - start):
                    lo, hi = tile.indptr[local:local + 2]
                    candidates = best[local] + [(min(1.0, float(score)), offset + int(pos))
                        for pos, score in zip(tile.indices[lo:hi], tile.data[lo:hi])
                        if score >= cfg.min_similarity]
                    best[local] = nsmallest(cfg.top_k + 1, candidates,
                        key=lambda item: (-item[0], self.postings[self.documents[item[1]]][0]))
            for local, vectors in enumerate(best):
                for query_id in self.postings[self.documents[start + local]]:
                    neighbors = ((score, rid) for score, pos in vectors
                                 for rid in self.postings[self.documents[pos]][:cfg.top_k + 1] if rid != query_id)
                    for score, rid in nsmallest(cfg.top_k, neighbors, key=lambda item: (-item[0], item[1])):
                        self.metrics["directed_observations"] += 1
                        yield query_id, rid, score

TOP_K = 10
SIM_THRESHOLD = 0.30
MAX_DF = 0.05
MIN_DF = 2
_BATCH_SIZE = 2000

def _partition_key(record: dict) -> str:
    fields = record.get("normalized_fields", {})
    dob = fields.get("dob", "")
    birth_year = dob[:4] if dob and len(dob) >= 4 and dob[:4].isdigit() else ""
    province = (fields.get("address_parts") or {}).get("province", "")
    if birth_year and province:
        return f"y{birth_year}|p{province}"
    if birth_year:
        return f"y{birth_year}"
    if province:
        return f"p{province}"
    return "all"

def _adaptive_max_df(n_docs: int) -> int:
    return max(2, int(MAX_DF * n_docs))

def _build_vectorizer(texts: list[str], min_df: int):
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
    except ImportError:
        return None
    n = len(texts)
    max_df_val = _adaptive_max_df(n)
    for _min_df in (min_df, 1):
        try:
            vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 4), max_df=max_df_val, min_df=_min_df, sublinear_tf=True, norm="l2")
            matrix = vec.fit_transform(texts)
            return matrix
        except ValueError:
            continue
    return None

def _top_k_pairs(ids: list[str], matrix, top_k: int, sim_threshold: float) -> dict[tuple[str, str], set[str]]:
    n = len(ids)
    candidates = defaultdict(set)
    effective_k = min(top_k, n - 1)
    if effective_k < 1:
        return {}
    for batch_start in range(0, n, _BATCH_SIZE):
        batch_end = min(batch_start + _BATCH_SIZE, n)
        batch = matrix[batch_start:batch_end]
        sim_block = (batch @ matrix.T).tocsr()
        for local_i in range(sim_block.shape[0]):
            global_i = batch_start + local_i
            # Rollback path keeps legacy argpartition tie semantics, but only
            # expands one O(N) row; never materialize a dense similarity block.
            row = np.zeros(n)
            lo, hi = sim_block.indptr[local_i:local_i + 2]
            row[sim_block.indices[lo:hi]] = sim_block.data[lo:hi]
            row[global_i] = 0.0
            top_indices = np.argpartition(row, -effective_k)[-effective_k:]
            for j in top_indices:
                if row[j] >= sim_threshold:
                    left, right = sorted((ids[global_i], ids[j]))
                    candidates[(left, right)].add("tfidf_name")
    return dict(candidates)

MAX_MATCH_PARTITION = 5000  # exact top-k is O(n^2) per partition; larger ones are matched in windows
MATCH_WINDOW_OVERLAP = 500  # neighbours near a window edge are still compared with each other


def _match_windows(part_records: list[dict]) -> list[list[dict]]:
    """Partitions up to MAX_MATCH_PARTITION are matched whole. Larger ones are cut into
    overlapping windows over their name-sorted order, so similar names share a window."""
    if len(part_records) <= MAX_MATCH_PARTITION:
        return [part_records]
    ordered = sorted(part_records, key=lambda r: ((r.get("normalized_fields", {}).get("name") or ""), r["id"]))
    step = MAX_MATCH_PARTITION - MATCH_WINDOW_OVERLAP
    return [ordered[start:start + MAX_MATCH_PARTITION] for start in range(0, len(ordered) - MATCH_WINDOW_OVERLAP, step)]


def tfidf_candidates(records: list[dict], top_k: int = TOP_K, sim_threshold: float = SIM_THRESHOLD) -> dict[tuple[str, str], set[str]]:
    partitions = defaultdict(list)
    for record in records:
        partitions[_partition_key(record)].append(record)
    all_candidates = defaultdict(set)
    for window in (w for part in partitions.values() for w in _match_windows(part)):
        part_records = window
        if len(part_records) < 2:
            continue
        ids = [r["id"] for r in part_records]
        texts = [r.get("normalized_fields", {}).get("name") or "" for r in part_records]
        if not any(texts):
            continue
        matrix = _build_vectorizer(texts, MIN_DF)
        if matrix is None:
            continue
        pairs = _top_k_pairs(ids, matrix, top_k, sim_threshold)
        for pair, reasons in pairs.items():
            all_candidates[pair] |= reasons
    return dict(all_candidates)
