"""TF-IDF candidate generation for entity resolution (A STG-03-FR-006 - A STG-03-FR-009)."""
from collections import defaultdict
import numpy as np

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
    if birth_year and province: return f"y{birth_year}|p{province}"
    if birth_year: return f"y{birth_year}"
    if province: return f"p{province}"
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
    if effective_k < 1: return {}
    for batch_start in range(0, n, _BATCH_SIZE):
        batch_end = min(batch_start + _BATCH_SIZE, n)
        batch = matrix[batch_start:batch_end]
        sim_block = (batch @ matrix.T).toarray()
        for local_i in range(sim_block.shape[0]):
            global_i = batch_start + local_i
            row = sim_block[local_i].copy()
            row[global_i] = 0.0
            top_indices = np.argpartition(row, -effective_k)[-effective_k:]
            for j in top_indices:
                if row[j] >= sim_threshold:
                    left, right = sorted((ids[global_i], ids[j]))
                    candidates[(left, right)].add("tfidf_name")
    return dict(candidates)

def tfidf_candidates(records: list[dict], top_k: int = TOP_K, sim_threshold: float = SIM_THRESHOLD) -> dict[tuple[str, str], set[str]]:
    partitions = defaultdict(list)
    for record in records: partitions[_partition_key(record)].append(record)
    all_candidates = defaultdict(set)
    for part_records in partitions.values():
        if len(part_records) < 2: continue
        ids = [r["id"] for r in part_records]
        texts = [r.get("normalized_fields", {}).get("name") or "" for r in part_records]
        if not any(texts): continue
        matrix = _build_vectorizer(texts, MIN_DF)
        if matrix is None: continue
        pairs = _top_k_pairs(ids, matrix, top_k, sim_threshold)
        for pair, reasons in pairs.items():
            all_candidates[pair] |= reasons
    return dict(all_candidates)
