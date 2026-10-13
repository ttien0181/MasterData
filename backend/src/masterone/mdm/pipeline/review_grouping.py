"""Stable review cohorts from comparison state and policy, never from UI labels.

Groups are derived from field evidence per architecture §13:
  D — at least one CF field (non-hard conflict)
  A — strong anchors, no conflict
  C — missing required anchor, no conflict
"""

from hashlib import sha256


def _derive_group(pair: dict) -> tuple[str, str]:
    from masterone.mdm.pipeline.pair_grouping import classify_result
    # Explicit old versions retain their fallback interpretation. Stored groups
    # are always preferred by classify_pair, regardless of policy version.
    if (pair.get('assessment_version') == 'person-review-assessment-v1' or
            pair.get('policy_version') == 'person-linh-v2-selective' and not pair.get('assessment_version')):
        return _derive_legacy_group(pair)
    return classify_result(pair)


def _derive_legacy_group(pair: dict) -> tuple[str, str]:
    """Re-derive primary_group + subgroup from field_evidence / comparisons (§15.1)."""
    comparisons = pair.get("comparisons", {})
    if not comparisons:
        comparisons = {e["field"]: {"state": e.get("state", "MI")}
                       for e in pair.get("field_evidence", [])}

    states = {f: v["state"] for f, v in comparisons.items()}

    cf_fields = [f for f, s in states.items() if s == "CF"]
    if cf_fields:
        if len(cf_fields) > 1:
            return "D", "D_MULTI"
        cf_map = {
            "phone": "D1_NAME_PHONE_CONFLICT",
            "name":  "D2_NAME_CONFLICT",
            "dob":   "D4_DOB_CONFLICT",
            "email": "D3_IDENTITY_CONFLICT",
            "government_id": "D3_IDENTITY_CONFLICT",
        }
        return "D", cf_map.get(cf_fields[0], "D_MULTI")

    contact_matches = [f for f in ("phone", "email") if states.get(f) in {"EX", "NX"}]
    gov_id_anchor   = states.get("government_id") == "EX"
    name_good       = states.get("name") in {"EX", "NX", "NA", "HS"}
    name_close      = states.get("name") in {"MS", "WS"}
    dob_anchor      = states.get("dob") in {"EX", "CL"}

    # Group A
    if name_good and "phone" in contact_matches:
        return "A", "A1_NAME_PHONE"
    if name_good and "email" in contact_matches:
        return "A", "A2_NAME_EMAIL"
    if ("phone" in contact_matches and dob_anchor
            and states.get("name") in {"EX", "NX", "NA", "HS", "MS"}
            and states.get("name") not in {"CF", "WS"}):
        return "A", "A3_NAME_PHONE_DOB"
    if gov_id_anchor and name_good:
        return "A", "A3_NAME_GOV_ID"

    # Group C
    if "email" in contact_matches and dob_anchor and not name_good:
        return "C", "C1_EMAIL_DOB"
    if gov_id_anchor and not name_good:
        return "C", "C3_GOV_ID_ONLY"
    if contact_matches:
        return "C", "C_MULTI" if len(contact_matches) > 1 else "C_NAME_ANCHOR_MISSING"
    return "C", "C_NAME_ANCHOR_WEAK" if name_close else "C_NAME_ANCHOR_MISSING"


def classify_pair(pair: dict, record_by_id: dict[str, dict]) -> dict | None:
    if pair.get("triage_status") != "REVIEW":
        return None

    # Use stored group if available; otherwise re-derive from evidence (§13)
    if pair.get("primary_group") and pair.get("subgroup"):
        group = pair["primary_group"]
        subgroup = pair["subgroup"]
    else:
        group, subgroup = _derive_group(pair)

    signature = pair.get("signature") or "|".join(
        f"{evidence['field']}:{evidence.get('state', 'NC')}"
        for evidence in pair.get("field_evidence", []))
    return {
        "primary_group": group,
        "subgroup": subgroup,
        "signature_hash": sha256(
            f"{pair.get('policy_version', 'v1')}:{group}:{subgroup}:{signature}".encode()
        ).hexdigest(),
        "reason_codes": [pair.get("triage_reason", "REVIEW_REQUIRED")],
    }
