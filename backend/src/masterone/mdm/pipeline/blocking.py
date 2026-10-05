"""Bounded candidate generation across and within all input sources."""

from collections import defaultdict
from itertools import combinations


def _ngrams(value: str, size: int = 3) -> set[str]:
    padded = f"  {value} "
    return {padded[index:index + size] for index in range(max(0, len(padded) - size + 1))}


def generate_candidates(records: list[dict], max_bucket_size: int = 60,
                        top_k: int = 12) -> dict[tuple[str, str], set[str]]:
    """Find candidates using reliable contacts and small text blocks.

    ``top_k`` stays in the signature for existing callers. No pass partitions
    records by CRM/ERP/BILL; duplicates within one source remain candidates.
    """
    del top_k
    blocks: dict[tuple[str, str], set[str]] = defaultdict(set)
    for record in records:
        record_id = record["id"]
        fields = record["normalized_fields"]
        quality = fields.get("_field_quality", {})
        for field in ("phone", "email"):
            value = fields.get(field, "")
            if value and quality.get(field, "VALID") == "VALID":
                blocks[(f"exact_{field}", value)].add(record_id)
        name = fields.get("name", "")
        if name:
            blocks[("exact_name", name)].add(record_id)
            for gram in _ngrams(name):
                blocks[("name_gram", gram)].add(record_id)
        address = fields.get("address", "")
        if address:
            for gram in _ngrams(address):
                blocks[("address_gram", gram)].add(record_id)

    candidates: dict[tuple[str, str], set[str]] = defaultdict(set)
    for (kind, _), record_ids in blocks.items():
        limit = 20 if kind in {"exact_phone", "exact_email"} else (
            max_bucket_size if kind == "exact_name" else 15)
        if not 1 < len(record_ids) <= limit:
            continue
        for left, right in combinations(sorted(record_ids), 2):
            candidates[(left, right)].add(kind)
    return dict(candidates)
