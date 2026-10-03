"""Pair comparison với Unified Comparison States theo kiến trúc v3.1.

Mục 8 — Unified Comparison State Contract
Mục 8.1 — Conflict Taxonomy (CF vs DF)
Mục 9 — Comparison Provenance
Mục 10 — Pair Scoring
Mục 11 — Hard Constraints
"""

# Version này được nhúng vào mỗi comparison để audit có thể tái lập kết quả
# (mục 8.1 quy tắc 7, mục P0 #9).  Tăng khi thay đổi logic CF/DF/hard-constraint.
CONFLICT_RULE_VERSION = "conflict-rules-v1"

import re
from difflib import SequenceMatcher


# ─── Unified Comparison States (mục 8) ───────────────────────────────────────
# EX  EXACT
# NX  NORMALIZED_EQUIVALENT
# NA  NORMALIZED_ASCII_EQUIVALENT
# HS  HIGH_SIMILAR   (>= 0.85)
# MS  MEDIUM_SIMILAR (>= 0.65)
# WS  WEAK_SIMILAR   (>= 0.40)
# CL  CLOSE
# CF  CONFLICT — có bằng chứng loại trừ
# DF  DIFFERENT_NON_EXCLUSIVE — khác nhưng không loại trừ
# MI  MISSING
# IV  INVALID
# SH  SHARED / SUSPICIOUS
# NC  NOT_COMPARABLE

# Match weights dùng để tính match_weight (thay thế similarity đơn giản)
_STATE_WEIGHT: dict[str, float] = {
    "EX": 4.0, "NX": 3.0, "NA": 2.0,
    "HS": 1.5, "MS": 0.8, "WS": 0.2,
    "CL": 0.5,
    "CF": -3.0, "DF": 0.0,
    "MI": 0.0, "IV": 0.0, "SH": 0.0, "NC": 0.0,
}

# Score compat (0–1) cho field_evidence — dùng cho UI hiển thị
_STATE_SCORE: dict[str, float] = {
    "EX": 1.0, "NX": 0.95, "NA": 0.85,
    "HS": 0.80, "MS": 0.65, "WS": 0.40,
    "CL": 0.50,
    "CF": 0.10, "DF": 0.30,
    "MI": 0.0, "IV": 0.0, "SH": 0.0, "NC": 0.0,
}


def _sim(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


def _quality(nf: dict, field: str) -> str:
    return nf.get("_field_quality", {}).get(field, "VALID")


# ─── Per-field comparators ────────────────────────────────────────────────────

def _compare_name(a: dict, b: dict) -> tuple[str, str]:
    """Trả về (state, provenance).

    Quy tắc mục 5.1:
    - Cả hai có dấu, unicode form giống → EX
    - ASCII form giống nhưng unicode khác → NA (không phải NX)
    - Similarity cao/vừa/thấp → HS/MS/WS
    - Surname khác rõ ràng (cả hai valid, không giải thích được bởi ASCII) → CF
    """
    qa, qb = _quality(a, "name"), _quality(b, "name")
    if qa == "MISSING" or qb == "MISSING":
        return "MI", "RULE"

    a_uni = a.get("name", "")
    b_uni = b.get("name", "")
    a_asc = a.get("name_ascii", "")
    b_asc = b.get("name_ascii", "")

    if a_uni == b_uni:
        return "EX", "RULE"

    # ASCII form giống nhưng unicode khác — một bên bỏ dấu
    # Không được nâng lên NX theo mục 5.1
    if a_asc and b_asc and a_asc == b_asc:
        return "NA", "RULE"

    # Kiểm tra surname (họ) — mục 5.1
    a_surname = (a.get("name_parts") or {}).get("surname", "")
    b_surname = (b.get("name_parts") or {}).get("surname", "")
    a_surname_asc = _sim(a_surname, b_surname)
    surname_differs = bool(a_surname and b_surname and a_surname_asc < 0.5)

    # Similarity toàn chuỗi (unicode)
    sim_uni = _sim(a_uni, b_uni)
    sim_asc = _sim(a_asc, b_asc)
    sim = max(sim_uni, sim_asc)

    # Surname khác rõ ràng → CF (mục 8.1 — name CF)
    if surname_differs and sim < 0.6:
        return "CF", "RULE"

    if sim >= 0.85:
        return "HS", "RULE"
    if sim >= 0.65:
        return "MS", "RULE"
    if sim >= 0.40:
        return "WS", "RULE"
    # Khác nhau và không đạt ngưỡng nào → CF nếu surname khác, else DF không apply cho name
    if surname_differs:
        return "CF", "RULE"
    return "WS", "RULE"


def _compare_phone(a: dict, b: dict) -> tuple[str, str]:
    """Theo mục 8.1 — phone CF chỉ khi đủ điều kiện primary/current.

    MVP: không có cờ primary/current trong schema hiện tại,
    nên phone khác nhau (cả hai VALID) → DF (không CF).
    Phone CF được để lại cho khi có cờ nguồn single_valued_reliable.
    """
    qa, qb = _quality(a, "phone"), _quality(b, "phone")
    if qa == "MISSING" and qb == "MISSING":
        return "MI", "RULE"
    if qa == "MISSING" or qb == "MISSING":
        return "MI", "RULE"
    if qa == "INVALID" or qb == "INVALID":
        return "IV", "RULE"
    if qa in ("SHARED", "PLACEHOLDER") or qb in ("SHARED", "PLACEHOLDER"):
        return "SH", "RULE"

    av, bv = a.get("phone", ""), b.get("phone", "")
    if av == bv:
        return "EX", "RULE"
    # Không có cờ primary/current → DF theo mục 8.1
    return "DF", "RULE"


def _compare_email(a: dict, b: dict) -> tuple[str, str]:
    """Tương tự phone — email khác nhau (VALID) → DF trong MVP."""
    qa, qb = _quality(a, "email"), _quality(b, "email")
    if qa == "MISSING" and qb == "MISSING":
        return "MI", "RULE"
    if qa == "MISSING" or qb == "MISSING":
        return "MI", "RULE"
    if qa == "INVALID" or qb == "INVALID":
        return "IV", "RULE"
    if qa in ("SHARED", "PLACEHOLDER") or qb in ("SHARED", "PLACEHOLDER"):
        return "SH", "RULE"

    av, bv = a.get("email", ""), b.get("email", "")
    if av == bv:
        return "EX", "RULE"
    # Gmail case/dot/tag variants → NX  (chỉ kiểm tra lowercase match đã đủ)
    # Khác nhau → DF
    return "DF", "RULE"


def _compare_address(a: dict, b: dict) -> tuple[str, str]:
    """Địa chỉ không bao giờ CF (mục 8.1). Dùng ward_ids + province + fuzzy.

    Signals (theo kế hoạch mục 5.4):
      ward=MATCH    → boost; ward=DIFFER → cap ≤ WS
      ward=AMBIGUOUS (needs_hitl) → bỏ qua ward signal, dùng fuzzy + province
      province=DIFFER → cap ≤ WS (không đè MATCH của ward cùng tỉnh)
    """
    qa, qb = _quality(a, "address"), _quality(b, "address")
    if qa == "MISSING" and qb == "MISSING":
        return "MI", "RULE"
    if qa == "MISSING" or qb == "MISSING":
        return "MI", "RULE"

    av, bv = a.get("address", ""), b.get("address", "")
    if av == bv:
        return "NX", "RULE"

    ap = a.get("address_parts") or {}
    bp = b.get("address_parts") or {}

    # ── Province signal ──────────────────────────────────────────────────────
    pa, pb_prov = ap.get("province", ""), bp.get("province", "")
    province_conflict = bool(pa and pb_prov and pa != pb_prov)

    # ── Ward signal ──────────────────────────────────────────────────────────
    wa: frozenset = ap.get("ward_ids") or frozenset()
    wb: frozenset = bp.get("ward_ids") or frozenset()
    wa_status = ap.get("ward_status", "unknown")
    wb_status = bp.get("ward_status", "unknown")

    # Bỏ qua ward signal nếu một trong hai bên là ambiguous (cần HITL)
    hitl_a = ap.get("needs_hitl", False)
    hitl_b = bp.get("needs_hitl", False)
    use_ward = bool(wa and wb and not hitl_a and not hitl_b
                    and wa_status != "unknown" and wb_status != "unknown")

    ward_match = use_ward and bool(wa & wb)
    ward_differ = use_ward and not ward_match

    sim = _sim(av, bv)

    # ── Kết hợp signals ──────────────────────────────────────────────────────
    if ward_match and not province_conflict:
        # Ward khớp rõ, cùng tỉnh → nâng lên ít nhất MS
        if sim >= 0.85:
            return "HS", "RULE"
        return "MS", "RULE"

    if ward_match and province_conflict:
        # Ward khớp nhưng tỉnh khác (ví dụ phường đổi tỉnh sau sáp nhập) → MS
        return "MS", "RULE"

    if ward_differ or province_conflict:
        # Ward hoặc tỉnh rõ ràng khác → cap ≤ WS
        if sim >= 0.40:
            return "WS", "FUZZY"
        return "DF", "RULE"

    # ward unknown — dùng fuzzy thuần
    if sim >= 0.85:
        return "HS", "FUZZY"
    if sim >= 0.65:
        return "MS", "FUZZY"
    if sim >= 0.40:
        return "WS", "FUZZY"
    return "DF", "RULE"


def _compare_dob(a: dict, b: dict) -> tuple[str, str]:
    """DOB comparison theo mục 8.1 và 5.5.

    Dùng dob_parts (canonical YYYY-MM-DD) để so sánh chính xác.

    Kết quả:
      EX  — canonical giống hệt (kể cả year-only vs year-only)
      CL  — dd/mm hoán đổi, hoặc một bên year-only cùng năm
      CF  — năm khác, hoặc full date khác rõ (không phải hoán đổi)
      MI  — missing
      IV  — invalid / placeholder
    """
    qa, qb = _quality(a, "dob"), _quality(b, "dob")
    if qa == "MISSING" and qb == "MISSING":
        return "MI", "RULE"
    if qa == "MISSING" or qb == "MISSING":
        return "MI", "RULE"
    if qa in ("INVALID", "PLACEHOLDER") or qb in ("INVALID", "PLACEHOLDER"):
        return "IV", "RULE"

    # Dùng dob_parts nếu có (sau khi qua parse_dob)
    pa = a.get("dob_parts") or {}
    pb = b.get("dob_parts") or {}
    can_a = pa.get("canonical") or a.get("dob", "")
    can_b = pb.get("canonical") or b.get("dob", "")
    yr_a = pa.get("year") or (re.search(r"\d{4}", can_a) or type("", (), {"group": lambda s: ""})()).group()
    yr_b = pb.get("year") or (re.search(r"\d{4}", can_b) or type("", (), {"group": lambda s: ""})()).group()
    only_a = pa.get("is_year_only", False)
    only_b = pb.get("is_year_only", False)
    amb_a = pa.get("is_ambiguous", False)
    amb_b = pb.get("is_ambiguous", False)

    if can_a == can_b:
        return "EX", "RULE"

    # Năm khác → CF ngay
    if yr_a and yr_b and yr_a != yr_b:
        return "CF", "RULE"

    # Cùng năm — xét thêm
    if yr_a and yr_b and yr_a == yr_b:
        # Một bên year-only → CL (gần, không confirm đủ)
        if only_a or only_b:
            return "CL", "RULE"

        mo_a = pa.get("month", "")
        mo_b = pb.get("month", "")
        d_a  = pa.get("day", "")
        d_b  = pb.get("day", "")

        # dd/mm hoán đổi (mục 5.5, T25)
        if mo_a and mo_b and d_a and d_b and mo_a == d_b and d_a == mo_b:
            return "CL", "RULE"

        # Một trong hai có ambiguous flag — có thể cùng ngày theo cách đọc khác
        if amb_a or amb_b:
            return "CL", "RULE"

        # Cùng năm, khác tháng/ngày, không giải thích được → CF
        return "CF", "RULE"

    # Không parse được năm đủ → fallback fuzzy
    return "CF", "RULE"


def _compare_gov_id(a: dict, b: dict) -> tuple[str, str, bool]:
    """Trả về (state, provenance, is_hard_conflict).

    Cùng type + cả hai VALID + khác value → HARD_CONFLICT (mục 11.2).
    Khác type → NC.
    """
    qa, qb = _quality(a, "government_id"), _quality(b, "government_id")
    if qa == "MISSING" and qb == "MISSING":
        return "MI", "RULE", False
    if qa == "MISSING" or qb == "MISSING":
        return "MI", "RULE", False

    type_a = a.get("government_id_type", "")
    type_b = b.get("government_id_type", "")
    if type_a and type_b and type_a != type_b:
        return "NC", "RULE", False

    val_a = a.get("government_id", "")
    val_b = b.get("government_id", "")
    if val_a == val_b:
        return "EX", "RULE", False

    # Cùng type, cả hai valid, khác giá trị → HARD_CONFLICT
    if qa == "VALID" and qb == "VALID":
        return "CF", "RULE", True   # hard_conflict = True

    return "DF", "RULE", False


def _compare_entity_type(a: dict, b: dict) -> bool:
    """Trả về True nếu PERSON vs ORGANIZATION → HARD_CONFLICT (mục 11.1)."""
    ta = a.get("entity_type", "PERSON")
    tb = b.get("entity_type", "PERSON")
    if "UNKNOWN" in (ta, tb):
        return False
    return ta != tb


# ─── Anchor helpers ───────────────────────────────────────────────────────────

def _count_anchors(comparisons: dict[str, str]) -> int:
    """Đếm số anchor thỏa (mục 12.1): phone/email EX/NX + name HS trở lên."""
    count = 0
    if comparisons.get("phone") in ("EX", "NX"):
        count += 1
    if comparisons.get("email") in ("EX", "NX"):
        count += 1
    if comparisons.get("name") in ("EX", "NX", "NA", "HS"):
        count += 1
    return count


# ─── Main comparison function ─────────────────────────────────────────────────

def score_pair(left: dict, right: dict, blocking_reasons: set[str]) -> dict:
    """So sánh hai record, trả về dict với Unified Comparison States.

    Giữ nguyên tên hàm để tương thích với resolution_service.py.

    Return keys:
      score           — float [0,1] backward compat
      match_weight    — weighted sum of state weights
      field_evidence  — list per-field dicts (state, provenance, similarity, left, right)
      comparisons     — dict field→state (dùng bởi classify_pair)
      decision        — HARD_CONFLICT / MATCH_CANDIDATE / REVIEW / NON_MATCH
      reason          — string reason code
      blocking_reasons — sorted list
    """
    a = left["normalized_fields"]
    b = right["normalized_fields"]

    # ── Hard constraint: entity type (mục 11.1) ───────────────────────────────
    if _compare_entity_type(a, b):
        return {
            "score": 0.0, "match_weight": -99.0,
            "field_evidence": [],
            "comparisons": {"entity_type": "CF"},
            "decision": "NON_MATCH",           # NON_MATCH để tương thích DB/items
            "reason": "ENTITY_TYPE_MISMATCH",  # reason chứa chi tiết hard constraint
            "blocking_reasons": sorted(blocking_reasons),
        }

    # ── Field comparisons ─────────────────────────────────────────────────────
    name_state, name_prov = _compare_name(a, b)
    phone_state, phone_prov = _compare_phone(a, b)
    email_state, email_prov = _compare_email(a, b)
    addr_state, addr_prov = _compare_address(a, b)
    dob_state, dob_prov = _compare_dob(a, b)
    gov_state, gov_prov, gov_hard = _compare_gov_id(a, b)

    # ── Hard constraint: government_id same-type conflict (mục 11.2) ──────────
    if gov_hard:
        comparisons = {
            "name": name_state, "phone": phone_state, "email": email_state,
            "address": addr_state, "dob": dob_state, "government_id": gov_state,
        }
        return {
            "score": 0.0, "match_weight": -99.0,
            "field_evidence": _build_evidence(a, b, comparisons, {
                "name": name_prov, "phone": phone_prov, "email": email_prov,
                "address": addr_prov, "dob": dob_prov, "government_id": gov_prov,
            }),
            "comparisons": comparisons,
            "decision": "NON_MATCH",                    # backward compat
            "reason": "SAME_TYPE_GOV_ID_CONFLICT",      # hard constraint detail
            "blocking_reasons": sorted(blocking_reasons),
        }

    comparisons = {
        "name": name_state, "phone": phone_state, "email": email_state,
        "address": addr_state, "dob": dob_state, "government_id": gov_state,
    }
    provenance = {
        "name": name_prov, "phone": phone_prov, "email": email_prov,
        "address": addr_prov, "dob": dob_prov, "government_id": gov_prov,
    }

    # ── Match weight (mục 10) — trọng số theo field ───────────────────────────
    weights = {
        "name": 3.0, "phone": 3.5, "email": 3.0, "dob": 2.0,
        "address": 1.0, "government_id": 4.0,
    }
    match_weight = sum(
        weights.get(field, 1.0) * _STATE_WEIGHT.get(state, 0.0)
        for field, state in comparisons.items()
    )

    # Backward-compat score [0,1]
    max_possible = sum(weights.values()) * 4.0
    score = max(0.0, min(1.0, match_weight / max_possible))

    # ── HITL flag: address ambiguous ward (mục 5.4) ──────────────────────────
    address_needs_hitl = (
        (a.get("address_parts") or {}).get("needs_hitl", False) or
        (b.get("address_parts") or {}).get("needs_hitl", False)
    )

    # ── Decision ──────────────────────────────────────────────────────────────
    anchors = _count_anchors(comparisons)
    has_cf = any(state == "CF" for state in comparisons.values())
    phone_or_email_exact = phone_state in ("EX", "NX") or email_state in ("EX", "NX")
    name_ok = name_state in ("EX", "NX", "NA", "HS")

    if has_cf or address_needs_hitl:
        decision = "REVIEW"
        reason = "CONFLICT_FIELD" if has_cf else "ADDRESS_WARD_AMBIGUOUS"
    elif match_weight >= 8.0 and phone_or_email_exact and name_ok:
        decision, reason = "MATCH_CANDIDATE", "EXACT_CONTACT_AND_NAME"
    elif match_weight >= 10.0 and anchors >= 2:
        decision, reason = "MATCH_CANDIDATE", "MULTI_FIELD_SUPPORT"
    elif match_weight >= 3.0 or phone_or_email_exact:
        decision, reason = "REVIEW", "AMBIGUOUS_FIELD_EVIDENCE"
    else:
        decision, reason = "NON_MATCH", "WEAK_FIELD_EVIDENCE"

    return {
        "score": round(score, 6),
        "match_weight": round(match_weight, 4),
        "field_evidence": _build_evidence(a, b, comparisons, provenance),
        "comparisons": comparisons,
        "conflict_rule_version": CONFLICT_RULE_VERSION,
        "decision": decision,
        "reason": reason,
        "blocking_reasons": sorted(blocking_reasons),
        "address_needs_hitl": address_needs_hitl,
    }


def _build_evidence(a: dict, b: dict, comparisons: dict, provenance: dict) -> list[dict]:
    """Build field_evidence list với state + provenance + backward-compat similarity."""
    result = []
    for field in ("name", "phone", "email", "address", "dob", "government_id"):
        state = comparisons.get(field, "NC")
        result.append({
            "field": field,
            "state": state,
            "provenance": provenance.get(field, "RULE"),
            "similarity": round(_STATE_SCORE.get(state, 0.0), 4),
            "left": a.get(field, ""),
            "right": b.get(field, ""),
        })
    return result
