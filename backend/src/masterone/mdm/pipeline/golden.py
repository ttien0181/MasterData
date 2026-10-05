"""Golden Record survivorship rules (architecture §22).

build_golden() selects the best field value from each cluster member using:
  1. Source reliability order (§22 source_reliability)
  2. Verified flag (quality == VALID preferred over UNKNOWN/INVALID)
  3. Field quality score
  4. Recency (source_updated_at if present, else external_id as tiebreak)
  5. Steward decision (overrides from normalized_fields._golden_override)

Returns a golden record dict with field provenance attached.
"""

from unicodedata import normalize

# Source reliability priority (lower = more reliable) — §22
SOURCE_PRIORITY: dict[str, int] = {"ERP": 0, "CRM": 1, "BILL": 2}

# Fields included in the golden record
FIELDS = ("name", "phone", "email", "address", "dob", "government_id")

# Quality tiers — higher is better
_QUALITY_TIER: dict[str, int] = {
    "VALID": 3, "UNKNOWN": 2, "PLACEHOLDER": 1, "INVALID": 0, "MISSING": 0, "SHARED": 1,
}


def _field_quality(record: dict, field: str) -> int:
    q = record.get("normalized_fields", {}).get("_field_quality", {}).get(field, "UNKNOWN")
    return _QUALITY_TIER.get(q, 2)


def _recency_key(record: dict) -> str:
    """Use source_updated_at if available; fall back to external_id for stable sort."""
    return record.get("source_updated_at") or record.get("external_id") or record["id"]


def _display_value(field: str, value: str) -> str:
    if field == "name":
        return normalize("NFC", " ".join(value.split()))
    return value


def _best_record_for_field(field: str, ordered: list[dict]) -> dict | None:
    """Pick the best record for a field using survivorship priority chain."""
    candidates = [
        r for r in ordered
        if (r.get("raw_fields", {}).get(field) or "").strip()
    ]
    if not candidates:
        return None

    # Sort: highest quality first, then source reliability (already in ordered),
    # then most recent (recency desc = lexicographically largest updated_at)
    candidates.sort(
        key=lambda r: (_field_quality(r, field), -SOURCE_PRIORITY.get(r["source_system"], 99),
                       _recency_key(r)),
        reverse=True,
    )
    return candidates[0]


def build_golden(member_ids: list[str], records_by_id: dict[str, dict]) -> dict:
    """Build golden record for a cluster using full survivorship rules (§22).

    Returns:
      {
        "member_record_ids": [...],
        "fields": {
          "<field>": {
            "value": ...,
            "source_record_id": ...,
            "source_system": ...,
            "quality": ...,
            "provenance": "SURVIVORSHIP" | "STEWARD_OVERRIDE",
          }
        }
      }
    """
    records = [records_by_id[rid] for rid in member_ids if rid in records_by_id]
    # Primary sort: source reliability, secondary: external_id for determinism
    ordered = sorted(records, key=lambda r: (
        SOURCE_PRIORITY.get(r["source_system"], 99),
        r.get("external_id") or r["id"],
    ))

    fields: dict[str, dict] = {}

    for field in FIELDS:
        # Check for steward override in normalized_fields (§22 — steward decision)
        for rec in ordered:
            override = rec.get("normalized_fields", {}).get("_golden_override", {}).get(field)
            if override:
                fields[field] = {
                    "value": _display_value(field, str(override)),
                    "source_record_id": rec["id"],
                    "source_system": rec["source_system"],
                    "quality": "STEWARD",
                    "provenance": "STEWARD_OVERRIDE",
                }
                break
        else:
            best = _best_record_for_field(field, ordered)
            if best:
                raw_value = (best.get("raw_fields", {}).get(field) or "").strip()
                q = best.get("normalized_fields", {}).get("_field_quality", {}).get(field, "UNKNOWN")
                fields[field] = {
                    "value": _display_value(field, raw_value),
                    "source_record_id": best["id"],
                    "source_system": best["source_system"],
                    "quality": q,
                    "provenance": "SURVIVORSHIP",
                }

    return {"member_record_ids": sorted(member_ids), "fields": fields}
