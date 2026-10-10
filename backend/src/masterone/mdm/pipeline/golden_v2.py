"""Deterministic person survivorship with observations and preserved overrides."""
from copy import deepcopy
from datetime import UTC, datetime
from unicodedata import normalize

from masterone.mdm.pipeline.cluster_policy import load_policy
from masterone.mdm.pipeline.decision_semantics import verified

FIELDS = ("name", "address", "dob", "government_id", "phone", "email")
QUALITY = {"VALID": 3, "UNKNOWN": 2, "SHARED": 1, "PLACEHOLDER": 0, "INVALID": 0, "MISSING": 0}


def timestamp(value):
    """Only real timezone-aware source times; never substitute record/import IDs."""
    if not value:
        return None
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if result.tzinfo is None:
        return None
    return result.astimezone(UTC)


def record_payload(row):
    nf = row.normalized_fields or {}
    original = getattr(row, "original_fields", None) or {}
    extra = getattr(row, "extra_fields", None) or {}
    return {"id": row.id, "external_id": row.external_id, "source_system": row.source_system,
        "entity_type": row.entity_type, "raw_fields": row.raw_fields, "normalized_fields": nf,
        "quality_flags": row.quality_flags,
        "source_updated_at": nf.get("_source_updated_at") or original.get("source_updated_at") or extra.get("source_updated_at")}


def golden_payload(row):
    from masterone.mdm.pipeline.cluster_policy import enabled
    if enabled():
        return record_payload(row)
    return {"id": row.id, "external_id": row.external_id,
            "source_system": row.source_system, "raw_fields": row.raw_fields}


def merge_existing_golden(proposals, members):
    from fastapi import HTTPException

    from masterone.mdm.pipeline.cluster_policy import enabled
    if not enabled():
        return None
    overrides = {}
    for proposal in proposals:
        if not set(proposal.member_record_ids) & set(members):
            continue
        for field, value in proposal.golden_fields.get("_golden_override", {}).items():
            if field in overrides and overrides[field] != value:
                raise HTTPException(409, "Conflicting golden overrides require explicit resolution")
            overrides[field] = deepcopy(value)
    return {"_golden_override": overrides} if overrides else None


def rank(observation, priorities):
    updated = timestamp(observation.get("observed_at"))
    return (priorities.get(observation["source_system"], 99), not observation["verified"],
            -QUALITY.get(observation["quality"], 2), updated is None,
            -updated.timestamp() if updated else 0, observation["source_system"],
            observation["external_id"], observation["source_record_id"], observation["value"])


def observations(field, records):
    for record in records:
        nf = record.get("normalized_fields", {})
        raw = record.get("raw_fields", {})
        multi = field in {"phone", "email"}
        items = list(nf.get("phones" if field == "phone" else "emails", [])) if multi else []
        scalar = nf.get(field) if multi else raw.get(field)
        if scalar and not any(i.get("value") == scalar for i in items):
            items.append({"value": scalar, "quality": nf.get("_field_quality", {}).get(field, "UNKNOWN")})
        if not items and raw.get(field):
            items = [{"value": raw[field], "quality": "UNKNOWN"}]
        for item in items:
            value = str(item.get("value") or "").strip()
            quality = item.get("quality", nf.get("_field_quality", {}).get(field, "UNKNOWN"))
            if not value or multi and quality in {"INVALID", "PLACEHOLDER", "MISSING"}:
                continue
            if field == "name":
                value = normalize("NFC", " ".join(value.split()))
            time = timestamp(item.get("observed_at") or nf.get("_field_timestamps", {}).get(field)
                             or record.get("source_updated_at") or nf.get("_source_updated_at"))
            yield {"value": value, "source_record_id": record["id"],
                "source_system": record.get("source_system", ""), "external_id": record.get("external_id") or record["id"],
                "quality": quality, "verified": (item.get("verified") is True or item.get("verification") == "verified")
                    if "verified" in item or "verification" in item else verified(nf, field),
                "observed_at": time.isoformat() if time else None, "shared_risk": item.get("shared") is True or item.get("shared_risk") is True
                or quality == "SHARED" or nf.get(f"_{field}_shared_risk") is True,
                "provenance": "SOURCE_OBSERVATION"}


def build_golden_v2(member_ids, by_id, *, existing_golden=None, policy=None):
    policy = policy or load_policy()
    priorities = dict(policy.source_priority)
    records = [by_id[rid] for rid in sorted(member_ids)]
    fields = {}
    for field in FIELDS:
        observed = sorted(observations(field, records), key=lambda o: rank(o, priorities))
        grouped = {}
        for observation in observed:
            grouped.setdefault(observation["value"], []).append(observation)
        values = [{**sources[0], "sources": sources} for sources in grouped.values()]
        if values:
            fields[field] = {**values[0], "provenance": "SURVIVORSHIP",
                             "alternatives": values[1:]}
            if field in {"phone", "email"}:
                fields[field]["values"] = values
    overrides = {}
    for record in records:
        for field, value in record.get("normalized_fields", {}).get("_golden_override", {}).items():
            if field in overrides and overrides[field] != value:
                raise ValueError("Conflicting record-level golden overrides require review")
            overrides[field] = value
    if existing_golden:
        overrides.update(deepcopy(existing_golden.get("_golden_override", {})))
    for field, value in overrides.items():
        if field not in FIELDS:
            continue
        base = fields.get(field, {})
        alternatives = ([{k: v for k, v in base.items() if k not in {"values", "alternatives"}}]
                        + base.get("alternatives", [])) if base else []
        fields[field] = {**base, "value": value, "provenance": "STEWARD_OVERRIDE",
            "source_record_id": None, "source_system": None, "verified": False,
            "sources": [], "external_id": None,
            "quality": "STEWARD", "observed_at": None, "alternatives": alternatives}
    result = {"member_record_ids": sorted(member_ids), "fields": fields, "survivorship_version": "p119_golden_v2"}
    if overrides or existing_golden and "_golden_override" in existing_golden:
        result["_golden_override"] = overrides
    return result
