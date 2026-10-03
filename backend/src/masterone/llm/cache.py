"""LLM Cache — in-memory với TTL, key theo SHA256.

Cache key semantic:
  SHA256(task_type + field + canonical_left + canonical_right + model + prompt_version + schema_version)

Canonical pair ordering: sort(left, right) để A→B và B→A cùng 1 entry.

Phase L1: in-memory dict với TTL.
Phase L4: Redis / PostgreSQL hot cache.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Any


@dataclass
class _CacheEntry:
    value: Any
    expires_at: float


class LLMCache:
    """In-memory TTL cache. Thread-safe cho single-process (GIL).
    Async-safe: không có blocking I/O.
    """

    def __init__(self, ttl_seconds: int = 604800) -> None:
        self._ttl = ttl_seconds
        self._store: dict[str, _CacheEntry] = {}
        self._hits = 0
        self._misses = 0

    # ─── Public API ────────────────────────────────────────────────────────────

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None or time.monotonic() > entry.expires_at:
            if entry is not None:
                del self._store[key]
            self._misses += 1
            return None
        self._hits += 1
        return entry.value

    def set(self, key: str, value: Any) -> None:
        self._store[key] = _CacheEntry(
            value=value,
            expires_at=time.monotonic() + self._ttl,
        )

    def stats(self) -> dict:
        total = self._hits + self._misses
        return {
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": self._hits / total if total else 0.0,
            "size": len(self._store),
        }

    def clear(self) -> None:
        self._store.clear()
        self._hits = 0
        self._misses = 0

    # ─── Key builders ──────────────────────────────────────────────────────────

    @staticmethod
    def semantic_key(
        *,
        field: str,
        left_value: str,
        right_value: str,
        model: str,
        prompt_version: str,
        schema_version: str,
    ) -> str:
        """Canonical: sort(left, right) để A,B và B,A cùng key."""
        lo, hi = sorted([left_value.strip().lower(), right_value.strip().lower()])
        raw = "\x00".join([
            "semantic", field, lo, hi, model, prompt_version, schema_version,
        ])
        return hashlib.sha256(raw.encode()).hexdigest()

    @staticmethod
    def explanation_key(
        *,
        pair_id: str,
        comparison_signature: str,
        policy_version: str,
        prompt_version: str,
        model: str,
    ) -> str:
        raw = "\x00".join([
            "explanation", pair_id, comparison_signature,
            policy_version, prompt_version, model,
        ])
        return hashlib.sha256(raw.encode()).hexdigest()


# ─── Singleton ────────────────────────────────────────────────────────────────

_semantic_cache: LLMCache | None = None
_explanation_cache: LLMCache | None = None


def get_semantic_cache(ttl_seconds: int = 604800) -> LLMCache:
    global _semantic_cache
    if _semantic_cache is None:
        _semantic_cache = LLMCache(ttl_seconds)
    return _semantic_cache


def get_explanation_cache(ttl_seconds: int = 604800) -> LLMCache:
    global _explanation_cache
    if _explanation_cache is None:
        _explanation_cache = LLMCache(ttl_seconds)
    return _explanation_cache


def reset_caches() -> None:
    """Dùng trong tests."""
    global _semantic_cache, _explanation_cache
    _semantic_cache = None
    _explanation_cache = None
