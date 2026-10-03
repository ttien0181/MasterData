"""Candidate Generation / Blocking theo kiến trúc v3.1, mục 7.

Chiến lược multi-pass:
  1. Exact Blocking — phone/email EX (bỏ qua SHARED/PLACEHOLDER)
  2. Composite Blocking — name_ascii + birth_year
  3. N-gram Blocking — character n-gram trên name (bounded)

Mục 7.1: Không tạo block lớn nếu value là SHARED hoặc document frequency cao.
Mục 7.2: Composite blocking phù hợp dữ liệu Việt Nam.
"""

from collections import defaultdict
from itertools import combinations

# Giá trị phone/email có quá nhiều record → không blocking (mục 6.1, 7.1)
_MAX_EXACT_BUCKET = 20   # phone/email xuất hiện > 20 record → SHARED, bỏ qua
_MAX_NGRAM_BUCKET = 15   # n-gram block > 15 → bỏ qua (giảm từ 60 để tránh explosion với 7K records)


def _ngrams(value: str, size: int = 3) -> set[str]:
    padded = f"  {value} "
    return {padded[i:i + size] for i in range(max(0, len(padded) - size + 1))}


def _field_quality(record: dict, field: str) -> str:
    nf = record.get("normalized_fields", {})
    return nf.get("_field_quality", {}).get(field, "VALID")


def _birth_year(record: dict) -> str:
    """Lấy năm sinh từ dob nếu có."""
    import re
    dob = record.get("normalized_fields", {}).get("dob", "") or ""
    m = re.search(r"\d{4}", dob)
    return m.group() if m else ""


def generate_candidates(
    records: list[dict],
    max_exact_bucket: int = _MAX_EXACT_BUCKET,
    max_ngram_bucket: int = _MAX_NGRAM_BUCKET,
) -> dict[tuple[str, str], set[str]]:
    """Tạo candidate pairs không theo O(N²).

    Trả về {(left_id, right_id): set_of_blocking_reasons}.
    """
    blocks: dict[tuple[str, str], list[str]] = defaultdict(list)

    for record in records:
        rid = record["id"]
        nf = record.get("normalized_fields", {})

        # Pass 1: Exact contact blocking — chỉ khi VALID
        for field in ("phone", "email"):
            quality = _field_quality(record, field)
            value = nf.get(field, "") or ""
            if value and quality == "VALID":
                blocks[(field, value)].append(rid)

        # Pass 2: Composite blocking — name_ascii + birth_year (mục 7.2)
        name_asc = nf.get("name_ascii", "") or ""
        birth_yr = _birth_year(record)
        if name_asc and birth_yr:
            blocks[("name_ascii_year", f"{name_asc[:12]}_{birth_yr}")].append(rid)

        # Pass 3: N-gram trên name_ascii (bounded — mục 7.3)
        if name_asc:
            for gram in _ngrams(name_asc):
                blocks[("name_gram", gram)].append(rid)

        # Pass 4: N-gram trên address (bounded)
        addr = nf.get("address", "") or ""
        if addr:
            for gram in _ngrams(addr):
                blocks[("address_gram", gram)].append(rid)

    # Bước 2: lọc bucket quá lớn (SHARED/high-frequency) và tạo pairs
    candidates: dict[tuple[str, str], set[str]] = defaultdict(set)
    for (kind, value), members in blocks.items():
        unique = sorted(set(members))
        if len(unique) < 2:
            continue

        # Exact contact: bỏ qua nếu quá nhiều record dùng chung (mục 7.1)
        if kind in ("phone", "email") and len(unique) > max_exact_bucket:
            continue

        # N-gram: bỏ qua bucket quá lớn (mục 7.3)
        if kind.endswith("_gram") and len(unique) > max_ngram_bucket:
            continue

        reason = "exact_" + kind if kind in ("phone", "email") else kind
        for left, right in combinations(unique, 2):
            candidates[(left, right)].add(reason)

    return dict(candidates)
