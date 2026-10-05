"""Ward/commune resolution for Vietnamese administrative boundary merges.

Maps pre-2025 ward names to current administrative unit IDs.
Supports resolved (1-to-1) and ambiguous (1-to-many) cases.
"""

from dataclasses import dataclass, field

import unicodedata as _uc


def _fold(value: str) -> str:
    text = _uc.normalize("NFC", " ".join(value.split()).casefold())
    decomposed = _uc.normalize("NFD", text.replace("đ", "d"))
    return "".join(c for c in decomposed if _uc.category(c) != "Mn")


@dataclass(frozen=True)
class WardResolution:
    status: str          # "resolved" | "ambiguous" | "not_found"
    unit_ids: frozenset  # set of current admin unit IDs


# Ward mapping: folded_name → (status, frozenset of unit_ids)
# Unit IDs are synthetic but stable identifiers for current wards.
# Source: Vietnam 2025 administrative boundary reform.
_WARD_MAP: dict[str, WardResolution] = {
    # Hà Nội — Ba Đình merges
    "phuong truc bach": WardResolution("resolved", frozenset({"HN-BD-001"})),
    "truc bach": WardResolution("resolved", frozenset({"HN-BD-001"})),
    "phuong quan thanh": WardResolution("resolved", frozenset({"HN-BD-001"})),
    "quan thanh": WardResolution("resolved", frozenset({"HN-BD-001"})),
    "phuong ba dinh": WardResolution("resolved", frozenset({"HN-BD-001"})),
    "ba dinh": WardResolution("resolved", frozenset({"HN-BD-001"})),
    # Ambiguous example: Ngọc Hà maps to multiple possible wards
    "phuong ngoc ha": WardResolution("ambiguous", frozenset({"HN-BD-002", "HN-BD-003"})),
    "ngoc ha": WardResolution("ambiguous", frozenset({"HN-BD-002", "HN-BD-003"})),
}


def resolve_ward(ward_name: str) -> WardResolution:
    """Resolve a ward name string to current administrative unit IDs.

    Applies fold_text normalization before lookup.
    Returns WardResolution(status="not_found", unit_ids=frozenset()) when unknown.
    """
    key = _fold(ward_name)
    return _WARD_MAP.get(key, WardResolution("not_found", frozenset()))
