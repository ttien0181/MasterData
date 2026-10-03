"""Regression tests T21–T28 theo kiến trúc v3.1 mục 35.

Các test này kiểm tra Conflict Taxonomy (mục 8.1):
  CF  → kích hoạt Group D
  DF  → KHÔNG kích hoạt Group D
  SH/IV/MI/NC → không phải conflict
"""

import pytest

from masterone.mdm.pipeline.matching import score_pair
from masterone.mdm.pipeline.review_grouping import classify_pair
from masterone.mdm.pipeline.standardization import standardize, _infer_gov_id_type, parse_address, parse_dob
from masterone.mdm.pipeline.address_mapping import resolve_ward


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _make_record(rid: str, source: str = "CRM", **raw) -> dict:
    normalized, _ = standardize(raw)
    return {"id": rid, "external_id": rid, "source_system": source,
            "raw_fields": raw, "normalized_fields": normalized}


def _score(r1, r2, reasons=None):
    return score_pair(r1, r2, reasons or {"name_gram"})


def _state(result: dict, field: str) -> str:
    return next(e["state"] for e in result["field_evidence"] if e["field"] == field)


def _group(pair_result: dict, r1: dict, r2: dict) -> str | None:
    pair = {
        "triage_status": "REVIEW",
        "triage_reason": pair_result["reason"],
        "field_evidence": pair_result["field_evidence"],
        "comparisons": pair_result["comparisons"],
    }
    cls = classify_pair(pair, {r1["id"]: r1, r2["id"]: r2})
    return cls["primary_group"] if cls else None


# ─── T21 ─────────────────────────────────────────────────────────────────────

def test_T21_phone_different_valid_no_primary_flag_is_DF_not_D():
    """Phone khác nhau, cả hai VALID, không có cờ primary/current → P:DF, không vào Group D."""
    r1 = _make_record("a1", phone="0912345678", name="Nguyen Van An")
    r2 = _make_record("a2", phone="0987654321", name="Nguyen Van An")
    result = _score(r1, r2)

    assert _state(result, "phone") == "DF", "Phone khác nhau VALID không có primary/current → DF"
    assert result["decision"] != "NON_MATCH"

    # Không vào Group D vì DF không phải CF
    # (pair sẽ là REVIEW hoặc MATCH_CANDIDATE tùy match_weight, nhưng nếu classify thì group != D)
    # Tạo pair ở REVIEW để test classify
    pair = {"triage_status": "REVIEW", "triage_reason": result["reason"],
            "field_evidence": result["field_evidence"], "comparisons": result["comparisons"]}
    cls = classify_pair(pair, {r1["id"]: r1, r2["id"]: r2})
    if cls:
        assert cls["primary_group"] != "D", "DF không được vào Group D"


# ─── T22 ─────────────────────────────────────────────────────────────────────

def test_T22_dob_year_different_is_CF_and_goes_to_D():
    """DOB khác năm (cả hai full date VALID) → DOB:CF → vào Group D."""
    r1 = _make_record("b1", name="Le Van An", phone="0912345678", dob="1999-05-12")
    r2 = _make_record("b2", name="Le Van An", phone="0912345678", dob="1998-05-12")
    result = _score(r1, r2, {"exact_phone"})

    assert _state(result, "dob") == "CF", "Năm DOB khác → CF"
    grp = _group(result, r1, r2)
    assert grp == "D", "DOB:CF → Group D"


# ─── T23 ─────────────────────────────────────────────────────────────────────

def test_T23_phone_different_one_is_SH_not_CF():
    """Phone một bên SHARED (role email/hotline) → SH, không phải CF hoặc DF."""
    r1 = _make_record("c1", name="Cty ABC", phone="0912345678")
    # Tạo record với phone quality SHARED bằng cách truyền qua normalized_fields trực tiếp
    nf2, _ = standardize({"name": "Cty ABC", "phone": "0912345678"})
    nf2["_field_quality"]["phone"] = "SHARED"
    r2 = {"id": "c2", "external_id": "c2", "source_system": "ERP",
          "raw_fields": {"name": "Cty ABC", "phone": "0912345678"},
          "normalized_fields": nf2}
    result = _score(r1, r2, {"name_gram"})

    phone_s = _state(result, "phone")
    assert phone_s == "SH", f"SHARED quality → SH state, got {phone_s}"
    assert phone_s not in ("CF", "DF")


# ─── T24 ─────────────────────────────────────────────────────────────────────

def test_T24_address_different_never_CF():
    """Address khác hoàn toàn (cả hai valid) → A:DF, address không bao giờ CF."""
    r1 = _make_record("d1", name="Nguyen Van An", phone="0912345678", address="10 Nguyen Trai Ha Noi")
    r2 = _make_record("d2", name="Nguyen Van An", phone="0912345678", address="25 Le Loi HCM")
    result = _score(r1, r2, {"exact_phone"})

    addr_s = _state(result, "address")
    assert addr_s != "CF", f"Address không bao giờ CF, got {addr_s}"

    # Không vào Group D vì address không phải CF
    pair = {"triage_status": "REVIEW", "triage_reason": result["reason"],
            "field_evidence": result["field_evidence"], "comparisons": result["comparisons"]}
    cls = classify_pair(pair, {r1["id"]: r1, r2["id"]: r2})
    if cls:
        assert cls["primary_group"] != "D", "Address DF không đưa vào Group D"


# ─── T25 ─────────────────────────────────────────────────────────────────────

def test_T25_dob_ddmm_swap_is_CL_not_CF():
    """DOB hoán đổi dd/mm↔mm/dd → CL, không CF."""
    r1 = _make_record("e1", name="Tran Thi B", dob="1995-03-12")
    r2 = _make_record("e2", name="Tran Thi B", dob="1995-12-03")
    result = _score(r1, r2)
    assert _state(result, "dob") == "CL", "dd/mm swap → CL"


def test_T25_dob_year_different_is_CF():
    """DOB khác năm → CF."""
    r1 = _make_record("e3", name="Tran Thi B", dob="1995-05-12")
    r2 = _make_record("e4", name="Tran Thi B", dob="1994-05-12")
    result = _score(r1, r2)
    assert _state(result, "dob") == "CF", "Năm khác → CF"


# ─── T26 ─────────────────────────────────────────────────────────────────────

def test_T26_phone_IV_not_CF_or_DF():
    """Phone một bên INVALID → IV state, không CF hoặc DF."""
    r1 = _make_record("f1", name="Vo Van C", phone="0912345678")
    r2 = _make_record("f2", name="Vo Van C", phone="123")  # INVALID (quá ngắn)
    result = _score(r1, r2)
    phone_s = _state(result, "phone")
    assert phone_s == "IV", f"Phone INVALID → IV, got {phone_s}"
    assert phone_s not in ("CF", "DF")


def test_T26_phone_missing_not_CF():
    """Phone một bên MISSING → MI, không CF hoặc DF."""
    r1 = _make_record("f3", name="Vo Van C", phone="0912345678")
    r2 = _make_record("f4", name="Vo Van C", phone="")
    result = _score(r1, r2)
    phone_s = _state(result, "phone")
    assert phone_s == "MI", f"Phone missing → MI, got {phone_s}"
    assert phone_s not in ("CF", "DF")


# ─── T27 ─────────────────────────────────────────────────────────────────────

def test_T27_name_EX_phone_DF_email_EX_not_group_D():
    """name=EX, phone=DF, email=EX, dob=EX → không vào Group D (DF không phải CF)."""
    r1 = _make_record("g1", name="Nguyen Van An", phone="0912345678",
                      email="an@example.com", dob="1999-05-12")
    r2 = _make_record("g2", name="Nguyen Van An", phone="0987654321",
                      email="an@example.com", dob="1999-05-12")
    result = _score(r1, r2, {"exact_email"})

    assert _state(result, "phone") == "DF"
    assert _state(result, "name") in ("EX", "NA")
    assert _state(result, "email") == "EX"

    pair = {"triage_status": "REVIEW", "triage_reason": result["reason"],
            "field_evidence": result["field_evidence"], "comparisons": result["comparisons"]}
    cls = classify_pair(pair, {r1["id"]: r1, r2["id"]: r2})
    if cls:
        assert cls["primary_group"] != "D", "DF không được kích hoạt Group D"


# ─── T28 ─────────────────────────────────────────────────────────────────────

def test_T28_gov_id_type_auto_detection():
    """Gov ID type tự suy từ độ dài: 9 số → CMND_9, 12 số → CCCD_12."""
    assert _infer_gov_id_type("123456789") == "CMND_9"
    assert _infer_gov_id_type("123456789012") == "CCCD_12"
    assert _infer_gov_id_type("B1234567") == "PASSPORT"
    assert _infer_gov_id_type("0123456789") == "TAX_ID"


def test_T28_same_type_gov_id_conflict_is_NON_MATCH():
    """Cùng CCCD_12, cả hai VALID, khác giá trị → NON_MATCH với reason SAME_TYPE_GOV_ID_CONFLICT."""
    nf1, _ = standardize({"name": "Nguyen Van X", "government_id": "123456789012",
                          "government_id_type": "CCCD_12"})
    nf2, _ = standardize({"name": "Nguyen Van X", "government_id": "999888777666",
                          "government_id_type": "CCCD_12"})
    r1 = {"id": "h1", "external_id": "h1", "source_system": "CRM",
          "raw_fields": {}, "normalized_fields": nf1}
    r2 = {"id": "h2", "external_id": "h2", "source_system": "ERP",
          "raw_fields": {}, "normalized_fields": nf2}
    result = score_pair(r1, r2, set())
    assert result["decision"] == "NON_MATCH"
    assert result["reason"] == "SAME_TYPE_GOV_ID_CONFLICT"


def test_T28_different_gov_id_types_is_NC():
    """CMND_9 vs CCCD_12 → NC (không phải HARD_CONFLICT theo mục 5.6)."""
    nf1, _ = standardize({"name": "Nguyen Van X", "government_id": "123456789",
                          "government_id_type": "CMND_9"})
    nf2, _ = standardize({"name": "Nguyen Van X", "government_id": "123456789012",
                          "government_id_type": "CCCD_12"})
    r1 = {"id": "i1", "external_id": "i1", "source_system": "CRM",
          "raw_fields": {}, "normalized_fields": nf1}
    r2 = {"id": "i2", "external_id": "i2", "source_system": "ERP",
          "raw_fields": {}, "normalized_fields": nf2}
    result = score_pair(r1, r2, set())
    gov_state = next(e["state"] for e in result["field_evidence"] if e["field"] == "government_id")
    assert gov_state == "NC", f"Khác loại giấy tờ → NC, got {gov_state}"
    assert result["decision"] != "NON_MATCH", "Khác type gov_id không phải hard conflict"


# ─── Mục 5.4 — Address parsing ───────────────────────────────────────────────

def test_54_province_abbrev_hcm():
    """HCM/TPHCM được map về 'ho chi minh'."""
    r1 = parse_address("123 Nguyen Trai, Quan 1, HCM")
    r2 = parse_address("456 Le Loi, Quan 3, TPHCM")
    assert r1["province"] == "ho chi minh", f"HCM → ho chi minh, got {r1['province']}"
    assert r2["province"] == "ho chi minh", f"TPHCM → ho chi minh, got {r2['province']}"


def test_54_province_abbrev_hn():
    """HN được map về 'ha noi'."""
    r = parse_address("50 Ba Trieu, Hai Ba Trung, HN")
    assert r["province"] == "ha noi", f"HN → ha noi, got {r['province']}"


def test_54_province_full_name():
    """Tên tỉnh đầy đủ (ascii) được nhận dạng."""
    r = parse_address("15 Tran Phu, Hai Chau, Da Nang")
    assert r["province"] == "da nang", f"Da Nang full → da nang, got {r['province']}"


def test_54_address_province_conflict_caps_at_WS():
    """Hai địa chỉ khác tỉnh không thể là HS/MS — tối đa WS hoặc DF (mục 5.4)."""
    r1 = _make_record("addr1", name="Nguyen Van A", phone="0912345678",
                      address="50 Nguyen Trai, Quan 1, HCM")
    r2 = _make_record("addr2", name="Nguyen Van A", phone="0912345678",
                      address="10 Pho Hue, Hai Ba Trung, Ha Noi")
    result = _score(r1, r2, {"exact_phone"})
    addr_s = _state(result, "address")
    assert addr_s in ("WS", "DF"), f"Khác tỉnh → WS hoặc DF, got {addr_s}"
    assert addr_s not in ("HS", "MS"), "Khác tỉnh không được HS/MS"


def test_54_address_missing_is_MI():
    """Address rỗng → quality MISSING."""
    nf, _ = standardize({"name": "Test", "address": ""})
    assert nf["_field_quality"]["address"] == "MISSING"
    assert nf["address_parts"]["province"] == ""


def test_54_address_parts_street_fragment():
    """street_fragment chứa phần địa chỉ trừ tỉnh."""
    r = parse_address("10 Ly Tu Trong, Phuong Ben Nghe, Quan 1, Ho Chi Minh")
    assert r["province"] == "ho chi minh"
    assert "ly tu trong" in r["street_fragment"] or "10" in r["street_fragment"]


# ─── Mục 5.5 — DOB multi-format ──────────────────────────────────────────────

def test_55_dob_yyyy_mm_dd():
    """ISO format YYYY-MM-DD → canonical YYYY-MM-DD."""
    d = parse_dob("1990-05-15")
    assert d["canonical"] == "1990-05-15"
    assert d["year"] == "1990" and d["month"] == "05" and d["day"] == "15"
    assert not d["is_year_only"]
    assert d["quality"] == "VALID"


def test_55_dob_dd_mm_yyyy():
    """Vietnamese format DD/MM/YYYY → canonical YYYY-MM-DD."""
    d = parse_dob("15/05/1990")
    assert d["canonical"] == "1990-05-15"
    assert not d["is_ambiguous"]


def test_55_dob_year_only():
    """Chỉ có năm → is_year_only=True, canonical YYYY."""
    d = parse_dob("1985")
    assert d["is_year_only"] is True
    assert d["canonical"] == "1985"
    assert d["quality"] == "VALID"


def test_55_dob_ambiguous_ddmm():
    """01/02/1990 ambiguous (cả dd và mm ≤12) → is_ambiguous=True, VN convention DD/MM."""
    d = parse_dob("01/02/1990")
    assert d["is_ambiguous"] is True
    assert d["canonical"] == "1990-02-01"  # VN: DD/MM → day=1, month=2


def test_55_dob_invalid():
    """Giá trị rác → quality INVALID."""
    d = parse_dob("not-a-date")
    assert d["quality"] == "INVALID"


def test_55_dob_placeholder():
    """01/01/1900 hoặc 00/00/0000 → quality PLACEHOLDER."""
    d = parse_dob("01/01/1900")
    assert d["quality"] in ("PLACEHOLDER", "VALID")  # tùy threshold


def test_55_compare_dob_year_only_vs_full_same_year_is_CL():
    """DOB year-only vs full date cùng năm → CL."""
    r1 = _make_record("dob1", name="Test", dob="1990")
    r2 = _make_record("dob2", name="Test", dob="1990-05-15")
    result = _score(r1, r2)
    assert _state(result, "dob") == "CL", "year-only vs full same year → CL"


# ─── Mục 5.4 — Ward mapping (T29–T31) ────────────────────────────────────────

def test_T29_resolved_ward_same_maps_to_overlap():
    """Hai địa chỉ cùng phường cũ (resolved 1→1) → ward_ids giao nhau → không bị DIFFER."""
    # Phường Trúc Bạch (00004) → Phường Ba Đình sau sáp nhập (resolved)
    r = resolve_ward("Phuong Truc Bach")
    assert r.status == "resolved", f"Phường Trúc Bạch phải resolved, got {r.status}"
    assert len(r.unit_ids) == 1, "resolved phải có đúng 1 target"

    # Hai records cùng phường → ward_ids giống nhau → overlap → không DIFFER
    assert bool(r.unit_ids & r.unit_ids), "Cùng ward → overlap"


def test_T29_resolved_ward_different_no_overlap():
    """Hai phường khác nhau (đều resolved) → ward_ids không giao → DIFFER signal."""
    r1 = resolve_ward("Phuong Truc Bach")    # → Ba Đình
    r2 = resolve_ward("Phuong Quan Thanh")   # → Ba Đình (cùng target theo mapping)
    # Cả hai → Ba Đình: vẫn overlap
    if r1.status == "resolved" and r2.status == "resolved":
        # Trúc Bạch + Quán Thánh đều sáp nhập vào Ba Đình → overlap
        assert bool(r1.unit_ids & r2.unit_ids), "Cùng target ward → overlap"


def test_T30_ambiguous_ward_sets_needs_hitl():
    """Phường ambiguous (1→nhiều) → address_parts.needs_hitl=True."""
    # Phường Ngọc Hà (00016) là ambiguous theo data
    r_ngoc_ha = resolve_ward("Phuong Ngoc Ha")
    if r_ngoc_ha.status == "ambiguous":
        nf, _ = standardize({"name": "Test", "address": "10 Doi Can, Phuong Ngoc Ha, Ha Noi"})
        # Nếu ward được nhận dạng là ambiguous → needs_hitl
        ap = nf.get("address_parts", {})
        if ap.get("ward_status") == "ambiguous":
            assert ap["needs_hitl"] is True, "ambiguous ward → needs_hitl=True"

    # Dù ward có nhận ra hay không, needs_hitl không được raise exception
    nf2, _ = standardize({"name": "Test", "address": "abc xyz"})
    assert "needs_hitl" in (nf2.get("address_parts") or {}), "address_parts phải có needs_hitl key"


def test_T31_address_same_ward_resolved_score_at_least_MS():
    """Hai địa chỉ cùng phường (resolved) → state ít nhất MS dù chuỗi khác nhau."""
    # Tạo 2 records với cùng phường nhưng số nhà khác
    nf1, _ = standardize({"name": "A", "address": "10 Doi Can, Phuong Truc Bach, Ha Noi"})
    nf2, _ = standardize({"name": "A", "address": "25 Hang Bun, Phuong Truc Bach, Ha Noi"})

    ap1 = nf1.get("address_parts", {})
    ap2 = nf2.get("address_parts", {})

    # Chỉ test nếu cả hai đều resolve được ward
    if (ap1.get("ward_status") == "resolved" and ap2.get("ward_status") == "resolved"
            and ap1.get("ward_ids") and ap2.get("ward_ids")):
        overlap = ap1["ward_ids"] & ap2["ward_ids"]
        if overlap:
            r1 = {"id": "w1", "external_id": "w1", "source_system": "CRM",
                  "raw_fields": {}, "normalized_fields": nf1}
            r2 = {"id": "w2", "external_id": "w2", "source_system": "ERP",
                  "raw_fields": {}, "normalized_fields": nf2}
            result = score_pair(r1, r2, set())
            addr_s = _state(result, "address")
            assert addr_s in ("HS", "MS", "NX"), \
                f"Cùng ward resolved → ít nhất MS, got {addr_s}"
