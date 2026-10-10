import re
import unicodedata
from datetime import date

FIELDS = ("name", "phone", "email", "address")
OPTIONAL_FIELDS = ("dob", "government_id")

_LEGACY_PHONE_MAP = {
    "0162": "032", "0163": "033", "0164": "034", "0165": "035",
    "0166": "036", "0167": "037", "0168": "038", "0169": "039",
    "0123": "083", "0124": "084", "0125": "085", "0127": "081",
    "0129": "082", "0120": "070", "0121": "079", "0122": "077",
    "0126": "076", "0128": "078", "0186": "056", "0188": "058",
    "0199": "059",
}
_PLACEHOLDER_PHONES = frozenset({
    "0000000000", "0123456789", "0999999999", "1111111111",
    "9999999999", "0111111111", "0000000", "1234567890",
})
_PLACEHOLDER_EMAILS = frozenset({
    "test@test.com", "noemail@noemail.com", "a@a.com", "none@none.com",
    "test@example.com", "user@example.com", "abc@abc.com", "no@email.com",
    "noemail@", "email@email.com", "nomail@nomail.com", "fake@fake.com",
    "xxx@xxx.com", "aaa@aaa.com", "email@domain.com",
})
# §5.3: role / shared email prefixes — not a personal anchor
_ROLE_EMAIL_PREFIXES = frozenset({
    "info", "admin", "ketoan", "sales", "support", "contact",
    "office", "hr", "help", "noreply", "no-reply", "noreply",
    "marketing", "billing", "invoice", "accounting", "finance",
    "service", "cs", "cskh", "cham-soc-khach-hang",
    "reception", "hotline", "letan", "webmaster", "postmaster",
})

# Canonical date separators for DOB normalization
_DATE_SEP = re.compile(r"[/\-\.]")
# CCCD/CMND/passport-style: digits only, 9 or 12 digits, or alphanumeric passport
_GOV_ID_DIGITS = re.compile(r"^\d{9}$|^\d{12}$")
_GOV_ID_PASSPORT = re.compile(r"^[A-Z]\d{7,8}$", re.IGNORECASE)


def fold_text(value: str) -> str:
    text = unicodedata.normalize("NFC", " ".join(value.split()).casefold())
    decomposed = unicodedata.normalize("NFD", text.replace("đ", "d"))
    return "".join(char for char in decomposed if unicodedata.category(char) != "Mn")


def tokenize_vietnamese_name(name: str) -> dict:
    """Tokenize a Vietnamese name into surname, middle_name, and given_name.
    
    Args:
        name: A full name string (expected to be NFC + lowercase/casefold).
        
    Returns:
        A dict with 'surname', 'middle_name', 'given_name'.
    """
    tokens = [t for t in name.split() if t]
    if not tokens:
        return {"surname": "", "middle_name": "", "given_name": ""}
    if len(tokens) == 1:
        return {"surname": tokens[0], "middle_name": "", "given_name": tokens[0]}
    if len(tokens) == 2:
        return {"surname": tokens[0], "middle_name": "", "given_name": tokens[1]}
    return {
        "surname": tokens[0],
        "middle_name": " ".join(tokens[1:-1]),
        "given_name": tokens[-1]
    }


def normalize_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    # Strip international prefix: +84 / 0084
    if digits.startswith("0084") and len(digits) > 10:
        digits = "0" + digits[4:]
    if digits.startswith("84") and len(digits) == 11:
        digits = "0" + digits[2:]
    # §T9: legacy 11-digit Vietnam mobile numbers → 10-digit (2018 renumbering)
    # First try explicit map (covers Viettel 016x→03x, Mobi 012x→07x etc.)
    if len(digits) == 11 and digits[0] == "0" and digits[:4] in _LEGACY_PHONE_MAP:
        digits = _LEGACY_PHONE_MAP[digits[:4]] + digits[4:]
    # Generic fallback: 11-digit 01x prefix that starts with 01[2-9]
    # The 2018 plan removed all 01x prefixes; any remaining → drop leading "1"
    elif len(digits) == 11 and digits.startswith("01") and digits[2] in "23456789":
        digits = "0" + digits[2:]
    return digits


def normalize_dob(value: str) -> tuple[str, str]:
    """Return (normalized_dob, quality_flag).

    Normalized form: YYYY-MM-DD when full date, YYYY-MM when partial, YYYY when year-only.
    quality_flag: VALID | PARTIAL | INVALID | MISSING
    """
    raw = " ".join(value.split())
    if not raw:
        return "", "MISSING"
    parts = _DATE_SEP.split(raw)
    # Detect DD/MM/YYYY vs YYYY/MM/DD by part length
    if len(parts) == 3:
        a, b, c = parts
        if len(a) == 4:  # YYYY-MM-DD
            year, month, day = a, b, c
        else:  # DD-MM-YYYY
            day, month, year = a, b, c
        try:
            y, m, d = int(year), int(month), int(day)
            if 1900 <= y <= 2100 and 1 <= m <= 12 and 1 <= d <= 31:
                date(y, m, d)
                return f"{y:04d}-{m:02d}-{d:02d}", "VALID"
        except ValueError:
            pass
        return raw, "INVALID"
    if len(parts) == 2:
        a, b = parts
        if len(a) == 4:
            year, month = a, b
        else:
            month, year = a, b
        try:
            y, m = int(year), int(month)
            if 1900 <= y <= 2100 and 1 <= m <= 12:
                return f"{y:04d}-{m:02d}", "PARTIAL"
        except ValueError:
            pass
        return raw, "INVALID"
    if len(parts) == 1 and re.fullmatch(r"\d{4}", raw):
        y = int(raw)
        if 1900 <= y <= 2100:
            return raw, "PARTIAL"
    return raw, "INVALID"


def normalize_government_id(value: str) -> tuple[str, str]:
    """Return (normalized_gov_id, quality_flag).

    quality_flag: VALID | INVALID | MISSING
    """
    raw = re.sub(r"\s+", "", value).upper()
    if not raw:
        return "", "MISSING"
    if _GOV_ID_DIGITS.match(raw) or _GOV_ID_PASSPORT.match(raw):
        return raw, "VALID"
    return raw, "INVALID"


def _infer_gov_id_type(gov_id: str) -> str:
    """Infer Vietnamese government ID type from the normalized value.

    Returns: CMND_9 | CCCD_12 | PASSPORT | TAX_ID | UNKNOWN
    """
    v = re.sub(r"\s+", "", gov_id).upper()
    if re.fullmatch(r"\d{9}", v):
        return "CMND_9"
    if re.fullmatch(r"\d{12}", v):
        return "CCCD_12"
    if re.fullmatch(r"[A-Z]\d{7,8}", v):
        return "PASSPORT"
    if re.fullmatch(r"\d{10}", v):
        return "TAX_ID"
    return "UNKNOWN"


def parse_dob(value: str) -> dict:
    """Parse a date-of-birth string into a structured dict.

    Returns:
        canonical (str): YYYY-MM-DD, YYYY-MM, or YYYY
        year/month/day (str): individual parts ("" if unavailable)
        quality: VALID | PARTIAL | PLACEHOLDER | INVALID | MISSING
        is_year_only (bool)
        is_ambiguous (bool): True when day/month are both ≤12 and order is unclear
    """
    _PLACEHOLDER_DOBS = frozenset({"01/01/1900", "1900-01-01", "00/00/0000", "0000-00-00"})

    raw = " ".join(value.split()) if value else ""
    if not raw:
        return {"canonical": "", "year": "", "month": "", "day": "",
                "quality": "MISSING", "is_year_only": False, "is_ambiguous": False}

    if raw in _PLACEHOLDER_DOBS:
        return {"canonical": raw, "year": "", "month": "", "day": "",
                "quality": "PLACEHOLDER", "is_year_only": False, "is_ambiguous": False}

    parts = _DATE_SEP.split(raw)
    if len(parts) == 3:
        a, b, c = parts
        if len(a) == 4:
            year, month, day = a, b, c
            is_ambiguous = False
        else:
            # DD/MM/YYYY — VN convention; flag ambiguous when both day and month ≤ 12
            day, month, year = a, b, c
            try:
                is_ambiguous = int(a) <= 12 and int(b) <= 12
            except ValueError:
                is_ambiguous = False
        try:
            y, m, d = int(year), int(month), int(day)
            if 1900 <= y <= 2100 and 1 <= m <= 12 and 1 <= d <= 31:
                date(y, m, d)
                canonical = f"{y:04d}-{m:02d}-{d:02d}"
                return {"canonical": canonical, "year": f"{y:04d}", "month": f"{m:02d}", "day": f"{d:02d}",
                        "quality": "VALID", "is_year_only": False, "is_ambiguous": is_ambiguous}
        except ValueError:
            pass
        return {"canonical": raw, "year": "", "month": "", "day": "",
                "quality": "INVALID", "is_year_only": False, "is_ambiguous": False}

    if len(parts) == 2:
        a, b = parts
        year, month = (a, b) if len(a) == 4 else (b, a)
        try:
            y, m = int(year), int(month)
            if 1900 <= y <= 2100 and 1 <= m <= 12:
                return {"canonical": f"{y:04d}-{m:02d}", "year": f"{y:04d}", "month": f"{m:02d}", "day": "",
                        "quality": "VALID", "is_year_only": False, "is_ambiguous": False}
        except ValueError:
            pass
        return {"canonical": raw, "year": "", "month": "", "day": "",
                "quality": "INVALID", "is_year_only": False, "is_ambiguous": False}

    if len(parts) == 1 and re.fullmatch(r"\d{4}", raw):
        y = int(raw)
        if 1900 <= y <= 2100:
            return {"canonical": raw, "year": raw, "month": "", "day": "",
                    "quality": "VALID", "is_year_only": True, "is_ambiguous": False}

    return {"canonical": raw, "year": "", "month": "", "day": "",
            "quality": "INVALID", "is_year_only": False, "is_ambiguous": False}


# ── Province abbreviation map (fold_text-normalized keys) ────────────────────
_PROVINCE_ABBREV: dict[str, str] = {
    "hcm": "ho chi minh", "tphcm": "ho chi minh", "tp hcm": "ho chi minh",
    "hn": "ha noi", "tp hn": "ha noi",
    "dn": "da nang", "tp dn": "da nang",
    "hp": "hai phong", "tp hp": "hai phong",
    "ct": "can tho", "tp ct": "can tho",
}

_PROVINCE_FULL: frozenset[str] = frozenset({
    "ha noi", "ho chi minh", "da nang", "hai phong", "can tho",
    "an giang", "ba ria vung tau", "bac giang", "bac kan", "bac lieu",
    "bac ninh", "ben tre", "binh dinh", "binh duong", "binh phuoc",
    "binh thuan", "ca mau", "cao bang", "dak lak", "dak nong",
    "dien bien", "dong nai", "dong thap", "gia lai", "ha giang",
    "ha nam", "ha tinh", "hai duong", "hau giang", "hoa binh",
    "hung yen", "khanh hoa", "kien giang", "kon tum", "lai chau",
    "lam dong", "lang son", "lao cai", "long an", "nam dinh",
    "nghe an", "ninh binh", "ninh thuan", "phu tho", "phu yen",
    "quang binh", "quang nam", "quang ngai", "quang ninh", "quang tri",
    "soc trang", "son la", "tay ninh", "thai binh", "thai nguyen",
    "thanh hoa", "thua thien hue", "tien giang", "tra vinh",
    "tuyen quang", "vinh long", "vinh phuc", "yen bai",
})


def _extract_ward_token(folded_address: str) -> str:
    """Extract the ward token from a fold_text-normalized address string.

    Returns the first token that starts with 'phuong ' or 'xa ' or 'thi tran ',
    or "" if none found.
    """
    for token in (t.strip() for t in re.split(r",|;", folded_address) if t.strip()):
        if re.match(r"^(phuong|xa|thi tran)\s+", token):
            return token
    return ""


def parse_address(address: str) -> dict:
    """Parse an address string into structured parts.

    Returns:
        province (str): normalized province name (fold_text), "" if not found
        street_fragment (str): remainder after removing province token
    """
    if not address:
        return {"province": "", "street_fragment": "", "needs_hitl": False}

    folded = fold_text(address)
    tokens = [t.strip() for t in re.split(r",|;", folded) if t.strip()]

    # Try each token (right-to-left, province usually last)
    province = ""
    province_token_idx = -1
    for idx in range(len(tokens) - 1, -1, -1):
        tok = tokens[idx]
        # Remove leading "tinh ", "tp ", "thanh pho ", "huyen ", "quan " prefixes
        stripped = re.sub(r"^(tinh|tp|thanh pho|huyen|quan|phuong|xa|thi xa|thi tran)\s+", "", tok).strip()
        if stripped in _PROVINCE_ABBREV:
            province = _PROVINCE_ABBREV[stripped]
            province_token_idx = idx
            break
        if stripped in _PROVINCE_FULL:
            province = stripped
            province_token_idx = idx
            break
        if tok in _PROVINCE_ABBREV:
            province = _PROVINCE_ABBREV[tok]
            province_token_idx = idx
            break
        if tok in _PROVINCE_FULL:
            province = tok
            province_token_idx = idx
            break

    street_parts = [t for i, t in enumerate(tokens) if i != province_token_idx]
    street_fragment = ", ".join(street_parts)

    return {"province": province, "street_fragment": street_fragment, "needs_hitl": False}


def normalize_org_name(name: str) -> dict:
    """Normalize organization name and extract legal form.
    
    Returns:
        {"org_name_core": "...", "legal_form": "..."}
    """
    legal_forms = [
        "tnhh mtv", "tnhh", "co phan", "cp", "co., ltd", "jsc", "llc", "inc", "tap doan", "tong cong ty"
    ]
    core = fold_text(name)
    found_form = ""
    
    core = re.sub(r"^(cong ty|cty)\s+", "", core).strip()
    
    # Combine into a single regex to match the first occurrence from left to right
    pattern = r"(^|\s)(" + "|".join(re.escape(f) for f in legal_forms) + r")($|\s)"
    match = re.search(pattern, core)
    if match:
        form = match.group(2)
        if form == "cp":
            found_form = "CO PHAN"
        else:
            found_form = form.upper()
        core = core[:match.start()] + " " + core[match.end():]
            
    core = " ".join(core.split())
    return {"org_name_core": core, "legal_form": found_form}


def standardize(raw_fields: dict[str, str], entity_type: str = "PERSON") -> tuple[dict, list[str]]:
    raw = {field: raw_fields.get(field, "") or "" for field in FIELDS}
    raw_dob = raw_fields.get("dob", "") or ""
    raw_gov_id = raw_fields.get("government_id", "") or ""

    normalized: dict = {
        "name": fold_text(raw["name"]),
        "name_unicode": unicodedata.normalize("NFC", " ".join(raw["name"].split()).casefold()),
        "address": fold_text(raw["address"]),
    }
    
    if entity_type == "ORGANIZATION":
        normalized["_org_parts"] = normalize_org_name(raw["name"])
    else:
        normalized["_name_parts"] = tokenize_vietnamese_name(normalized["name_unicode"])

    raw_phones = [p.strip() for p in re.split(r"[,;]", raw["phone"]) if p.strip()]
    phones = []
    for p in raw_phones:
        norm_p = normalize_phone(p)
        if not norm_p:
            q = "MISSING"
        elif norm_p in _PLACEHOLDER_PHONES:
            q = "PLACEHOLDER"
        elif len(norm_p) not in range(9, 13):
            q = "INVALID"
        else:
            q = "VALID"
        phones.append({"value": norm_p, "quality": q, "is_primary": False})
        
    if phones:
        primary_idx = next((i for i, p in enumerate(phones) if p["quality"] == "VALID"), 0)
        phones[primary_idx]["is_primary"] = True
        normalized["phone"] = phones[primary_idx]["value"]
        phone_quality = phones[primary_idx]["quality"]
    else:
        normalized["phone"] = ""
        phone_quality = "MISSING"
        
    normalized["phones"] = phones

    raw_emails = [e.strip() for e in re.split(r"[,;]", raw["email"]) if e.strip()]
    emails = []
    for e in raw_emails:
        norm_e = " ".join(e.split()).casefold()
        is_valid_format = bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", norm_e))
        if not norm_e:
            q = "MISSING"
        elif not is_valid_format:
            q = "INVALID"
        elif norm_e in _PLACEHOLDER_EMAILS:
            q = "PLACEHOLDER"
        elif norm_e.split("@", 1)[0].split("+", 1)[0].replace(".", "") in _ROLE_EMAIL_PREFIXES:
            q = "SHARED"
        else:
            q = "VALID"
        emails.append({"value": norm_e, "quality": q, "is_primary": False})
        
    if emails:
        primary_idx = next((i for i, e in enumerate(emails) if e["quality"] == "VALID"), 0)
        emails[primary_idx]["is_primary"] = True
        normalized["email"] = emails[primary_idx]["value"]
        email_quality = emails[primary_idx]["quality"]
    else:
        normalized["email"] = ""
        email_quality = "MISSING"
        
    normalized["emails"] = emails
    # _name_parts already set for PERSON at line 354; ensure ORGANIZATION also gets it for cross-entity safety
    if "_name_parts" not in normalized:
        normalized["_name_parts"] = tokenize_vietnamese_name(normalized["name_unicode"])

    dob_norm, dob_quality = normalize_dob(raw_dob)
    gov_id_norm, gov_id_quality = normalize_government_id(raw_gov_id)
    if raw_dob:
        normalized["dob"] = dob_norm
    if raw_gov_id:
        normalized["government_id"] = gov_id_norm

    # Pass-through government_id_type if provided (used by scoring)
    gov_id_type = raw_fields.get("government_id_type", "") or ""
    if gov_id_type:
        normalized["government_id_type"] = gov_id_type
    elif gov_id_norm:
        normalized["government_id_type"] = _infer_gov_id_type(gov_id_norm)

    # Structured address parts with ward resolution
    addr_parts = parse_address(raw["address"])
    from masterone.mdm.pipeline.address_mapping import resolve_ward, MAPPING_VERSION  # local import avoids circular
    ward_match = _extract_ward_token(normalized["address"])
    if ward_match:
        wr = resolve_ward(ward_match)
        addr_parts["ward_status"] = wr.status
        addr_parts["ward_ids"] = list(wr.unit_ids)
        addr_parts["needs_hitl"] = wr.status == "ambiguous"
    else:
        addr_parts["ward_status"] = "unresolved"
        addr_parts["ward_ids"] = []
        addr_parts["needs_hitl"] = False
        
    addr_parts["mapping_version"] = MAPPING_VERSION
    normalized["address_parts"] = addr_parts

    quality: dict[str, str] = {
        "name": "VALID" if normalized["name"] else "MISSING",
        "phone": phone_quality,
        "email": email_quality,
        "address": "VALID" if normalized["address"] else "MISSING",
        "dob": dob_quality,
        "government_id": gov_id_quality,
    }
    normalized["_field_quality"] = quality

    flags = []
    if not normalized["name"]:
        flags.append("MISSING_NAME")
    if not normalized["phone"] and not normalized["email"]:
        flags.append("NO_CONTACT_POINT")
    if quality["email"] == "INVALID":
        flags.append("INVALID_EMAIL_FORMAT")
    if quality["phone"] == "INVALID":
        flags.append("UNUSUAL_PHONE_LENGTH")
    if quality["phone"] == "PLACEHOLDER":
        flags.append("PLACEHOLDER_PHONE")
    if quality["email"] in {"PLACEHOLDER", "SHARED"}:
        flags.append("SHARED_OR_PLACEHOLDER_EMAIL")
    if raw_dob and dob_quality == "INVALID":
        flags.append("INVALID_DOB_FORMAT")
    if raw_gov_id and gov_id_quality == "INVALID":
        flags.append("INVALID_GOV_ID_FORMAT")
    return normalized, flags
