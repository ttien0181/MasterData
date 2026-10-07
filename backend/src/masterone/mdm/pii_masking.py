"""PII masking utilities for API responses (spec §SVC-01-FR-002).

Role-based masking rules:
  VIEWER          — phone: show last 3 digits only, email: first char + *** + @domain, gov_id: masked
  STEWARD+        — full values
"""

from __future__ import annotations

import copy

# Roles that receive full PII
_FULL_PII_ROLES = frozenset({"STEWARD", "SENIOR_STEWARD", "POLICY_ADMIN", "AUDITOR"})


def mask_phone(phone: str, role: str) -> str:
    if role in _FULL_PII_ROLES or not phone:
        return phone
    visible = phone[-3:] if len(phone) >= 3 else phone
    return "***" + visible


def mask_email(email: str, role: str) -> str:
    if role in _FULL_PII_ROLES or not email:
        return email
    if "@" not in email:
        return email[0] + "***" if email else email
    local, domain = email.split("@", 1)
    masked_local = local[0] + "***" if local else "***"
    return f"{masked_local}@{domain}"


def mask_gov_id(gov_id: str, role: str) -> str:
    if role in _FULL_PII_ROLES or not gov_id:
        return gov_id
    # Show last 3 chars only
    visible = gov_id[-3:] if len(gov_id) >= 3 else gov_id
    return "***" + visible


_PII_FIELD_MASKS = {
    "phone": mask_phone,
    "email": mask_email,
    "government_id": mask_gov_id,
}


def mask_normalized_fields(normalized_fields: dict, role: str) -> dict:
    """Return a shallow-copied dict with PII fields masked for the given role."""
    if role in _FULL_PII_ROLES:
        return normalized_fields
    result = dict(normalized_fields)
    for field, masker in _PII_FIELD_MASKS.items():
        if field in result:
            result[field] = masker(result[field], role)
    # Also mask phones/emails list
    for collection in ("phones", "emails"):
        if collection in result:
            field = "phone" if collection == "phones" else "email"
            masker = _PII_FIELD_MASKS[field]
            result[collection] = [
                {**item, "value": masker(item.get("value", ""), role)}
                for item in result[collection]
            ]
    return result


def mask_record(record: dict, role: str) -> dict:
    """Return a deep-enough copy of a record dict with PII masked."""
    if role in _FULL_PII_ROLES:
        return record
    result = dict(record)
    if "normalized_fields" in result:
        result["normalized_fields"] = mask_normalized_fields(result["normalized_fields"], role)
    if "raw_fields" in result:
        raw = dict(result["raw_fields"])
        for field, masker in _PII_FIELD_MASKS.items():
            if field in raw:
                raw[field] = masker(raw[field], role)
        result["raw_fields"] = raw
    return result


def mask_pair(pair: dict, role: str) -> dict:
    """Mask PII inside a scored pair dict (left_record / right_record if present)."""
    if role in _FULL_PII_ROLES:
        return pair
    result = dict(pair)
    for side in ("left_record", "right_record"):
        if side in result:
            result[side] = mask_record(result[side], role)
    return result
