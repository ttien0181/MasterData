"""Semantic Fallback Service — orchestrates Gate → Cache → Batch → LLM → Map.

Flow cho mỗi PairFieldContext:
  1. Gate check → skip nếu FALSE
  2. Cache check → return cached SemanticEvidence nếu HIT
  3. Batch remaining contexts (batch_size items/request)
  4. Async concurrent calls với semaphore (max_concurrency)
  5. Validate SemanticBatchResponse (pair_id integrity)
  6. Map → SemanticEvidence với provenance
  7. Write to cache
  8. Return: {pair_id: SemanticEvidence | None}
     None = gate skipped hoặc LLM error → Review Queue

LLM failure KHÔNG raise — trả None cho pair đó → Review Queue.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

from pydantic import ValidationError

from masterone.llm.audit import hash_response
from masterone.llm.cache import LLMCache, get_semantic_cache
from masterone.llm.client import LLMClient, LLMError, LLMSchemaError, get_llm_client
from masterone.llm.config import LLMSettings, get_llm_settings
from masterone.llm.contracts import (
    SemanticBatchRequest,
    SemanticBatchResponse,
    SemanticEvidence,
    SemanticField,
    SemanticInputItem,
    LLMRequestStatus,
)
from masterone.llm.gate import LLMGate, PairFieldContext
from masterone.llm.semantic.mapper import map_to_evidence
from masterone.llm.semantic.prompt import PROMPT_VERSION, build_messages
from masterone.llm.semantic.schema import SCHEMA_VERSION, SEMANTIC_BATCH_SCHEMA

log = logging.getLogger(__name__)


@dataclass
class SemanticRunStats:
    total: int = 0
    gate_skipped: int = 0
    cache_hits: int = 0
    llm_calls: int = 0
    llm_errors: int = 0
    budget_exceeded: int = 0


class SemanticFallbackService:
    """Semantic fallback cho một batch candidate pair contexts."""

    def __init__(
        self,
        *,
        settings: LLMSettings | None = None,
        client: LLMClient | None = None,
        cache: LLMCache | None = None,
        gate: LLMGate | None = None,
    ) -> None:
        self._settings = settings or get_llm_settings()
        self._client = client or get_llm_client(self._settings)
        self._cache = cache or get_semantic_cache(self._settings.cache_ttl_seconds)
        self._gate = gate or LLMGate()

    async def run(
        self,
        contexts: list[PairFieldContext],
        *,
        routing_mode: str = "latency",
        llm_call_budget: int | None = None,
    ) -> dict[str, SemanticEvidence | None]:
        """Xử lý danh sách PairFieldContext, trả về map pair_id → evidence."""
        if not self._settings.enabled or not self._settings.semantic_enabled:
            return {ctx.pair_id: None for ctx in contexts}

        budget = llm_call_budget if llm_call_budget is not None else self._settings.max_semantic_calls_per_run
        stats = SemanticRunStats(total=len(contexts))
        results: dict[str, SemanticEvidence | None] = {}

        # ── Phase 1: Gate + Cache ──────────────────────────────────────────────
        pending: list[PairFieldContext] = []
        for ctx in contexts:
            if not self._gate.should_run_semantic_fallback(ctx):
                stats.gate_skipped += 1
                results[ctx.pair_id] = None
                continue

            cache_key = LLMCache.semantic_key(
                field=ctx.field,
                left_value=ctx.left_value,
                right_value=ctx.right_value,
                model=self._settings.semantic_model,
                prompt_version=PROMPT_VERSION,
                schema_version=SCHEMA_VERSION,
            )
            cached = self._cache.get(cache_key)
            if cached is not None:
                stats.cache_hits += 1
                # Cache lưu SemanticEvidence với pair_id gốc; trả về với pair_id hiện tại
                results[ctx.pair_id] = _clone_evidence_for_pair(cached, ctx.pair_id)
                continue

            pending.append(ctx)

        # ── Phase 2: Budget check ──────────────────────────────────────────────
        if len(pending) > budget:
            log.warning(
                "LLM semantic budget exceeded: %d pending > %d budget, skipping excess",
                len(pending), budget,
            )
            for ctx in pending[budget:]:
                stats.budget_exceeded += 1
                results[ctx.pair_id] = None
            pending = pending[:budget]

        if not pending:
            _log_stats(stats)
            return results

        # ── Phase 3: Batch + Async concurrent calls ────────────────────────────
        batch_size = self._settings.semantic_batch_size
        batches = [pending[i:i + batch_size] for i in range(0, len(pending), batch_size)]
        semaphore = asyncio.Semaphore(self._settings.max_concurrency)

        async def process_batch(batch: list[PairFieldContext]) -> dict[str, SemanticEvidence | None]:
            async with semaphore:
                return await self._call_batch(batch, routing_mode=routing_mode, stats=stats)

        batch_results = await asyncio.gather(
            *(process_batch(b) for b in batches),
            return_exceptions=True,
        )

        for br in batch_results:
            if isinstance(br, BaseException):
                # Unhandled exception trong batch — log, mark all as None
                log.error("Unexpected batch exception: %s", br)
                continue
            results.update(br)

        _log_stats(stats)
        return results

    async def _call_batch(
        self,
        batch: list[PairFieldContext],
        *,
        routing_mode: str,
        stats: SemanticRunStats,
    ) -> dict[str, SemanticEvidence | None]:
        """Gọi LLM cho một batch, validate, map, cache. Không raise."""
        items_payload = [
            {
                "pair_id": ctx.pair_id,
                "field": ctx.field,
                "left_value": ctx.left_value,
                "right_value": ctx.right_value,
                "left_normalized": ctx.left_normalized,
                "right_normalized": ctx.right_normalized,
            }
            for ctx in batch
        ]

        # Build SemanticBatchRequest để validate input trước khi gửi
        try:
            batch_req = SemanticBatchRequest(items=[
                SemanticInputItem(
                    pair_id=ctx.pair_id,
                    field=SemanticField(ctx.field),
                    left_value=ctx.left_value,
                    right_value=ctx.right_value,
                    left_normalized=ctx.left_normalized,
                    right_normalized=ctx.right_normalized,
                )
                for ctx in batch
            ])
        except (ValidationError, ValueError) as exc:
            log.error("Batch input validation error: %s", exc)
            return {ctx.pair_id: None for ctx in batch}

        messages = build_messages(items_payload)
        stats.llm_calls += 1

        try:
            raw = await self._client.generate_structured(
                model=self._settings.semantic_model,
                messages=messages,
                json_schema=SEMANTIC_BATCH_SCHEMA,
                routing_mode=routing_mode,  # type: ignore[arg-type]
                request_type="semantic_fallback",
                prompt_version=PROMPT_VERSION,
                schema_version=SCHEMA_VERSION,
            )
        except LLMError as exc:
            log.warning("LLM semantic batch failed: %s — marking all as None (Review)", exc)
            stats.llm_errors += len(batch)
            return {ctx.pair_id: None for ctx in batch}

        meta = raw.pop("__llm_meta__", {})
        request_id = meta.get("request_id", "")
        request_hash = meta.get("request_hash", "")

        # Validate batch response
        try:
            batch_resp = SemanticBatchResponse.model_validate(raw)
            batch_resp.validate_pair_ids(batch_req)
        except (ValidationError, ValueError) as exc:
            log.error("LLM response schema/pair_id validation failed: %s", exc)
            stats.llm_errors += len(batch)
            return {ctx.pair_id: None for ctx in batch}

        # Map → evidence và cache
        out: dict[str, SemanticEvidence | None] = {}
        resp_hash = hash_response(raw)
        ctx_by_id = {ctx.pair_id: ctx for ctx in batch}

        for item in batch_resp.items:
            ctx = ctx_by_id[item.pair_id]
            evidence = map_to_evidence(
                item,
                model=self._settings.semantic_model,
                request_id=request_id,
                request_hash=request_hash,
                response_hash=resp_hash,
            )
            out[item.pair_id] = evidence

            # Cache với canonical key
            cache_key = LLMCache.semantic_key(
                field=ctx.field,
                left_value=ctx.left_value,
                right_value=ctx.right_value,
                model=self._settings.semantic_model,
                prompt_version=PROMPT_VERSION,
                schema_version=SCHEMA_VERSION,
            )
            self._cache.set(cache_key, evidence)

        return out


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _clone_evidence_for_pair(cached: SemanticEvidence, pair_id: str) -> SemanticEvidence:
    """Return copy của cached evidence với pair_id mới."""
    data = cached.model_dump()
    data["pair_id"] = pair_id
    return SemanticEvidence(**data)


def _log_stats(stats: SemanticRunStats) -> None:
    log.info(
        "SemanticFallback: total=%d gate_skipped=%d cache_hits=%d "
        "llm_calls=%d errors=%d budget_exceeded=%d",
        stats.total, stats.gate_skipped, stats.cache_hits,
        stats.llm_calls, stats.llm_errors, stats.budget_exceeded,
    )
