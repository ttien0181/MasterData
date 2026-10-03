"""Standardization pipeline cho Customer Master Data.

Theo kiến trúc v3.1, mục 5 (dữ liệu Việt Nam) và mục 6 (Data Quality).
Giữ nguyên signature (normalized_fields, flags) để tương thích với
resolution_import.py, nhưng bổ sung _field_quality vào normalized_fields.
"""

import re
import unicodedata

from masterone.mdm.pipeline.address_mapping import resolve_ward, ward_overlap  # noqa: F401


# ─── Legacy 11-digit mobile mapping (old prefix → new prefix) ────────────────
# Nguồn: VNPT/Viettel/MobiFone mapping chính thức
_LEGACY_PHONE_MAP: dict[str, str] = {
    "0162": "032", "0163": "033", "0164": "034", "0165": "035",
    "0166": "036", "0167": "037", "0168": "038", "0169": "039",
    "0123": "083", "0124": "084", "0125": "085", "0127": "081",
    "0129": "082", "0120": "070", "0121": "079", "0122": "077",
    "0126": "076", "0128": "078", "0186": "056", "0188": "058",
    "0199": "059",
}

_PLACEHOLDER_PHONES: frozenset[str] = frozenset({
    "0000000000", "0123456789", "0999999999", "1111111111",
    "9999999999", "0111111111", "0000000", "1234567890",
})

_PLACEHOLDER_EMAILS: frozenset[str] = frozenset({
    "test@test.com", "noemail@noemail.com", "a@a.com",
    "none@none.com", "test@example.com", "user@example.com",
    "abc@abc.com", "no@email.com",
})

# Tiền tố email tổ chức / dùng chung — không dùng làm anchor mạnh (mục 5.3)
_ROLE_EMAIL_PREFIXES: frozenset[str] = frozenset({
    "info", "admin", "ketoan", "sales", "support",
    "contact", "office", "hr", "help", "noreply", "no-reply",
})

# Ngày sinh placeholder của các hệ thống nguồn (mục 5.5)
_PLACEHOLDER_DOBS: frozenset[str] = frozenset({
    "1900-01-01", "1970-01-01", "01-01-1900", "0001-01-01",
    "01/01/1900", "01/01/1970", "1900/01/01", "1970/01/01",
})

FIELDS = ("name", "phone", "email", "address")


# ─── Address helpers (mục 5.4) ───────────────────────────────────────────────

# Viết tắt tỉnh/thành phố → tên chuẩn hóa (lowercase ascii)
_PROVINCE_ABBREV: dict[str, str] = {
    "hn": "ha noi", "hni": "ha noi",
    "hcm": "ho chi minh", "tphcm": "ho chi minh", "hcmc": "ho chi minh",
    "dn": "da nang",
    "ct": "can tho",
    "hp": "hai phong",
    "bd": "binh duong",
    "brvt": "ba ria vung tau", "vt": "ba ria vung tau",
    "la": "long an",
    "qn": "quang ninh",
    "nt": "nha trang", "kh": "khanh hoa",
    "pt": "phu yen", "py": "phu yen",
    "bp": "binh phuoc",
    "bt": "binh thuan",
    "gl": "gia lai",
    "dl": "dak lak", "daklak": "dak lak",
    "lc": "lao cai",
    "tb": "thai binh",
    "na": "nghe an",
    "hb": "hoa binh",
    "nb": "ninh binh",
    "nd": "nam dinh",
    "tt": "thua thien hue", "hue": "thua thien hue",
    "qb": "quang binh",
    "qt": "quang tri",
    "qng": "quang ngai",
    "qnm": "quang nam",
    "bn": "bac ninh",
    "bg": "bac giang",
    "pth": "phu tho",
    "vp": "vinh phuc",
    "hd": "hai duong",
    "hy": "hung yen",
    "tnh": "thai nguyen",
    "ls": "lang son",
    "cb": "cao bang",
    "bk": "bac kan",
    "tq": "tuyen quang",
    "yb": "yen bai",
    "sla": "son la",
    "db": "dien bien",
    "lb": "lai chau",
    "hg": "ha giang",
}

# Mapping hành chính cũ → mới sau các lần sáp nhập (mục 5.4)
# Nhiều-1: nhiều tên cũ có thể map về cùng một tên mới
_PROVINCE_ADMIN_MAP: dict[str, str] = {
    # Hà Tây sáp nhập vào Hà Nội 2008
    "ha tay": "ha noi",
    # TP.HCM các cách viết
    "thanh pho ho chi minh": "ho chi minh",
    "sai gon": "ho chi minh",
    # Các tỉnh dự kiến sáp nhập (2025 reform — cập nhật theo quyết định chính thức)
    "ha nam": "ninh binh",  # theo phương án sáp nhập 2025
    "nam dinh": "ninh binh",
    "ninh binh": "ninh binh",
    "thai binh": "nam dinh moi",   # placeholder — cập nhật khi có quyết định chính thức
    # Thêm các mapping khi có quyết định hành chính mới
}

_ADMIN_MAPPING_VERSION = "vn-admin-2025-draft"


def _normalize_province_token(token: str) -> str:
    """Chuẩn hóa một token tỉnh/thành: thử tra viết tắt rồi admin map."""
    t = _ascii_fold(token)
    # Thử viết tắt trước
    if t in _PROVINCE_ABBREV:
        return _PROVINCE_ABBREV[t]
    # Thử admin map
    if t in _PROVINCE_ADMIN_MAP:
        return _PROVINCE_ADMIN_MAP[t]
    return t


# Regex nhận dạng các từ khóa phân cấp hành chính VN
_ADMIN_KEYWORDS = re.compile(
    r"\b(tinh|thanh pho|quan|huyen|thi xa|thi tran|phuong|xa|to|khu|lo|"
    r"duong|ngo|hem|kiot|tang|phong|so|can ho)\b",
    re.IGNORECASE,
)

# Tên tỉnh/thành đầy đủ (ascii) — để nhận dạng ở cuối địa chỉ
_KNOWN_PROVINCES_ASC: frozenset[str] = frozenset(set(_PROVINCE_ABBREV.values()) | set(_PROVINCE_ADMIN_MAP.values()))


def _extract_ward_token(parts: list[str]) -> str:
    """Trích token phường/xã từ danh sách parts (sau khi tách bằng dấu phẩy).

    Tìm part có chứa từ khóa phường/xã/thị trấn.
    Trả về chuỗi nguyên bản (chưa ascii-fold) để resolve_ward xử lý.
    """
    _ward_prefix = re.compile(
        r"^(phuong|p\.|xa|thi tran|tt\.?)\s+", re.IGNORECASE
    )
    for part in reversed(parts):  # thường phường/xã gần cuối địa chỉ
        if _ward_prefix.match(part.strip()):
            return part.strip()
    return ""


def parse_address(raw: str) -> dict:
    """Parse địa chỉ VN thành các thành phần (mục 5.4).

    Bước thực hiện:
      1. ASCII-fold + casefold toàn bộ
      2. Nhận dạng tỉnh/TP từ phần cuối (abbrev + admin map)
      3. Nhận dạng phường/xã → resolve_ward() → ward_ids
         - status="resolved"  → 1 phường mới, dùng trực tiếp
         - status="ambiguous" → nhiều phường mới → cần HITL
         - status="unknown"   → không nhận ra → bỏ qua, dùng fuzzy
      4. Phần còn lại = street_fragment

    Trả về dict:
      province          — tên tỉnh/thành chuẩn hóa (ascii) hoặc ""
      province_raw      — token tỉnh gốc
      ward_ids          — frozenset[str] unit_id(s) sau sáp nhập (rỗng nếu unknown)
      ward_status       — "resolved" | "ambiguous" | "unknown"
      ward_raw          — token phường gốc
      street_fragment   — phần địa chỉ còn lại (không có tỉnh)
      normalized        — toàn bộ địa chỉ ascii-fold (dùng fuzzy)
      needs_hitl        — True nếu ward_status == "ambiguous"
      admin_map_version — phiên bản mapping đã áp dụng
    """
    _empty = {
        "province": "", "province_raw": "", "ward_ids": frozenset(),
        "ward_status": "unknown", "ward_raw": "", "street_fragment": "",
        "normalized": "", "needs_hitl": False,
        "admin_map_version": _ADMIN_MAPPING_VERSION,
    }
    if not raw or not raw.strip():
        return _empty

    normalized = _ascii_fold(raw)

    # Tách bằng dấu phẩy — phần cuối thường là tỉnh/TP
    parts = [p.strip() for p in normalized.split(",") if p.strip()]
    if not parts:
        return {**_empty, "normalized": normalized, "street_fragment": normalized}

    # ── Nhận dạng tỉnh từ phần cuối ─────────────────────────────────────────
    candidate = parts[-1]
    clean = re.sub(r"^(tinh|thanh pho|tp\.?)\s*", "", candidate, flags=re.IGNORECASE).strip()
    province = _normalize_province_token(clean)

    if province not in _KNOWN_PROVINCES_ASC:
        tokens = normalized.split()
        if tokens:
            last = tokens[-1]
            mapped = _normalize_province_token(last)
            if mapped in _KNOWN_PROVINCES_ASC:
                province = mapped
                candidate = last
            else:
                province = ""
                candidate = ""

    inner_parts = parts[:-1] if province and len(parts) > 1 else parts
    street_fragment = ", ".join(inner_parts)

    # ── Resolve phường/xã → ward_ids ─────────────────────────────────────────
    ward_token = _extract_ward_token(inner_parts)
    if ward_token:
        resolution = resolve_ward(ward_token)
    else:
        # Thử từng part từ cuối lên — đôi khi không có prefix "Phường"
        resolution_found = None
        for part in reversed(inner_parts):
            r = resolve_ward(part)
            if r.status != "unknown":
                resolution_found = r
                ward_token = part
                break
        resolution = resolution_found or resolve_ward("")

    ward_ids = resolution.unit_ids
    ward_status = resolution.status
    needs_hitl = ward_status == "ambiguous"

    return {
        "province": province,
        "province_raw": candidate,
        "ward_ids": ward_ids,
        "ward_status": ward_status,
        "ward_raw": ward_token,
        "street_fragment": street_fragment,
        "normalized": normalized,
        "needs_hitl": needs_hitl,
        "admin_map_version": _ADMIN_MAPPING_VERSION,
    }


# ─── Government ID helpers ────────────────────────────────────────────────────

def _infer_gov_id_type(value: str) -> str:
    """Suy loại giấy tờ từ độ dài / cấu trúc (mục 5.6).

    Quy tắc VN hiện hành:
      9 chữ số         → CMND_9  (CMND cũ 9 số)
      12 chữ số        → CCCD_12 (CCCD gắn chip / CMND 12 số cũ)
      Bắt đầu chữ cái + 6-8 chữ số → PASSPORT
      Khác             → UNKNOWN_ID
    """
    digits_only = re.fullmatch(r"\d+", value)
    if digits_only:
        n = len(value)
        if n == 9:
            return "CMND_9"
        if n == 12:
            return "CCCD_12"
        if n == 10:
            return "TAX_ID"
    if re.fullmatch(r"[A-Z]\d{6,8}", value.upper()):
        return "PASSPORT"
    return "UNKNOWN_ID"


# ─── Text helpers ─────────────────────────────────────────────────────────────

def _unicode_fold(value: str) -> str:
    """NFC + casefold, giữ nguyên dấu tiếng Việt."""
    return unicodedata.normalize("NFC", " ".join(value.split()).casefold())


def _ascii_fold(value: str) -> str:
    """Bỏ dấu hoàn toàn, trả về ASCII lowercase.

    Chỉ dùng cho so sánh phụ trợ (NA state), KHÔNG dùng làm
    giá trị identity chính (xem mục 5.1 — An vs Ân).
    """
    text = _unicode_fold(value).replace("đ", "d")
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def _parse_name_parts(raw: str) -> dict:
    """Tách surname / given_name / tokens từ tên Việt Nam."""
    tokens = raw.strip().split()
    if not tokens:
        return {"surname": "", "given_name": "", "tokens": []}
    return {
        "surname": tokens[0],
        "given_name": tokens[-1] if len(tokens) > 1 else "",
        "tokens": tokens,
    }


# ─── Field normalizers ────────────────────────────────────────────────────────

def normalize_phone(raw: str) -> tuple[str, str]:
    """Trả về (normalized, quality) — quality ∈ {VALID, INVALID, MISSING, PLACEHOLDER, SHARED}."""
    if not raw or not raw.strip():
        return "", "MISSING"
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return "", "INVALID"

    # Bỏ tiền tố quốc gia
    if digits.startswith("0084") and len(digits) >= 13:
        digits = "0" + digits[4:]
    elif digits.startswith("84") and len(digits) == 11:
        digits = "0" + digits[2:]

    # Legacy 11-digit → 10-digit (mục 5.2)
    if len(digits) == 11 and digits.startswith("0"):
        prefix4 = digits[:4]
        if prefix4 in _LEGACY_PHONE_MAP:
            digits = _LEGACY_PHONE_MAP[prefix4] + digits[4:]

    if digits in _PLACEHOLDER_PHONES:
        return digits, "PLACEHOLDER"
    if len(digits) not in range(9, 12):
        return digits, "INVALID"
    return digits, "VALID"


def normalize_email(raw: str) -> tuple[str, str]:
    """Trả về (normalized, quality) — quality ∈ {VALID, INVALID, MISSING, PLACEHOLDER, SHARED}."""
    if not raw or not raw.strip():
        return "", "MISSING"
    normalized = " ".join(raw.split()).casefold()
    if normalized in _PLACEHOLDER_EMAILS:
        return normalized, "PLACEHOLDER"
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", normalized):
        return normalized, "INVALID"
    local_part = normalized.split("@")[0]
    # Bỏ Gmail +tag và dots trước khi kiểm tra role prefix
    clean_local = local_part.split("+")[0].replace(".", "")
    if clean_local in _ROLE_EMAIL_PREFIXES:
        return normalized, "SHARED"
    return normalized, "VALID"


def parse_dob(raw: str) -> dict:
    """Parse DOB thành canonical form và metadata (mục 5.5).

    Hỗ trợ các định dạng:
      YYYY-MM-DD  /  YYYY/MM/DD  /  YYYY.MM.DD
      DD-MM-YYYY  /  DD/MM/YYYY  /  DD.MM.YYYY
      MM-DD-YYYY  /  MM/DD/YYYY  (chỉ dùng khi dd > 12 rõ ràng)
      YYYY                        (year-only)

    Trả về dict với các key:
      canonical     — "YYYY-MM-DD" hoặc "YYYY" nếu year-only, hoặc raw nếu parse fail
      year          — str 4 chữ số hoặc ""
      month         — str 2 chữ số hoặc ""
      day           — str 2 chữ số hoặc ""
      is_year_only  — bool
      is_ambiguous  — bool (cả day lẫn month đều ≤ 12, không biết thứ tự)
      quality       — VALID / INVALID / MISSING / PLACEHOLDER
    """
    empty = {"canonical": "", "year": "", "month": "", "day": "",
             "is_year_only": False, "is_ambiguous": False, "quality": "MISSING"}
    if not raw or not raw.strip():
        return empty

    raw = raw.strip()
    if raw in _PLACEHOLDER_DOBS:
        return {**empty, "canonical": raw, "quality": "PLACEHOLDER"}

    # ── Year-only: 4 chữ số
    if re.fullmatch(r"\d{4}", raw):
        yr = int(raw)
        if 1900 <= yr <= 2100:
            return {"canonical": raw, "year": raw, "month": "", "day": "",
                    "is_year_only": True, "is_ambiguous": False, "quality": "VALID"}
        return {**empty, "canonical": raw, "quality": "INVALID"}

    # ── Patterns 3 nhóm số
    sep = r"[-/.]"
    # YYYY-?-?
    m = re.fullmatch(rf"(\d{{4}}){sep}(\d{{1,2}}){sep}(\d{{1,2}})", raw)
    if m:
        yr, p2, p3 = m.group(1), m.group(2).zfill(2), m.group(3).zfill(2)
        return _build_dob_result(yr, p2, p3, raw, fmt="YMD")

    # ?-?-YYYY
    m = re.fullmatch(rf"(\d{{1,2}}){sep}(\d{{1,2}}){sep}(\d{{4}})", raw)
    if m:
        p1, p2, yr = m.group(1).zfill(2), m.group(2).zfill(2), m.group(3)
        return _build_dob_result(yr, p1, p2, raw, fmt="DMY")

    # Không parse được
    if re.search(r"\d{4}", raw):
        return {**empty, "canonical": raw, "year": re.search(r"\d{4}", raw).group(),
                "month": "", "day": "", "quality": "VALID"}
    return {**empty, "canonical": raw, "quality": "INVALID"}


def _build_dob_result(yr: str, a: str, b: str, raw: str, fmt: str) -> dict:
    """Xây kết quả DOB từ (year, field_a, field_b).

    fmt="YMD": a=month, b=day
    fmt="DMY": a=day_or_month, b=month_or_day — cần xét ambiguity.
    """
    def _valid_date(y: str, mo: str, d: str) -> bool:
        try:
            import datetime
            datetime.date(int(y), int(mo), int(d))
            return True
        except (ValueError, OverflowError):
            return False

    yr_int = int(yr)
    if not (1900 <= yr_int <= 2100):
        return {"canonical": raw, "year": yr, "month": "", "day": "",
                "is_year_only": False, "is_ambiguous": False, "quality": "INVALID"}

    if fmt == "YMD":
        mo, d = a, b
        if _valid_date(yr, mo, d):
            return {"canonical": f"{yr}-{mo}-{d}", "year": yr, "month": mo, "day": d,
                    "is_year_only": False, "is_ambiguous": False, "quality": "VALID"}
        return {"canonical": raw, "year": yr, "month": mo, "day": d,
                "is_year_only": False, "is_ambiguous": False, "quality": "INVALID"}

    # fmt == "DMY": a,b có thể là DD,MM hoặc MM,DD
    a_int, b_int = int(a), int(b)
    can_be_dm = _valid_date(yr, b, a)   # a=day, b=month
    can_be_md = _valid_date(yr, a, b)   # a=month, b=day

    if can_be_dm and not can_be_md:
        # Rõ ràng a=day, b=month (DD/MM)
        return {"canonical": f"{yr}-{b}-{a}", "year": yr, "month": b, "day": a,
                "is_year_only": False, "is_ambiguous": False, "quality": "VALID"}
    if can_be_md and not can_be_dm:
        # Rõ ràng a=month, b=day (MM/DD)
        return {"canonical": f"{yr}-{a}-{b}", "year": yr, "month": a, "day": b,
                "is_year_only": False, "is_ambiguous": False, "quality": "VALID"}
    if can_be_dm and can_be_md:
        # Ambiguous: cả hai đều hợp lệ (a≤12 và b≤12)
        # VN convention: DD/MM/YYYY → canonical dùng DD/MM
        return {"canonical": f"{yr}-{b}-{a}", "year": yr, "month": b, "day": a,
                "is_year_only": False, "is_ambiguous": True, "quality": "VALID"}
    return {"canonical": raw, "year": yr, "month": "", "day": "",
            "is_year_only": False, "is_ambiguous": False, "quality": "INVALID"}


def normalize_dob(raw: str) -> tuple[str, str]:
    """Trả về (canonical, quality).

    canonical = "YYYY-MM-DD" hoặc "YYYY" (year-only) hoặc raw nếu không parse được.
    quality   ∈ {VALID, INVALID, MISSING, PLACEHOLDER}.

    Dùng parse_dob() nếu cần metadata đầy đủ (is_year_only, is_ambiguous...).
    """
    parts = parse_dob(raw)
    return parts["canonical"], parts["quality"]


# ─── Public API ───────────────────────────────────────────────────────────────

def standardize(raw_fields: dict[str, str]) -> tuple[dict, list[str]]:
    """Chuẩn hóa raw fields, trả về (normalized_fields, quality_flags).

    normalized_fields gồm:
      name          — unicode NFC casefold (giữ dấu)
      name_ascii    — bỏ dấu hoàn toàn (chỉ dùng so sánh phụ)
      name_parts    — {"surname", "given_name", "tokens"}
      phone         — digits chuẩn hóa
      email         — lowercase
      address       — ascii fold (địa chỉ thường không có dấu quan trọng)
      dob           — raw strip
      government_id — digits/text stripped
      government_id_type — uppercase
      entity_type   — uppercase (PERSON / ORGANIZATION / UNKNOWN)
      _field_quality — dict per-field quality flag (dùng nội bộ pipeline)

    quality_flags — list các flag batch-level (backward compat DB).
    """
    raw_name = raw_fields.get("name", "") or ""
    raw_phone = raw_fields.get("phone", "") or ""
    raw_email = raw_fields.get("email", "") or ""
    raw_address = raw_fields.get("address", "") or ""
    raw_dob = raw_fields.get("dob", "") or ""
    raw_gov_id = raw_fields.get("government_id", "") or ""
    raw_gov_id_type = raw_fields.get("government_id_type", "") or ""
    raw_entity_type = (raw_fields.get("entity_type", "") or "PERSON").strip().upper()
    if raw_entity_type not in ("PERSON", "ORGANIZATION"):
        raw_entity_type = "UNKNOWN"

    # Name
    if not raw_name.strip():
        name_unicode, name_ascii = "", ""
        name_parts = {"surname": "", "given_name": "", "tokens": []}
        name_quality = "MISSING"
    else:
        name_unicode = _unicode_fold(raw_name)
        name_ascii = _ascii_fold(raw_name)
        name_parts = _parse_name_parts(raw_name)
        name_quality = "VALID"

    # Phone
    phone_norm, phone_quality = normalize_phone(raw_phone)

    # Email
    email_norm, email_quality = normalize_email(raw_email)

    # Address — parse province + ascii-fold (mục 5.4)
    if raw_address.strip():
        address_parts = parse_address(raw_address)
        address_norm = address_parts["normalized"]
        address_quality = "VALID"
    else:
        address_parts = parse_address("")
        address_norm = ""
        address_quality = "MISSING"
    # frozenset is not JSON-serializable — convert before storing
    address_parts = {**address_parts, "ward_ids": sorted(address_parts["ward_ids"])}

    # DOB — dùng parse_dob để lấy canonical + metadata (mục 5.5)
    dob_parts = parse_dob(raw_dob)
    dob_norm = dob_parts["canonical"]
    dob_quality = dob_parts["quality"]

    # Government ID — tự suy loại nếu không có (mục 5.6)
    gov_id_norm = re.sub(r"\s+", "", raw_gov_id).strip()
    gov_id_type = raw_gov_id_type.upper().strip()
    if gov_id_norm and not gov_id_type:
        gov_id_type = _infer_gov_id_type(gov_id_norm)
    gov_id_quality = "VALID" if gov_id_norm else "MISSING"

    field_quality = {
        "name": name_quality,
        "phone": phone_quality,
        "email": email_quality,
        "address": address_quality,
        "dob": dob_quality,
        "government_id": gov_id_quality,
    }

    normalized_fields = {
        "name": name_unicode,
        "name_ascii": name_ascii,
        "name_parts": name_parts,
        "phone": phone_norm,
        "email": email_norm,
        "address": address_norm,
        "address_parts": address_parts,
        "dob": dob_norm,
        "dob_parts": dob_parts,
        "government_id": gov_id_norm,
        "government_id_type": gov_id_type,
        "entity_type": raw_entity_type,
        "_field_quality": field_quality,
    }

    # Batch-level flags (backward compat — lưu vào quality_flags column)
    run_flags: list[str] = []
    if name_quality == "MISSING":
        run_flags.append("MISSING_NAME")
    if phone_quality == "MISSING" and email_quality == "MISSING":
        run_flags.append("NO_CONTACT_POINT")
    if email_quality == "INVALID":
        run_flags.append("INVALID_EMAIL_FORMAT")
    if phone_quality == "INVALID":
        run_flags.append("UNUSUAL_PHONE_LENGTH")
    if phone_quality == "PLACEHOLDER":
        run_flags.append("PLACEHOLDER_PHONE")
    if email_quality == "PLACEHOLDER":
        run_flags.append("PLACEHOLDER_EMAIL")

    return normalized_fields, run_flags
