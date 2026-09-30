from difflib import SequenceMatcher


def _similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right, autojunk=False).ratio()


def score_pair(left: dict, right: dict, blocking_reasons: set[str]) -> dict:
    a, b = left["normalized_fields"], right["normalized_fields"]
    similarities = {
        "name": _similarity(a.get("name", ""), b.get("name", "")),
        "phone": float(bool(a.get("phone")) and a.get("phone") == b.get("phone")),
        "email": float(bool(a.get("email")) and a.get("email") == b.get("email")),
        "address": _similarity(a.get("address", ""), b.get("address", "")),
    }
    score = (0.35 * similarities["name"] + 0.30 * similarities["phone"]
             + 0.30 * similarities["email"] + 0.05 * similarities["address"])
    exact_contact = similarities["phone"] == 1 or similarities["email"] == 1
    name_conflict = similarities["name"] < 0.48 and exact_contact
    if name_conflict:
        decision = "NON_MATCH"
        reason = "SHARED_CONTACT_NAME_CONFLICT"
    elif exact_contact and similarities["name"] >= 0.68:
        decision = "MATCH_CANDIDATE"
        reason = "EXACT_CONTACT_AND_NAME"
    elif score >= 0.76 and similarities["name"] >= 0.88:
        decision = "MATCH_CANDIDATE"
        reason = "MULTI_FIELD_SUPPORT"
    elif score >= 0.38 or exact_contact:
        decision = "REVIEW"
        reason = "AMBIGUOUS_FIELD_EVIDENCE"
    else:
        decision = "NON_MATCH"
        reason = "WEAK_FIELD_EVIDENCE"
    evidence = [{"field": field, "similarity": round(value, 4), "left": a.get(field, ""), "right": b.get(field, "")}
                for field, value in similarities.items()]
    return {"score": round(score, 6), "field_evidence": evidence, "decision": decision,
            "reason": reason, "blocking_reasons": sorted(blocking_reasons)}
