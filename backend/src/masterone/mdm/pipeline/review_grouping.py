"""Phân loại pair vào Primary Group A/C/D và Semantic Subgroup.

Theo kiến trúc v3.1:
  Mục 13 — Primary Group (A / C / D)
  Mục 15 — Semantic Subgroup và Comparison Signature
  Mục 16 — Pseudocode Grouping

Primary Group:
  A — Strong / clean evidence (không hard conflict, không CF, đủ anchor)
  C — Missing required anchor (không conflict, thiếu anchor cần thiết)
  D — Non-hard conflict (có ít nhất một field ở state CF theo mục 8.1)

State CF vs DF:
  CF → kích hoạt Group D
  DF → KHÔNG kích hoạt Group D (chỉ ảnh hưởng A/C)
  IV, MI, SH, NC → không phải conflict, không kích hoạt D

Semantic Subgroups:
  A1 — Name + Phone
  A2 — Name + Email
  A3 — Phone/Email + DOB + compatible name
  C1 — Phone/Email missing, identity anchors partial
  C2 — Name weak, contact anchors strong
  C_MULTI — nhiều required anchor missing
  D1 — Name similar + Phone CF
  D2 — Phone same + Name CF/suspicious
  D3 — Address/Contact same + Identity field CF (name/dob)
  D4 — DOB CF, identity fields khác yếu
  D_MULTI — nhiều CF field
"""

from hashlib import sha256


# ─── State sets ──────────────────────────────────────────────────────────────

_STRONG_ANCHOR_STATES = frozenset({"EX", "NX", "NA", "HS"})
_CONTACT_ANCHOR_STATES = frozenset({"EX", "NX"})

# Chỉ CF mới kích hoạt Group D (không phải DF, IV, MI, SH, NC)
_CONFLICT_STATE = "CF"


def _states(pair: dict) -> dict[str, str]:
    """Trích xuất dict field→state từ field_evidence hoặc comparisons."""
    if "comparisons" in pair:
        return dict(pair["comparisons"])
    return {item["field"]: item.get("state", "NC") for item in pair.get("field_evidence", [])}


def _has_non_hard_conflict(states: dict[str, str]) -> bool:
    """True nếu có ít nhất một field ở state CF (mục 8.1, 16)."""
    return any(v == _CONFLICT_STATE for v in states.values())


def _contact_anchors_met(states: dict[str, str]) -> list[str]:
    """Trả về list các contact anchor thỏa (phone/email EX/NX)."""
    anchors = []
    if states.get("phone") in _CONTACT_ANCHOR_STATES:
        anchors.append("phone")
    if states.get("email") in _CONTACT_ANCHOR_STATES:
        anchors.append("email")
    return anchors


def _name_anchor_met(states: dict[str, str]) -> bool:
    return states.get("name") in _STRONG_ANCHOR_STATES


def _dob_anchor_met(states: dict[str, str]) -> bool:
    return states.get("dob") in ("EX",)


# ─── Subgroup selectors ───────────────────────────────────────────────────────

def _select_d_subgroup(states: dict[str, str]) -> str:
    """Conservative D subgroup selection."""
    cf_fields = [f for f, s in states.items() if s == _CONFLICT_STATE]
    if len(cf_fields) >= 2:
        return "D_MULTI"
    cf = cf_fields[0] if cf_fields else ""

    if cf == "phone":
        return "D2"  # Phone CF (phone_same logic → phone giống nhưng name differ)
    if cf == "name":
        # Name CF + contact anchor → D1 or D2
        contacts = _contact_anchors_met(states)
        if "phone" in contacts:
            return "D2"
        return "D1"
    if cf == "dob":
        return "D4"
    if cf in ("address",):
        return "D3"
    # Fallback cho email/gov CF
    contacts = _contact_anchors_met(states)
    if contacts:
        return "D3"
    return "D1"


def _select_c_subgroup(states: dict[str, str]) -> str:
    """Conservative C subgroup selection."""
    missing_contacts = [f for f in ("phone", "email")
                        if states.get(f) in ("MI", "IV", "SH", "DF", "NC")]
    if len(missing_contacts) >= 2:
        return "C_MULTI"
    if "phone" in missing_contacts and _name_anchor_met(states):
        return "C1"
    if "email" in missing_contacts and _name_anchor_met(states):
        return "C1"
    if not _name_anchor_met(states):
        return "C2"
    return "C_MULTI"


def _select_a_subgroup(states: dict[str, str]) -> str:
    """Conservative A subgroup selection — chọn subgroup yêu cầu ít nhất."""
    contacts = _contact_anchors_met(states)
    name_ok = _name_anchor_met(states)
    dob_ok = _dob_anchor_met(states)

    if "phone" in contacts and "email" in contacts and name_ok:
        return "A1"  # tốt nhất
    if "phone" in contacts and name_ok:
        return "A1"
    if "email" in contacts and name_ok:
        return "A2"
    if contacts and dob_ok:
        return "A3"
    return "A1"  # fallback


# ─── Signature builder ────────────────────────────────────────────────────────

def _build_signature(states: dict[str, str]) -> str:
    """N:HS|P:CF|E:EX|DOB:EX|A:HS|G:NC (mục 15.2)."""
    field_codes = {
        "name": "N", "phone": "P", "email": "E",
        "dob": "DOB", "address": "A", "government_id": "G",
    }
    parts = []
    for field, code in field_codes.items():
        state = states.get(field, "NC")
        parts.append(f"{code}:{state}")
    return "|".join(parts)


# ─── Public API ───────────────────────────────────────────────────────────────

def classify_pair(pair: dict, record_by_id: dict[str, dict]) -> dict | None:
    """Phân loại pair vào Primary Group A/C/D + Semantic Subgroup.

    Chỉ xử lý pair ở trạng thái REVIEW (triage_status == "REVIEW").
    Trả về None nếu pair không phải REVIEW.

    Theo pseudocode mục 16:
      if has_non_hard_conflict → D
      elif missing_required_anchor → C
      else → A
    """
    if pair.get("triage_status") != "REVIEW":
        return None

    states = _states(pair)

    # Theo mục 16 — conflict check trước
    if _has_non_hard_conflict(states):
        group = "D"
        subgroup = _select_d_subgroup(states)
    else:
        contacts = _contact_anchors_met(states)
        name_ok = _name_anchor_met(states)
        # Missing required anchor → C
        # Required anchor đơn giản nhất: ít nhất một contact anchor HOẶC (name + DOB)
        has_enough_anchor = bool(contacts) or (name_ok and _dob_anchor_met(states))
        if not has_enough_anchor:
            group = "C"
            subgroup = _select_c_subgroup(states)
        else:
            group = "A"
            subgroup = _select_a_subgroup(states)

    signature_str = _build_signature(states)
    # Prefix để phân biệt với signature cũ
    versioned_sig = f"mdm-v3.1:{group}:{subgroup}:{signature_str}"
    sig_hash = sha256(versioned_sig.encode()).hexdigest()

    return {
        "primary_group": group,
        "subgroup": subgroup,
        "signature_hash": sig_hash,
        "signature": signature_str,
        "reason_codes": [pair.get("triage_reason", "")],
    }
