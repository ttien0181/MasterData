"""Deterministic entity comparison with Unified Comparison States (architecture v3.1 §8).

State contract  (§8):  EX NX NA HS MS WS CL CF DF MI IV SH NC
Conflict taxonomy (§8.1):
  CF = exclusion evidence  → Group D
  DF = different but non-exclusive → NOT Group D
Hard constraints (§11): delegated to hard_constraints module
Evidence floor   (§12): delegated to evidence_floor module
Semantic subgroups (§15.1): A1/A2/A3, C1/C2/C3/C_MULTI, D1/D2/D3/D4/D_MULTI
Policy engine (§17): per-subgroup config gates auto-merge
"""

from collections import Counter
from difflib import SequenceMatcher
from math import log2
from unicodedata import normalize

from masterone.mdm.pipeline.evidence_floor import evaluate as _floor_evaluate
from masterone.mdm.pipeline.hard_constraints import check_all as _hard_check
from masterone.mdm.pipeline.policy_engine import apply_policy
from masterone.mdm.pipeline.standardization import parse_dob

FIELDS = ("name", "phone", "email", "address")
OPTIONAL_FIELDS = ("dob", "government_id")
POLICY_VERSION = "person-linh-v2-selective"

# Signature field abbreviations (architecture §15.2)
_SIG_ABBREV = {"name": "N", "phone": "P", "email": "E",
               "dob": "DOB", "address": "A", "government_id": "G"}


def _similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right, autojunk=False).ratio()


def _dob_state(nf_left: dict, nf_right: dict) -> tuple[str, float, str]:
    """Compute DOB comparison state per architecture §8.1.

    Returns (state, similarity, provenance).
    """
    a = nf_left.get("dob", "")
    b = nf_right.get("dob", "")
    qa = nf_left.get("_field_quality", {}).get("dob", "MISSING")
    qb = nf_right.get("_field_quality", {}).get("dob", "MISSING")

    if not a or not b or qa == "MISSING" or qb == "MISSING":
        return "MI", 0.0, "RULE"
    if qa == "INVALID" or qb == "INVALID":
        return "IV", 0.0, "RULE"
    if qa in ("PLACEHOLDER", "SH") or qb in ("PLACEHOLDER", "SH"):
        return "SH", 0.0, "RULE"

    # Both non-empty and non-invalid — parse structured parts
    pa = parse_dob(a)
    pb = parse_dob(b)

    # Exact match
    if pa["canonical"] == pb["canonical"]:
        return "EX", 1.0, "RULE"

    # Year-only vs full date — same year → not CF (architecture §8.1 note)
    if pa["is_year_only"] or pb["is_year_only"]:
        if pa["year"] and pb["year"] and pa["year"] == pb["year"]:
            return "CL", 0.9, "RULE"
        if pa["year"] and pb["year"] and pa["year"] != pb["year"]:
            return "CF", 0.0, "RULE"
        return "MI", 0.0, "RULE"

    # Both full dates: check dd/mm swap (CL)
    if (pa["year"] == pb["year"] and pa["month"] == pb["day"] and pa["day"] == pb["month"]
            and pa["month"] != pa["day"]):
        return "CL", 0.8, "RULE"

    # Same year but different day/month → CL (close, but not definitively CF)
    if pa["year"] == pb["year"]:
        return "CL", 0.5, "RULE"

    # Different years → CF (both full-date VALID, different year is exclusion evidence)
    return "CF", 0.0, "RULE"


def _gov_id_state(nf_left: dict, nf_right: dict) -> tuple[str, float, str]:
    """Compute government_id comparison state per architecture §8.1 and §11.2.

    Returns (state, similarity, provenance).
    Hard conflicts are NOT returned here — caller checks for HARD_CONFLICT separately.
    """
    a = nf_left.get("government_id", "")
    b = nf_right.get("government_id", "")
    qa = nf_left.get("_field_quality", {}).get("government_id", "MISSING")
    qb = nf_right.get("_field_quality", {}).get("government_id", "MISSING")

    if not a or not b or qa == "MISSING" or qb == "MISSING":
        return "MI", 0.0, "RULE"
    if qa == "INVALID" or qb == "INVALID":
        return "IV", 0.0, "RULE"

    type_a = nf_left.get("government_id_type", "UNKNOWN")
    type_b = nf_right.get("government_id_type", "UNKNOWN")

    # Different ID types → NC (§11.2: CMND_9 vs CCCD_12 → NC)
    if type_a != type_b or type_a == "UNKNOWN":
        return "NC", 0.0, "RULE"

    # Same type, both VALID
    if a == b:
        return "EX", 1.0, "RULE"

    # Same type, both VALID, different value → HARD_CONFLICT signal
    # Caller will detect this and set decision=NON_MATCH. Return a sentinel.
    return "_HARD_CONFLICT", 0.0, "RULE"


def _name_state(left: dict, right: dict) -> tuple[str, float, list[str]]:
    a = left["normalized_fields"].get("name", "")
    b = right["normalized_fields"].get("name", "")
    raw_a = (left.get("raw_fields", {}).get("name") or "").strip()
    raw_b = (right.get("raw_fields", {}).get("name") or "").strip()

    if not a or not b:
        return "MI", 0.0, []
    if a == b:
        if normalize("NFC", raw_a) == normalize("NFC", raw_b):
            return "EX", 1.0, []
        unicode_a = left["normalized_fields"].get("name_unicode")
        unicode_b = right["normalized_fields"].get("name_unicode")
        if unicode_a == unicode_b:
            return "NX", 1.0, ["NAME_FORMAT_EQUIVALENT"]
        if unicode_a and unicode_b and any(ord(c) > 127 for c in raw_a) and any(ord(c) > 127 for c in raw_b):
            return "MS", _similarity(unicode_a, unicode_b), ["NAME_DIACRITIC_CONFLICT"]
        return "NA", 1.0, ["NAME_ASCII_EQUIVALENT"]
    if sorted(a.split()) == sorted(b.split()):
        return "NX", 1.0, ["TOKEN_ORDER_VARIATION"]

    similarity = _similarity(a, b)
    if similarity >= 0.88:
        return "HS", similarity, []
    if similarity >= 0.68:
        return "MS", similarity, []
    if similarity >= 0.48:
        return "WS", similarity, []
    # Low similarity name: CF only if both non-empty and very different (exclusion evidence)
    return "CF", similarity, []


def _contact_state(field: str, left: dict, right: dict, frequencies: dict) -> tuple[str, float, list[str]]:
    """Phone or email comparison state.

    Per §8.1: phone/email are CF only when BOTH sides have primary/current flags AND
    the source is single_valued_reliable. CSV data never has these flags → always DF.
    """
    a = left["normalized_fields"].get(field, "")
    b = right["normalized_fields"].get(field, "")
    if not a or not b:
        return "MI", 0.0, []

    quality_a = left["normalized_fields"].get("_field_quality", {}).get(field)
    quality_b = right["normalized_fields"].get("_field_quality", {}).get(field)

    if "INVALID" in {quality_a, quality_b}:
        return "IV", 0.0, []
    if {quality_a, quality_b} & {"SHARED", "PLACEHOLDER"}:
        return "SH", 0.0, []

    flags = set(left.get("quality_flags", [])) | set(right.get("quality_flags", []))
    if field == "email" and "INVALID_EMAIL_FORMAT" in flags:
        return "IV", 0.0, []
    if field == "phone" and "UNUSUAL_PHONE_LENGTH" in flags:
        return "IV", 0.0, []

    # High-frequency shared value
    if a == b and frequencies[field][a] > 12:
        return "SH", 1.0, []

    if a == b:
        raw_a = (left.get("raw_fields", {}).get(field) or "").strip()
        raw_b = (right.get("raw_fields", {}).get(field) or "").strip()
        if normalize("NFC", raw_a) == normalize("NFC", raw_b):
            return "EX", 1.0, []
        tag = {"phone": "PHONE_FORMAT_EQUIVALENT", "email": "EMAIL_CASE_EQUIVALENT"}[field]
        return "NX", 1.0, [tag]

    # Different values: per §8.1 CSV never has primary/current flags → DF (never CF)
    similarity = _similarity(a, b)
    return "DF", similarity, []


def _address_state(left: dict, right: dict) -> tuple[str, float, list[str]]:
    """Address comparison state. Address is NEVER CF (§8.1)."""
    a = left["normalized_fields"].get("address", "")
    b = right["normalized_fields"].get("address", "")
    if not a or not b:
        return "MI", 0.0, []

    # Ward overlap check
    ap_l = left["normalized_fields"].get("address_parts", {})
    ap_r = right["normalized_fields"].get("address_parts", {})
    ward_l = ap_l.get("ward_ids") or frozenset()
    ward_r = ap_r.get("ward_ids") or frozenset()
    prov_l = ap_l.get("province", "")
    prov_r = ap_r.get("province", "")

    # Different resolved provinces → cap at WS / DF (never HS/MS)
    if prov_l and prov_r and prov_l != prov_r:
        similarity = _similarity(a, b)
        return ("WS" if similarity >= 0.35 else "DF"), similarity, []

    # Ward overlap → boost
    if ward_l and ward_r:
        if ward_l & ward_r:
            # Same ward — compare full string for granularity
            similarity = _similarity(a, b)
            if similarity >= 0.88:
                return "HS", similarity, ["ADDRESS_ADMIN_MAPPING"]
            return "MS", max(similarity, 0.65), ["ADDRESS_ADMIN_MAPPING"]

    if a == b:
        raw_a = (left.get("raw_fields", {}).get("address") or "").strip()
        raw_b = (right.get("raw_fields", {}).get("address") or "").strip()
        if normalize("NFC", raw_a) == normalize("NFC", raw_b):
            return "EX", 1.0, []
        return "NX", 1.0, ["ADDRESS_ABBREVIATION"]

    similarity = _similarity(a, b)
    if similarity >= 0.88:
        return "HS", similarity, []
    if similarity >= 0.68:
        return "MS", similarity, []
    if similarity >= 0.48:
        return "WS", similarity, []
    # Address is never CF — low similarity is DF
    return "DF", similarity, []


def _assign_d_subgroup(cf_fields: list[str], states: dict[str, str],
                       contact_matches: list[str]) -> tuple[str, str]:
    """Assign Group D semantic subgroup per §15.1.

    D1 = name similar + phone CF
    D2 = phone same   + name CF  (or any name CF with contact anchor)
    D3 = address same + identity CF (email/gov_id CF while address HS/EX)
    D4 = DOB CF + other strong evidence
    D_MULTI = multiple CF fields
    """
    if len(cf_fields) > 1:
        return "D", "D_MULTI"
    cf = cf_fields[0]
    if cf == "phone":
        # D1: name close/present + phone CF
        return "D", "D1_NAME_PHONE_CONFLICT"
    if cf == "name":
        # D2: phone same + name CF
        return "D", "D2_NAME_CONFLICT"
    if cf == "dob":
        return "D", "D4_DOB_CONFLICT"
    if cf in ("email", "government_id"):
        # D3: identity anchor CF while address matches
        return "D", "D3_IDENTITY_CONFLICT"
    return "D", "D_MULTI"


def _assign_ac_subgroup(
    states: dict[str, str],
    contact_matches: list[str],
    gov_id_anchor: bool,
    name_good: bool,
    name_close: bool,
    dob_anchor: bool,
) -> tuple[str | None, str | None]:
    """Assign Group A or C semantic subgroup per §15.1.

    A1 = name HS+ + phone EX/NX
    A2 = name HS+ + email EX/NX
    A3 = phone EX/NX + DOB EX + name compatible (§18)
    C1 = email EX/NX + DOB EX/CL  (missing phone/address anchor)
    C2 = phone EX/NX + name HS+   (missing email/address)
    C3 = identity anchors strong but no name/address
    C_MULTI = multiple weak signals, no dominant pattern
    """
    if not contact_matches and not gov_id_anchor:
        return None, None

    # ── Group A ──────────────────────────────────────────────────────────────
    if name_good and "phone" in contact_matches:
        return "A", "A1_NAME_PHONE"
    if name_good and "email" in contact_matches:
        return "A", "A2_NAME_EMAIL"
    # A3: phone + DOB exact + name at least close (§18: name not WS/CF)
    if ("phone" in contact_matches and dob_anchor
            and states.get("name") in {"EX", "NX", "NA", "HS", "MS"}
            and states.get("name") not in {"CF", "WS"}):
        return "A", "A3_NAME_PHONE_DOB"
    if name_good and gov_id_anchor:
        return "A", "A3_NAME_GOV_ID"

    # ── Group C ──────────────────────────────────────────────────────────────
    # C1: email + DOB strong, missing phone / name anchor
    if "email" in contact_matches and dob_anchor and not name_good:
        return "C", "C1_EMAIL_DOB"
    # C2: phone + name strong, missing email / address
    if "phone" in contact_matches and name_good:
        # This would be A1 if name_good — but if we reach here name was not HS+
        # Actually this is unreachable after the A checks above; kept for symmetry
        return "C", "C2_PHONE_NAME"
    # C3: gov_id anchor without name confirmation
    if gov_id_anchor and not name_good:
        return "C", "C3_GOV_ID_ONLY"
    # C_MULTI: contact matches but name missing/weak
    if contact_matches:
        return "C", "C_MULTI" if len(contact_matches) > 1 else "C_NAME_ANCHOR_MISSING"
    # Fallback
    subgroup = "C_NAME_ANCHOR_WEAK" if name_close else "C_NAME_ANCHOR_MISSING"
    return "C", subgroup


def value_frequencies(records: list[dict]) -> dict[str, Counter]:
    return {field: Counter(record["normalized_fields"].get(field, "") for record in records
                           if record["normalized_fields"].get(field, ""))
            for field in ("phone", "email")}


def score_pair(left: dict, right: dict, blocking_reasons: set[str],
               frequencies: dict | None = None,
               negative_pairs: frozenset | None = None) -> dict:
    frequencies = frequencies or value_frequencies([left, right])

    # ── Hard constraints (§11) — always before scoring ────────────────────────
    violation = _hard_check(left, right, negative_pairs)
    if violation:
        return _non_match(violation.reason, violation.constraint, blocking_reasons)

    # ── gov_id comparison state (used in evidence; hard conflict handled above) ─
    gov_state, gov_sim, gov_prov = _gov_id_state(
        left["normalized_fields"], right["normalized_fields"])
    # _HARD_CONFLICT sentinel means hard_constraints already caught this; skip silently
    if gov_state == "_HARD_CONFLICT":
        return _non_match("HARD_CONFLICT", "SAME_TYPE_GOV_ID_CONFLICT", blocking_reasons)

    # ── Per-field comparison ──────────────────────────────────────────────────
    evidence: list[dict] = []

    # name
    ns, n_sim, n_tags = _name_state(left, right)
    evidence.append({"field": "name", "state": ns, "provenance": "RULE",
                     "similarity": round(n_sim, 4), "normalization_tags": n_tags,
                     "left": left["normalized_fields"].get("name", ""),
                     "right": right["normalized_fields"].get("name", "")})

    # phone
    ps, p_sim, p_tags = _contact_state("phone", left, right, frequencies)
    evidence.append({"field": "phone", "state": ps, "provenance": "RULE",
                     "similarity": round(p_sim, 4), "normalization_tags": p_tags,
                     "left": left["normalized_fields"].get("phone", ""),
                     "right": right["normalized_fields"].get("phone", "")})

    # email
    es, e_sim, e_tags = _contact_state("email", left, right, frequencies)
    evidence.append({"field": "email", "state": es, "provenance": "RULE",
                     "similarity": round(e_sim, 4), "normalization_tags": e_tags,
                     "left": left["normalized_fields"].get("email", ""),
                     "right": right["normalized_fields"].get("email", "")})

    # dob
    dob_s, dob_sim, dob_prov = _dob_state(left["normalized_fields"], right["normalized_fields"])
    evidence.append({"field": "dob", "state": dob_s, "provenance": dob_prov,
                     "similarity": round(dob_sim, 4), "normalization_tags": [],
                     "left": left["normalized_fields"].get("dob", ""),
                     "right": right["normalized_fields"].get("dob", "")})

    # address
    as_, a_sim, a_tags = _address_state(left, right)
    evidence.append({"field": "address", "state": as_, "provenance": "RULE",
                     "similarity": round(a_sim, 4), "normalization_tags": a_tags,
                     "left": left["normalized_fields"].get("address", ""),
                     "right": right["normalized_fields"].get("address", "")})

    # government_id
    evidence.append({"field": "government_id", "state": gov_state, "provenance": gov_prov,
                     "similarity": round(gov_sim, 4), "normalization_tags": [],
                     "left": left["normalized_fields"].get("government_id", ""),
                     "right": right["normalized_fields"].get("government_id", "")})

    states = {item["field"]: item["state"] for item in evidence}

    # comparisons dict (architecture §15.2 — compact view used by policy engine)
    comparisons = {item["field"]: {"state": item["state"], "provenance": item["provenance"]}
                   for item in evidence}

    # Signature (§15.2): N:HS|P:CF|E:EX|DOB:EX|A:HS|G:NC
    sig_fields = ["name", "phone", "email", "dob", "address", "government_id"]
    signature = "|".join(f"{_SIG_ABBREV[f]}:{states[f]}" for f in sig_fields)
    norm_tags = sorted({tag for item in evidence for tag in item["normalization_tags"]})

    # ── Contact anchors and weight — computed before floor check ─────────────
    contact_matches = [f for f in ("phone", "email") if states[f] in {"EX", "NX"}]
    gov_id_anchor = states.get("government_id") == "EX"
    dob_anchor = states.get("dob") in {"EX", "CL"}
    name_good = states["name"] in {"EX", "NX", "NA", "HS"}
    name_close = states["name"] in {"MS", "WS"}

    # Frequency-adjusted weight
    weight = 0.0
    for field in contact_matches:
        value = left["normalized_fields"].get(field, "")
        weight += max(1.0, 5.0 - log2(max(1, frequencies[field].get(value, 1))))
    weight += {"EX": 2.0, "NX": 1.9, "NA": 1.8, "HS": 1.4,
               "MS": 0.6, "WS": 0.2, "CF": -2.0}.get(states["name"], 0.0)
    weight += {"EX": 0.8, "NX": 0.7, "HS": 0.5, "MS": 0.2}.get(states["address"], 0.0)
    if states.get("dob") == "EX":
        weight += 1.5
    elif states.get("dob") == "CL":
        weight += 0.5
    if gov_id_anchor:
        weight += 3.0

    # ── Evidence floor (§12) — before grouping ───────────────────────────────
    floor = _floor_evaluate(comparisons, weight)
    if not floor.passes:
        return {
            "score": round(weight, 6),
            "match_weight": round(weight, 6),
            "field_evidence": evidence,
            "comparisons": comparisons,
            "decision": "INSUFFICIENT_EVIDENCE",
            "reason": floor.reason,
            "primary_group": None,
            "subgroup": None,
            "matched_subgroups": [],
            "decision_subgroup": None,
            "normalization_tags": norm_tags,
            "signature": signature,
            "policy_id": None,
            "policy_version": POLICY_VERSION,
            "blocking_reasons": sorted(blocking_reasons),
            "triage_status": "INSUFFICIENT_EVIDENCE",
            "triage_reason": floor.reason,
        }

    nf_left  = left["normalized_fields"]
    nf_right = right["normalized_fields"]

    # ── Semantic subgroup (§15.1) ─────────────────────────────────────────────
    cf_fields = [f for f, s in states.items() if s == "CF"]
    has_conflict = bool(cf_fields)

    if has_conflict:
        group, subgroup = _assign_d_subgroup(cf_fields, states, contact_matches)
    else:
        group, subgroup = _assign_ac_subgroup(states, contact_matches, gov_id_anchor,
                                              name_good, name_close, dob_anchor)

    if group is None:
        # no identity anchor even after floor — rare but possible
        return {
            "score": round(weight, 6), "match_weight": round(weight, 6),
            "field_evidence": evidence, "comparisons": comparisons,
            "decision": "INSUFFICIENT_EVIDENCE", "reason": "NO_VALID_IDENTITY_ANCHOR",
            "primary_group": None, "subgroup": None, "matched_subgroups": [],
            "decision_subgroup": None,
            "normalization_tags": norm_tags, "signature": signature,
            "policy_id": None, "policy_version": POLICY_VERSION,
            "blocking_reasons": sorted(blocking_reasons),
            "triage_status": "INSUFFICIENT_EVIDENCE", "triage_reason": "NO_VALID_IDENTITY_ANCHOR",
        }

    # ── Policy engine (§17) — gates MATCH_CANDIDATE ─────────────────────────
    pol_result = apply_policy(group, comparisons, weight, nf_left, nf_right)

    matched_subgroups  = pol_result.matched_subgroups
    decision_subgroup  = pol_result.decision_subgroup or subgroup

    if pol_result.eligible:
        decision = "MATCH_CANDIDATE"
        reason   = pol_result.policy_id
        policy_id = pol_result.policy_id
        triage_status = "AUTO_MERGE"
        triage_reason = pol_result.reason
    else:
        # Group D is always REVIEW at MVP (§17.2); Group C always REVIEW
        decision = "REVIEW"
        reason   = pol_result.reason or ("NON_HARD_CONFLICT" if group == "D"
                                         else "MISSING_REQUIRED_NAME_ANCHOR")
        policy_id = None
        triage_status = "REVIEW"
        triage_reason = reason

    return {
        "score": round(weight, 6),
        "match_weight": round(weight, 6),
        "field_evidence": evidence,
        "comparisons": comparisons,
        "decision": decision,
        "reason": reason,
        "primary_group": group,
        "subgroup": subgroup,
        "matched_subgroups": matched_subgroups,
        "decision_subgroup": decision_subgroup,
        "normalization_tags": norm_tags,
        "signature": signature,
        "policy_id": policy_id,
        "policy_version": POLICY_VERSION,
        "blocking_reasons": sorted(blocking_reasons),
        "triage_status": triage_status,
        "triage_reason": triage_reason,
    }


def _non_match(decision_type: str, reason: str, blocking_reasons: set[str]) -> dict:
    """Return a hard NON_MATCH result (no field scoring needed)."""
    return {
        "score": 0.0,
        "match_weight": 0.0,
        "field_evidence": [],
        "comparisons": {},
        "decision": "NON_MATCH",
        "reason": reason,
        "primary_group": None,
        "subgroup": None,
        "matched_subgroups": [],
        "decision_subgroup": None,
        "normalization_tags": [],
        "signature": "",
        "policy_id": None,
        "policy_version": POLICY_VERSION,
        "blocking_reasons": sorted(blocking_reasons),
        "triage_status": "NON_MATCH",
        "triage_reason": reason,
    }
