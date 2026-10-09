"""Hard Constraints — always run before grouping (architecture §11).

Returns NOT_MERGEABLE immediately when any hard constraint fires.
Hard constraints cannot be overridden by score or policy.

§11.1  PERSON vs ORGANIZATION         → HARD_CONFLICT
§11.2  Same-type gov_id VALID conflict → SAME_TYPE_GOV_ID_CONFLICT
§11.3  Steward negative_edge           → STEWARD_NEGATIVE_DECISION
§11.4  Verified identity conflict      → VERIFIED_IDENTITY_CONFLICT
       Fires when both sides have a *verified* phone or email AND they differ.
       Verified flag: normalized_fields["_phone_verified"] or "_email_verified" = True.
       Only CSV sources with explicit verified flags will trigger this; absence = not verified.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HardConstraintViolation:
    constraint: str   # e.g. "PERSON_ORG_MISMATCH"
    reason: str       # human-readable reason code stored on the pair


def check_entity_type(left: dict, right: dict) -> HardConstraintViolation | None:
    """§11.1 — PERSON vs ORGANIZATION."""
    et_l = (left.get("entity_type") or
            left.get("normalized_fields", {}).get("entity_type", "UNKNOWN"))
    et_r = (right.get("entity_type") or
            right.get("normalized_fields", {}).get("entity_type", "UNKNOWN"))
    if et_l != "UNKNOWN" and et_r != "UNKNOWN" and et_l != et_r:
        return HardConstraintViolation("PERSON_ORG_MISMATCH", "HARD_CONFLICT")
    return None


def check_gov_id(left_nf: dict, right_nf: dict) -> HardConstraintViolation | None:
    """§11.2 — Same-type gov_id VALID conflict."""
    a = left_nf.get("government_id", "")
    b = right_nf.get("government_id", "")
    if not a or not b:
        return None
    qa = left_nf.get("_field_quality", {}).get("government_id", "MISSING")
    qb = right_nf.get("_field_quality", {}).get("government_id", "MISSING")
    if qa != "VALID" or qb != "VALID":
        return None
    type_a = left_nf.get("government_id_type", "UNKNOWN")
    type_b = right_nf.get("government_id_type", "UNKNOWN")
    if type_a == "UNKNOWN" or type_b == "UNKNOWN" or type_a != type_b:
        return None
    if a != b:
        return HardConstraintViolation("SAME_TYPE_GOV_ID_CONFLICT", "HARD_CONFLICT")
    return None


def check_negative_edge(left_id: str, right_id: str,
                         negative_pairs: frozenset[tuple[str, str]]) -> HardConstraintViolation | None:
    """§11.3 — Steward negative decision (negative_edge)."""
    key = tuple(sorted((left_id, right_id)))
    if key in negative_pairs:
        return HardConstraintViolation("STEWARD_NEGATIVE_DECISION", "NON_MATCH")
    return None


def check_verified_identity(left_nf: dict, right_nf: dict) -> HardConstraintViolation | None:
    """§11.4 — Verified phone or email conflict.

    Fires only when BOTH sides have an independently verified contact of the same type
    and those verified contacts differ. Absence of a verified flag = not verified → no fire.
    """
    for field, flag_key in (("phone", "_phone_verified"), ("email", "_email_verified")):
        if left_nf.get(flag_key) and right_nf.get(flag_key):
            val_l = left_nf.get(field, "")
            val_r = right_nf.get(field, "")
            if val_l and val_r and val_l != val_r:
                return HardConstraintViolation(
                    "VERIFIED_IDENTITY_CONFLICT",
                    f"HARD_VERIFIED_{field.upper()}",
                )
    return None


def check_all(left: dict, right: dict,
              negative_pairs: frozenset[tuple[str, str]] | None = None,
              ) -> HardConstraintViolation | None:
    """Run all hard constraints in priority order. Returns first violation or None."""
    # §11.3 steward decision is checked first (most authoritative)
    if negative_pairs:
        v = check_negative_edge(left["id"], right["id"], negative_pairs)
        if v:
            return v
    # §11.1 entity type
    v = check_entity_type(left, right)
    if v:
        return v
    # §11.2 gov_id
    v = check_gov_id(left.get("normalized_fields", {}), right.get("normalized_fields", {}))
    if v:
        return v
    # §11.4 verified identity
    v = check_verified_identity(left.get("normalized_fields", {}), right.get("normalized_fields", {}))
    if v:
        return v
    return None
