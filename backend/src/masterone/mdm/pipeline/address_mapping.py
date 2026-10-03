"""Address mapping — phường/xã cũ → mới theo NQ 1656/NQ-UBTVQH15 (mục 5.4).

Tải một lần khi import (lazy singleton). Hai lookup table:
  alias_to_id  : "phuong truc bach" → "00004"        (alias ascii → unit_id)
  old_to_new   : "00004" → frozenset({"00005"})       (old unit_id → new unit_ids)
               : "00016" → frozenset({"00004","00016"}) (ambiguous: nhiều targets)

Trạng thái resolve:
  "resolved"  — 1 target duy nhất
  "ambiguous" — nhiều targets → cần HITL
  "unknown"   — không tìm được trong data
"""

from __future__ import annotations

import csv
import json
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple

# Vị trí file data — đường dẫn tương đối từ repo root
_DATA_DIR = Path(__file__).resolve().parents[5] / "data" / "address"
_ALIASES_FILE = _DATA_DIR / "administrative_aliases_v1.csv"
_MAPPING_FILE = _DATA_DIR / "mapping_entries_v1.json"


class WardResolution(NamedTuple):
    unit_ids: frozenset[str]   # new unit_id(s) sau sáp nhập
    status: str                # "resolved" | "ambiguous" | "unknown"
    source_unit_id: str        # unit_id gốc trước sáp nhập (hoặc "" nếu unknown)


_UNKNOWN = WardResolution(frozenset(), "unknown", "")


def _ascii_fold(text: str) -> str:
    nfd = unicodedata.normalize("NFD", text)
    return "".join(c for c in nfd if unicodedata.category(c) != "Mn").lower()


class _AddressMappingIndex:
    """Singleton index — load một lần, tái dùng cho toàn bộ pipeline."""

    def __init__(self) -> None:
        # alias (ascii lowercase) → source unit_id
        self._alias_to_id: dict[str, str] = {}
        # source unit_id → (frozenset[new_unit_id], status)
        self._old_to_new: dict[str, tuple[frozenset[str], str]] = {}
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return

        # ── 1. Build alias → unit_id từ CSV ─────────────────────────────────
        if _ALIASES_FILE.exists():
            with _ALIASES_FILE.open(encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    uid = row.get("unit_id", "").strip()
                    alias_raw = row.get("alias", "").strip()
                    if uid and alias_raw:
                        key = _ascii_fold(alias_raw)
                        # Chỉ ghi nếu chưa có — tránh collision giữa tỉnh khác nhau
                        # (collision giải quyết bằng context province ở bước gọi)
                        if key not in self._alias_to_id:
                            self._alias_to_id[key] = uid

        # ── 2. Build old_unit_id → (new_unit_ids, status) từ JSON ────────────
        if _MAPPING_FILE.exists():
            with _MAPPING_FILE.open(encoding="utf-8") as f:
                entries = json.load(f)
            for entry in entries:
                src_id = entry.get("source_unit_id", "").strip()
                status = entry.get("mapping_status", "unknown")
                if not src_id:
                    continue

                if status == "resolved":
                    tgt_id = entry.get("target_unit_id", "").strip()
                    if tgt_id:
                        self._old_to_new[src_id] = (frozenset({tgt_id}), "resolved")
                elif status == "ambiguous":
                    # Có thể đã có entry resolved trước — ambiguous không ghi đè resolved
                    if src_id not in self._old_to_new:
                        # mapping_entries_v1 có flat target_unit_id cho ambiguous
                        tgt_id = entry.get("target_unit_id", "").strip()
                        existing = self._old_to_new.get(src_id)
                        if existing:
                            existing_ids, _ = existing
                            new_ids = existing_ids | ({tgt_id} if tgt_id else set())
                            self._old_to_new[src_id] = (frozenset(new_ids), "ambiguous")
                        elif tgt_id:
                            self._old_to_new[src_id] = (frozenset({tgt_id}), "ambiguous")

        self._loaded = True

    def resolve(self, ward_text: str) -> WardResolution:
        """Resolve tên phường/xã → WardResolution.

        ward_text: chuỗi tên phường/xã (có dấu hoặc không, bất kỳ cách viết).
        """
        self._load()
        if not ward_text or not ward_text.strip():
            return _UNKNOWN

        key = _ascii_fold(ward_text.strip())
        unit_id = self._alias_to_id.get(key)
        if not unit_id:
            return _UNKNOWN

        mapping = self._old_to_new.get(unit_id)
        if not mapping:
            # unit_id tồn tại nhưng không có mapping → đây là đơn vị mới, không cần map
            return WardResolution(frozenset({unit_id}), "resolved", unit_id)

        new_ids, status = mapping
        return WardResolution(new_ids, status, unit_id)


# ─── Public singleton ────────────────────────────────────────────────────────
_index = _AddressMappingIndex()


def resolve_ward(ward_text: str) -> WardResolution:
    """Resolve tên phường/xã → WardResolution (unit_ids, status, source_unit_id).

    status == "ambiguous" → có nhiều phường mới có thể map → cần HITL.
    status == "unknown"   → không nhận ra tên phường → bỏ qua, dùng fuzzy.
    status == "resolved"  → 1 target duy nhất, dùng được.
    """
    return _index.resolve(ward_text)


def ward_overlap(ids_a: frozenset[str], ids_b: frozenset[str]) -> bool:
    """True nếu hai tập ward_ids có giao (tương đồng địa chỉ có thể)."""
    return bool(ids_a & ids_b)
