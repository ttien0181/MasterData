"""Explanation Service — lazy, cached, không block Review Queue.

Flow pair explanation:
  1. Check cache (in-memory, key = pair_id + comparison_signature + policy_version + prompt_version + model)
  2. HIT → return immediately
  3. MISS → gọi LLM, validate, cache, return
  4. LLM fail → return status=FAILED (UI vẫn hoạt động, hiện deterministic evidence)

Flow group explanation:
  - Backend aggregate stats trước
  - LLM chỉ diễn giải pattern
  - Cache theo group/subgroup/signature

UI KHÔNG chờ explanation để render Review Queue.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

from pydantic import ValidationError

from masterone.llm.cache import LLMCache, get_explanation_cache
from masterone.llm.client import LLMClient, LLMError, get_llm_client
from masterone.llm.config import LLMSettings, get_llm_settings
from masterone.llm.contracts import ExplanationInput, ExplanationOutput
from masterone.llm.explanation.prompt import (
    GROUP_PROMPT_VERSION,
    PROMPT_VERSION,
    build_group_messages,
    build_pair_messages,
)
from masterone.llm.explanation.schema import (
    EXPLANATION_SCHEMA,
    GROUP_EXPLANATION_SCHEMA,
    POLICY_VERSION,
    SCHEMA_VERSION,
)

log = logging.getLogger(__name__)


class ExplanationStatus(str, Enum):
    READY = "READY"
    PROCESSING = "PROCESSING"
    FAILED = "FAILED"
    DISABLED = "DISABLED"


@dataclass
class ExplanationResult:
    status: ExplanationStatus
    source: str  # "CACHE" | "LLM" | "FALLBACK" | "DISABLED"
    explanation: dict | None = None
    error_code: str | None = None


class ExplanationService:
    """Lazy on-demand explanation cho individual pair."""

    def __init__(
        self,
        *,
        settings: LLMSettings | None = None,
        client: LLMClient | None = None,
        cache: LLMCache | None = None,
    ) -> None:
        self._settings = settings or get_llm_settings()
        self._client = client or get_llm_client(self._settings)
        self._cache = cache or get_explanation_cache(self._settings.cache_ttl_seconds)

    async def get_or_generate(self, input_data: ExplanationInput) -> ExplanationResult:
        """Trả về explanation — từ cache hoặc gọi LLM. Không bao giờ raise."""
        if not self._settings.enabled or not self._settings.explanation_enabled:
            return ExplanationResult(status=ExplanationStatus.DISABLED, source="DISABLED")

        cache_key = LLMCache.explanation_key(
            pair_id=input_data.pair_id,
            comparison_signature=_comparison_signature(input_data.comparisons),
            policy_version=POLICY_VERSION,
            prompt_version=PROMPT_VERSION,
            model=self._settings.explanation_model,
        )

        cached = self._cache.get(cache_key)
        if cached is not None:
            return ExplanationResult(
                status=ExplanationStatus.READY,
                source="CACHE",
                explanation=cached,
            )

        messages = build_pair_messages(input_data.model_dump())
        try:
            raw = await self._client.generate_structured(
                model=self._settings.explanation_model,
                messages=messages,
                json_schema=EXPLANATION_SCHEMA,
                routing_mode="latency",
                request_type="pair_explanation",
                prompt_version=PROMPT_VERSION,
                schema_version=SCHEMA_VERSION,
                pair_id=input_data.pair_id,
            )
        except LLMError as exc:
            log.warning("Explanation LLM error for pair %s: %s", input_data.pair_id, exc)
            return ExplanationResult(
                status=ExplanationStatus.FAILED,
                source="FALLBACK",
                error_code=type(exc).__name__,
            )

        raw.pop("__llm_meta__", None)

        # Validate output
        try:
            output = ExplanationOutput.model_validate(raw)
        except ValidationError as exc:
            log.warning("Explanation schema validation failed for pair %s: %s", input_data.pair_id, exc)
            return ExplanationResult(
                status=ExplanationStatus.FAILED,
                source="FALLBACK",
                error_code="SCHEMA_ERROR",
            )

        result_dict = output.model_dump()
        self._cache.set(cache_key, result_dict)

        return ExplanationResult(
            status=ExplanationStatus.READY,
            source="LLM",
            explanation=result_dict,
        )


class GroupExplanationService:
    """Lazy explanation cho Group/Subgroup pattern."""

    def __init__(
        self,
        *,
        settings: LLMSettings | None = None,
        client: LLMClient | None = None,
        cache: LLMCache | None = None,
    ) -> None:
        self._settings = settings or get_llm_settings()
        self._client = client or get_llm_client(self._settings)
        self._cache = cache or get_explanation_cache(self._settings.cache_ttl_seconds)

    async def get_or_generate(self, group_data: dict) -> ExplanationResult:
        """Trả về group explanation — từ cache hoặc gọi LLM."""
        if not self._settings.enabled or not self._settings.explanation_enabled:
            return ExplanationResult(status=ExplanationStatus.DISABLED, source="DISABLED")

        cache_key = _group_cache_key(
            group=group_data.get("group", ""),
            subgroup=group_data.get("subgroup", ""),
            dominant_signature=group_data.get("dominant_signature", ""),
            model=self._settings.explanation_model,
        )

        cached = self._cache.get(cache_key)
        if cached is not None:
            return ExplanationResult(
                status=ExplanationStatus.READY,
                source="CACHE",
                explanation=cached,
            )

        messages = build_group_messages(group_data)
        try:
            raw = await self._client.generate_structured(
                model=self._settings.explanation_model,
                messages=messages,
                json_schema=GROUP_EXPLANATION_SCHEMA,
                routing_mode="latency",
                request_type="group_explanation",
                prompt_version=GROUP_PROMPT_VERSION,
                schema_version=SCHEMA_VERSION,
            )
        except LLMError as exc:
            log.warning("Group explanation LLM error: %s", exc)
            return ExplanationResult(
                status=ExplanationStatus.FAILED,
                source="FALLBACK",
                error_code=type(exc).__name__,
            )

        raw.pop("__llm_meta__", None)

        # Basic validation — check required keys present
        required = {"pattern_summary", "common_signals", "review_guidance"}
        if not required.issubset(raw.keys()):
            log.warning("Group explanation missing required fields: %s", raw.keys())
            return ExplanationResult(
                status=ExplanationStatus.FAILED,
                source="FALLBACK",
                error_code="SCHEMA_ERROR",
            )

        self._cache.set(cache_key, raw)
        return ExplanationResult(
            status=ExplanationStatus.READY,
            source="LLM",
            explanation=raw,
        )


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _comparison_signature(comparisons: dict[str, str]) -> str:
    """Canonical signature từ comparisons dict — thứ tự field sorted."""
    parts = "|".join(f"{k}:{v}" for k, v in sorted(comparisons.items()))
    return hashlib.sha256(parts.encode()).hexdigest()[:16]


def _group_cache_key(*, group: str, subgroup: str, dominant_signature: str, model: str) -> str:
    raw = "\x00".join(["group_explanation", group, subgroup, dominant_signature,
                       GROUP_PROMPT_VERSION, SCHEMA_VERSION, model])
    return hashlib.sha256(raw.encode()).hexdigest()
