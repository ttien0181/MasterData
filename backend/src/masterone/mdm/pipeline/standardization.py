import re
import unicodedata


FIELDS = ("name", "phone", "email", "address")


def fold_text(value: str) -> str:
    text = unicodedata.normalize("NFC", " ".join(value.split()).casefold())
    decomposed = unicodedata.normalize("NFD", text.replace("đ", "d"))
    return "".join(char for char in decomposed if unicodedata.category(char) != "Mn")


def normalize_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    if digits.startswith("0084") and len(digits) > 10:
        return "0" + digits[4:]
    if digits.startswith("84") and len(digits) > 10:
        return "0" + digits[2:]
    return digits


def standardize(raw_fields: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    raw = {field: raw_fields.get(field, "") or "" for field in FIELDS}
    normalized = {
        "name": fold_text(raw["name"]),
        "phone": normalize_phone(raw["phone"]),
        "email": " ".join(raw["email"].split()).casefold(),
        "address": fold_text(raw["address"]),
    }
    flags = []
    if not normalized["name"]:
        flags.append("MISSING_NAME")
    if not normalized["phone"] and not normalized["email"]:
        flags.append("NO_CONTACT_POINT")
    if normalized["email"] and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", normalized["email"]):
        flags.append("INVALID_EMAIL_FORMAT")
    if normalized["phone"] and len(normalized["phone"]) not in range(9, 13):
        flags.append("UNUSUAL_PHONE_LENGTH")
    return normalized, flags
