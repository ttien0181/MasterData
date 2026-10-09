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


import json
from pathlib import Path

# Load from JSON file
_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent.parent.parent
_MAPPING_FILE = _BACKEND_DIR / "data" / "reference" / "address" / "mappings" / "mapping_entries_v1.json"

_WARD_MAP: dict[str, WardResolution] = {}
MAPPING_VERSION = "unknown"

try:
    with open(_MAPPING_FILE, "r", encoding="utf-8") as f:
        entries = json.load(f)
        if entries and isinstance(entries, list):
            MAPPING_VERSION = entries[0].get("mapping_dataset_version", "unknown")
            
        temp_map = {}
        for entry in entries:
            src_name = _fold(entry.get("source_unit_name", ""))
            # Also fold without prefix (e.g., "phuong truc bach" -> "truc bach")
            # to handle aliases if they aren't explicitly in the JSON
            target_id = entry.get("target_unit_id")
            if src_name and target_id:
                if src_name not in temp_map:
                    temp_map[src_name] = set()
                temp_map[src_name].add(target_id)
                
                # Add a variant without administrative prefix
                no_prefix = src_name.replace("phuong ", "").replace("xa ", "").replace("thi tran ", "").strip()
                if no_prefix and no_prefix != src_name:
                    if no_prefix not in temp_map:
                        temp_map[no_prefix] = set()
                    temp_map[no_prefix].add(target_id)
                
        for src, target_ids in temp_map.items():
            status = "resolved" if len(target_ids) == 1 else "ambiguous"
            _WARD_MAP[src] = WardResolution(status, frozenset(target_ids))
except FileNotFoundError:
    pass

def resolve_ward(ward_name: str) -> WardResolution:
    """Resolve a ward name string to current administrative unit IDs.

    Applies fold_text normalization before lookup.
    Returns WardResolution(status="not_found", unit_ids=frozenset()) when unknown.
    """
    key = _fold(ward_name)
    return _WARD_MAP.get(key, WardResolution("not_found", frozenset()))
