"""Deterministic person survivorship with observations and preserved overrides."""
from copy import deepcopy
from unicodedata import normalize

from masterone.mdm.pipeline.cluster_policy import load_policy
from masterone.mdm.pipeline.decision_semantics import verified
from masterone.mdm.pipeline.survivorship_policy import (
    QUALITY as QUALITY,
)
from masterone.mdm.pipeline.survivorship_policy import (
    eligible_primary,
    observation_rank,
    rank_with_priorities,
    source_timestamp,
)

FIELDS = ("name", "address", "dob", "government_id", "phone", "email")


def timestamp(value):
    """Only real timezone-aware source times; never substitute record/import IDs."""
    return source_timestamp(value)


def record_payload(row):
    nf = row.normalized_fields or {}
    original = getattr(row, "original_fields", None) or {}
    extra = getattr(row, "extra_fields", None) or {}
    return {"id": row.id, "external_id": row.external_id, "source_system": row.source_system,
        "entity_type": row.entity_type, "raw_fields": row.raw_fields, "normalized_fields": nf,
        "quality_flags": row.quality_flags,
        "extra_fields": extra,
        "source_updated_at": nf.get("_source_updated_at") or original.get("source_updated_at") or extra.get("source_updated_at")}


def golden_payload(row):
    from masterone.mdm.pipeline.cluster_policy import enabled
    if enabled():
        return record_payload(row)
    return {"id": row.id, "external_id": row.external_id,
            "source_system": row.source_system, "raw_fields": row.raw_fields,
            "normalized_fields": getattr(row, "normalized_fields", {}) or {},
            "source_updated_at": getattr(row, "source_updated_at", None)}


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
    return rank_with_priorities(observation, priorities)


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
            items = [{"value": raw[field], "quality": nf.get("_field_quality", {}).get(field, "UNKNOWN")}]
        for item in items:
            value = str(item.get("value") or "").strip()
            quality = str(item.get("quality", nf.get("_field_quality", {}).get(field, "UNKNOWN")) or "UNKNOWN").upper()
            if not value:
                continue
            if field == "name":
                value = normalize("NFC", " ".join(value.split()))
            time = timestamp(item.get("observed_at") or nf.get("_field_timestamps", {}).get(field)
                             or record.get("source_updated_at") or nf.get("_source_updated_at"))
            observation = {"value": value, "source_record_id": record["id"],
                "source_system": record.get("source_system", ""), "external_id": record.get("external_id") or record["id"],
                "quality": quality, "verified": (item.get("verified") is True or item.get("verification") == "verified")
                    if "verified" in item or "verification" in item else verified(nf, field),
                "observed_at": time.isoformat() if time else None, "shared_risk": item.get("shared") is True or item.get("shared_risk") is True
                or quality == "SHARED" or nf.get(f"_{field}_shared_risk") is True,
                "provenance": "SOURCE_OBSERVATION"}
            observation["eligible_primary"] = eligible_primary(field, observation)
            observation["excluded_reason"] = None if observation["eligible_primary"] else (
                "SHARED_CONTACT" if field in {"phone", "email"} and observation["shared_risk"] else quality)
            yield observation


def build_golden_v2(member_ids, by_id, *, existing_golden=None, policy=None):
    policy = policy or load_policy()
    records = [by_id[rid] for rid in sorted(member_ids)]
    fields = {}
    for field in FIELDS:
        observed = sorted(observations(field, records), key=lambda o: (
            not o["eligible_primary"], observation_rank(field, o, policy)))
        grouped = {}
        for observation in observed:
            grouped.setdefault(observation["value"], []).append(observation)
        values = [{**sources[0], "sources": sources} for sources in grouped.values()]
        if values:
            eligible = [v for v in values if v["eligible_primary"]]
            if eligible:
                fields[field] = {**eligible[0], "provenance": "SURVIVORSHIP",
                                 "alternatives": [v for v in values if v is not eligible[0]]}
            else:
                fields[field] = {"value": None, "source_record_id": None, "source_system": None,
                                 "provenance": "SURVIVORSHIP", "eligible_primary": False,
                                 "excluded_reason": "NO_ELIGIBLE_VALUE", "alternatives": values}
            if field in {"phone", "email"}:
                fields[field]["values"] = eligible
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
            "quality": "STEWARD", "observed_at": None, "eligible_primary": True,
            "excluded_reason": None, "alternatives": alternatives}
    result = {"member_record_ids": sorted(member_ids), "fields": fields, "survivorship_version": policy.survivorship_version}
    if overrides or existing_golden and "_golden_override" in existing_golden:
        result["_golden_override"] = overrides
    return result
