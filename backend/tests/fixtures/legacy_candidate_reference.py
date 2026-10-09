# Frozen pre-migration coverage oracle from P-119 86900b0. Test-only.
"""Bounded candidate generation across and within all input sources."""

from collections import defaultdict
from itertools import combinations

from masterone.mdm.pipeline.tfidf_blocking import tfidf_candidates


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
        for field, collection in (("phone", "phones"), ("email", "emails")):
            for item in fields.get(collection, []):
                value = item.get("value", "")
                if value and item.get("quality") == "VALID":
                    blocks[(f"exact_{field}", value)].add(record_id)
        gov_id = fields.get("government_id", "")
        if gov_id and quality.get("government_id", "MISSING") == "VALID":
            blocks[("exact_gov_id", gov_id)].add(record_id)
        name = fields.get("name", "")
        if name:
            blocks[("exact_name", name)].add(record_id)
            for gram in _ngrams(name):
                blocks[("name_gram", gram)].add(record_id)
        address = fields.get("address", "")
        if address:
            for gram in _ngrams(address):
                blocks[("address_gram", gram)].add(record_id)

        # Composite blocking keys (§STG-03-FR-004)
        name_parts = fields.get("_name_parts", {})
        surname = name_parts.get("surname", "")
        dob_val = fields.get("dob", "")
        birth_year = dob_val[:4] if dob_val and len(dob_val) >= 4 else ""
        province = (fields.get("address_parts") or {}).get("province", "")

        if name and len(name) >= 3 and birth_year:
            blocks[("name_prefix_year", f"{name[:3]}|{birth_year}")].add(record_id)
        if surname and birth_year:
            blocks[("surname_year", f"{surname}|{birth_year}")].add(record_id)
        if name and province:
            blocks[("name_province", f"{name}|{province}")].add(record_id)

        phone_val = fields.get("phone", "")
        if phone_val and len(phone_val) >= 4 and birth_year and quality.get("phone") == "VALID":
            blocks[("phone_suffix_year", f"{phone_val[-4:]}|{birth_year}")].add(record_id)

    _COMPOSITE_KINDS = {"name_prefix_year", "surname_year", "phone_suffix_year"}
    _COMPOSITE_NAME_PROVINCE_LIMIT = 40
    _COMPOSITE_LIMIT = 30

    candidates: dict[tuple[str, str], set[str]] = defaultdict(set)
    for (kind, _), record_ids in blocks.items():
        if kind in {"exact_phone", "exact_email", "exact_gov_id"}:
            limit = 20
        elif kind == "exact_name":
            limit = max_bucket_size
        elif kind in _COMPOSITE_KINDS:
            limit = _COMPOSITE_LIMIT
        elif kind == "name_province":
            limit = _COMPOSITE_NAME_PROVINCE_LIMIT
        else:
            limit = 15
        if not 1 < len(record_ids) <= limit:
            continue
        for left, right in combinations(sorted(record_ids), 2):
            candidates[(left, right)].add(kind)

    # TF-IDF pass (§STG-03-FR-006) — merge on top of rule-based candidates
    tfidf = tfidf_candidates(records)
    for pair, reasons in tfidf.items():
        candidates[pair] |= reasons

    return dict(candidates)
