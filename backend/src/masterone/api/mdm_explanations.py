"""LLM Explanation endpoints — lazy, không block Review Queue.

GET /mdm/runs/{run_id}/pairs/{pair_id}/explanation
  - Trả ngay nếu cache HIT
  - Gọi LLM nếu MISS, cache, trả kết quả
  - Nếu LLM fail → status=FAILED, UI vẫn hoạt động với deterministic evidence

GET /mdm/runs/{run_id}/groups/{primary_group}/{subgroup}/explanation
  - Backend aggregate stats từ DB trước
  - LLM chỉ diễn giải pattern

Review Queue KHÔNG phụ thuộc các endpoint này để render.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from masterone.auth.security import CurrentSession, current_session
from masterone.db.session import get_session
from masterone.llm.cache import get_explanation_cache, reset_caches
from masterone.llm.circuit_breaker import get_circuit_breaker
from masterone.llm.client import get_llm_client, reset_client
from masterone.llm.config import LLMConfigError, get_llm_settings
from masterone.llm.metrics import get_metrics
from masterone.llm.contracts import ExplanationInput
from masterone.llm.explanation.service import (
    ExplanationResult,
    ExplanationService,
    ExplanationStatus,
    GroupExplanationService,
)
from masterone.mdm.models import (
    ResolutionPair,
    ResolutionReviewCase,
)
from masterone.mdm.ownership import owned_run

router = APIRouter(prefix="/mdm/runs", tags=["MDM explanations"])
metrics_router = APIRouter(prefix="/mdm/llm", tags=["LLM metrics"])


def _get_explanation_service() -> ExplanationService:
    try:
        settings = get_llm_settings()
        return ExplanationService(
            settings=settings,
            client=get_llm_client(settings),
            cache=get_explanation_cache(settings.cache_ttl_seconds),
        )
    except LLMConfigError:
        # LLM not configured — return disabled service
        from masterone.llm.config import LLMSettings
        disabled = LLMSettings(
            enabled=False, semantic_enabled=False, explanation_enabled=False,
            openrouter_api_key="", openrouter_base_url="https://openrouter.ai/api/v1",
            semantic_model="", explanation_model="", semantic_batch_size=10,
            max_concurrency=5, request_timeout_seconds=15, max_retries=0,
            cache_ttl_seconds=600, allow_auto_merge_evidence=False,
            max_semantic_calls_per_run=0, max_explanation_calls_per_run=0,
            max_input_tokens_per_request=8000, max_output_tokens_semantic=500,
            max_output_tokens_explanation=500,
        )
        return ExplanationService(settings=disabled)


def _get_group_service() -> GroupExplanationService:
    try:
        settings = get_llm_settings()
        return GroupExplanationService(
            settings=settings,
            client=get_llm_client(settings),
            cache=get_explanation_cache(settings.cache_ttl_seconds),
        )
    except LLMConfigError:
        from masterone.llm.config import LLMSettings
        disabled = LLMSettings(
            enabled=False, semantic_enabled=False, explanation_enabled=False,
            openrouter_api_key="", openrouter_base_url="https://openrouter.ai/api/v1",
            semantic_model="", explanation_model="", semantic_batch_size=10,
            max_concurrency=5, request_timeout_seconds=15, max_retries=0,
            cache_ttl_seconds=600, allow_auto_merge_evidence=False,
            max_semantic_calls_per_run=0, max_explanation_calls_per_run=0,
            max_input_tokens_per_request=8000, max_output_tokens_semantic=500,
            max_output_tokens_explanation=500,
        )
        return GroupExplanationService(settings=disabled)


# ─── Pair explanation ─────────────────────────────────────────────────────────

@router.get("/{run_id}/pairs/{pair_id}/explanation")
async def get_pair_explanation(
    run_id: str,
    pair_id: str,
    db: Session = Depends(get_session),
    auth: CurrentSession = Depends(current_session),
):
    """Lazy explanation cho một pair cụ thể.

    Response:
      status=READY   → explanation dict có sẵn
      status=FAILED  → LLM lỗi, UI dùng deterministic evidence
      status=DISABLED → LLM không được cấu hình
    """
    owned_run(db, run_id, auth.user.id)

    pair = db.get(ResolutionPair, pair_id)
    if pair is None or pair.run_id != run_id:
        raise HTTPException(404, "Pair không tồn tại")

    # Build ExplanationInput từ DB pair
    comparisons = _extract_comparisons(pair.field_evidence or [])
    input_data = ExplanationInput(
        pair_id=pair_id,
        primary_group=_extract_primary_group(pair),
        decision_subgroup=_extract_subgroup(db, run_id, pair_id),
        decision=pair.triage_status or pair.pair_decision,
        match_weight=pair.score or 0.0,
        comparisons=comparisons,
        normalization_tags=_extract_norm_tags(pair.field_evidence or []),
        blocking_reasons=pair.blocking_reasons or [],
        policy_result={
            "eligible": pair.triage_status == "AUTO_MERGE",
            "blocked_reason": pair.triage_reason or "",
        },
    )

    svc = _get_explanation_service()
    result = await svc.get_or_generate(input_data)
    return _explanation_response(result)


# ─── Group explanation ────────────────────────────────────────────────────────

@router.get("/{run_id}/groups/{primary_group}/{subgroup}/explanation")
async def get_group_explanation(
    run_id: str,
    primary_group: str,
    subgroup: str,
    db: Session = Depends(get_session),
    auth: CurrentSession = Depends(current_session),
):
    """Group/Subgroup pattern explanation — backend aggregates, LLM explains."""
    owned_run(db, run_id, auth.user.id)

    # Aggregate từ DB — LLM không tự tính số liệu
    cases = (
        db.query(ResolutionReviewCase)
        .filter_by(run_id=run_id, primary_group=primary_group, subgroup=subgroup)
        .all()
    )
    if not cases:
        raise HTTPException(404, "Nhóm không tồn tại")

    pair_ids_flat: list[str] = [pid for c in cases for pid in (c.pair_ids or [])]
    pair_count = len(set(pair_ids_flat))
    dominant_signature = cases[0].signature_hash if cases else ""

    # Tính field-state stats từ pairs
    pairs = (
        db.query(ResolutionPair)
        .filter(ResolutionPair.run_id == run_id, ResolutionPair.id.in_(pair_ids_flat[:200]))
        .all()
    )
    stats = _aggregate_field_stats(pairs)

    group_data = {
        "group": primary_group,
        "subgroup": subgroup,
        "pair_count": pair_count,
        "dominant_signature": dominant_signature,
        "stats": stats,
    }

    svc = _get_group_service()
    result = await svc.get_or_generate(group_data)
    return {**_explanation_response(result), "group_data": group_data}


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _explanation_response(result: ExplanationResult) -> dict:
    return {
        "status": result.status.value,
        "source": result.source,
        "explanation": result.explanation,
        "error_code": result.error_code,
    }


def _extract_comparisons(field_evidence: list) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in field_evidence:
        if isinstance(item, dict):
            field = item.get("field") or item.get("name")
            state = item.get("state") or item.get("comparison_state")
            if field and state:
                out[field] = state
    return out


def _extract_norm_tags(field_evidence: list) -> list[str]:
    tags: list[str] = []
    for item in field_evidence:
        if isinstance(item, dict):
            t = item.get("normalization_tag") or item.get("method_version")
            if t:
                tags.append(t)
    return tags


def _extract_primary_group(pair: ResolutionPair) -> str:
    if pair.triage_status == "AUTO_MERGE":
        return "A"
    if pair.triage_status == "REVIEW":
        return "D" if _has_cf(pair.field_evidence or []) else "C"
    return "?"


def _has_cf(field_evidence: list) -> bool:
    return any(
        isinstance(e, dict) and e.get("state") == "CF"
        for e in field_evidence
    )


def _extract_subgroup(db: Session, run_id: str, pair_id: str) -> str:
    case = (
        db.query(ResolutionReviewCase)
        .filter(
            ResolutionReviewCase.run_id == run_id,
            ResolutionReviewCase.pair_ids.contains([pair_id]),
        )
        .first()
    )
    return case.subgroup if case else "UNKNOWN"


@metrics_router.get("/metrics")
async def get_llm_metrics(auth: CurrentSession = Depends(current_session)):
    """LLM observability metrics — gate_rate, cache_hit_rate, latency, errors."""
    m = get_metrics()
    breaker = get_circuit_breaker()
    return {
        "metrics": m.snapshot(),
        "circuit_breaker": breaker.stats(),
    }


def _aggregate_field_stats(pairs: list[ResolutionPair]) -> dict:
    """Đếm số pair theo state của từng field — LLM không tự tính."""
    counts: dict[str, dict[str, int]] = {}
    for pair in pairs:
        for item in pair.field_evidence or []:
            if not isinstance(item, dict):
                continue
            field = item.get("field") or item.get("name")
            state = item.get("state") or item.get("comparison_state")
            if field and state:
                counts.setdefault(field, {})
                counts[field][state] = counts[field].get(state, 0) + 1

    # Flatten: "phone_CF": 42
    flat: dict[str, int] = {}
    for field, state_counts in counts.items():
        for state, cnt in state_counts.items():
            flat[f"{field}_{state}"] = cnt
    return flat
