from collections import defaultdict
from itertools import combinations


def _ngrams(value: str, size: int = 3) -> set[str]:
    padded = f"  {value} "
    return {padded[index:index + size] for index in range(max(0, len(padded) - size + 1))}


def generate_candidates(records: list[dict], max_bucket_size: int = 60) -> dict[tuple[str, str], set[str]]:
    """Generate same-run candidates from exact contacts and bounded text blocks."""
    blocks: dict[tuple[str, str], list[str]] = defaultdict(list)
    for record in records:
        record_id = record["id"]
        fields = record["normalized_fields"]
        for field in ("phone", "email"):
            value = fields.get(field, "")
            if value:
                blocks[(field, value)].append(record_id)
        name = fields.get("name", "")
        for gram in _ngrams(name):
            blocks[("name_gram", gram)].append(record_id)
        address = fields.get("address", "")
        for gram in _ngrams(address):
            blocks[("address_gram", gram)].append(record_id)

    candidates: dict[tuple[str, str], set[str]] = defaultdict(set)
    for (kind, _), members in blocks.items():
        if len(members) < 2 or (kind.endswith("_gram") and len(members) > max_bucket_size):
            continue
        reason = "exact_" + kind if kind in {"phone", "email"} else kind
        for left, right in combinations(sorted(set(members)), 2):
            candidates[(left, right)].add(reason)
    return dict(candidates)
