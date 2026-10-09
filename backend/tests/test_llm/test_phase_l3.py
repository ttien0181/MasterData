"""Phase L3 acceptance tests — ExplanationService + GroupExplanationService.

Acceptance criteria:
- T-LLM-10: explanation cannot invent score — LLM không được tự tạo numeric match score
- Review UI không block vì explanation — service không raise, trả FAILED gracefully
- Cache hit → 0 LLM calls
- LLM fail → status=FAILED, explanation=None (UI vẫn hoạt động)
- LLM disabled → status=DISABLED
- ExplanationOutput validation reject recommendation merge/reject
- Group explanation: LLM chỉ diễn giải stats đã có, không bịa số liệu
"""
from __future__ import annotations

import json

import httpx
import pytest
from pydantic import ValidationError

from masterone.llm import audit as llm_audit
from masterone.llm.cache import LLMCache, reset_caches
from masterone.llm.circuit_breaker import CircuitBreaker
from masterone.llm.client import LLMClient, reset_client
from masterone.llm.config import LLMSettings
from masterone.llm.contracts import ExplanationInput, ExplanationOutput
from masterone.llm.explanation.service import (
    ExplanationService,
    ExplanationStatus,
    GroupExplanationService,
)
from masterone.llm.metrics import LLMMetrics


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clean_state():
    llm_audit.clear_log()
    reset_client()
    reset_caches()
    yield
    llm_audit.clear_log()
    reset_client()
    reset_caches()


def _settings(enabled: bool = True) -> LLMSettings:
    return LLMSettings(
        enabled=enabled,
        semantic_enabled=True,
        explanation_enabled=True,
        openrouter_api_key="sk-or-test",
        openrouter_base_url="https://openrouter.ai/api/v1",
        semantic_model="openai/gpt-5.6-luna",
        explanation_model="openai/gpt-5.4-nano",
        semantic_batch_size=10,
        max_concurrency=5,
        request_timeout_seconds=15,
        max_retries=0,
        cache_ttl_seconds=600,
        allow_auto_merge_evidence=False,
        max_semantic_calls_per_run=2000,
        max_explanation_calls_per_run=500,
        max_input_tokens_per_request=8000,
        max_output_tokens_semantic=500,
        max_output_tokens_explanation=500,
    )


def _mock_client(payload: dict, settings: LLMSettings, *, call_log: list | None = None) -> LLMClient:
    _log = call_log if call_log is not None else []

    def handler(req: httpx.Request) -> httpx.Response:
        _log.append(json.loads(req.content))
        body = {
            "choices": [{"message": {"content": json.dumps(payload)}}],
            "usage": {"prompt_tokens": 60, "completion_tokens": 40},
        }
        return httpx.Response(200, json=body)

    client = LLMClient.__new__(LLMClient)
    client._settings = settings
    client._breaker = CircuitBreaker(failure_threshold=5, recovery_timeout_seconds=60.0)
    client._metrics = LLMMetrics()
    client._http = httpx.AsyncClient(
        base_url=settings.openrouter_base_url,
        transport=httpx.MockTransport(handler),
        timeout=settings.request_timeout_seconds,
    )
    return client


def _timeout_client(settings: LLMSettings) -> LLMClient:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=req)

    client = LLMClient.__new__(LLMClient)
    client._settings = settings
    client._breaker = CircuitBreaker(failure_threshold=5, recovery_timeout_seconds=60.0)
    client._metrics = LLMMetrics()
    client._http = httpx.AsyncClient(
        base_url=settings.openrouter_base_url,
        transport=httpx.MockTransport(handler),
        timeout=settings.request_timeout_seconds,
    )
    return client


def _input(pair_id: str = "p001") -> ExplanationInput:
    return ExplanationInput(
        pair_id=pair_id,
        primary_group="D",
        decision_subgroup="D1",
        decision="REVIEW_REQUIRED",
        match_weight=8.7,
        comparisons={"name": "HS", "phone": "CF", "email": "EX", "dob": "EX", "address": "HS"},
        normalization_tags=["NAME_ASCII_EQUIVALENT"],
        blocking_reasons=["tfidf_name_birthyear_topk"],
        policy_result={"eligible": False, "blocked_reason": "PHONE_CONFLICT"},
    )


# ─── ExplanationOutput contract ───────────────────────────────────────────────

def test_t_llm_10_explanation_no_invented_score():
    """T-LLM-10: ExplanationOutput schema không có generated numeric score."""
    schema_keys = {"summary", "supporting_points", "risk_points"}
    from masterone.llm.explanation.schema import EXPLANATION_SCHEMA
    schema_props = set(EXPLANATION_SCHEMA["properties"].keys())
    assert schema_props == schema_keys, (
        f"Schema có field ngoài expected: {schema_props - schema_keys}"
    )
    # Không có "score", "confidence", "probability", "match_weight"
    forbidden = {"score", "confidence", "probability", "match_weight", "match_probability"}
    assert not (schema_props & forbidden), f"Schema chứa numeric score fields: {schema_props & forbidden}"


def test_explanation_output_no_merge_recommendation():
    """ExplanationOutput từ chối nếu chứa merge/reject recommendation."""
    with pytest.raises(ValidationError):
        ExplanationOutput(
            summary="Hai bản ghi giống nhau.",
            supporting_points=["Hãy gộp ngay.", "Email khớp."],
            risk_points=[],
        )


def test_explanation_output_valid():
    out = ExplanationOutput(
        summary="Số điện thoại có xung đột, cần Steward xem xét.",
        supporting_points=["Email khớp chính xác.", "Ngày sinh khớp."],
        risk_points=["Số điện thoại hiện hành bị đánh dấu xung đột."],
    )
    assert out.summary
    assert len(out.supporting_points) == 2


# ─── ExplanationService ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_explanation_success():
    s = _settings()
    payload = {
        "summary": "Cặp này có số điện thoại xung đột.",
        "supporting_points": ["Email khớp.", "Ngày sinh khớp."],
        "risk_points": ["Số điện thoại bị đánh dấu xung đột."],
    }
    client = _mock_client(payload, s)
    svc = ExplanationService(settings=s, client=client, cache=LLMCache(60))

    result = await svc.get_or_generate(_input())
    assert result.status == ExplanationStatus.READY
    assert result.source == "LLM"
    assert result.explanation is not None
    assert "summary" in result.explanation
    assert result.explanation["summary"] == payload["summary"]


@pytest.mark.asyncio
async def test_explanation_llm_fail_does_not_raise():
    """LLM fail → FAILED status, không raise — UI vẫn hoạt động."""
    s = _settings()
    client = _timeout_client(s)
    svc = ExplanationService(settings=s, client=client, cache=LLMCache(60))

    result = await svc.get_or_generate(_input())
    assert result.status == ExplanationStatus.FAILED
    assert result.explanation is None
    assert result.error_code is not None


@pytest.mark.asyncio
async def test_explanation_cache_hit_zero_llm_calls():
    """Cache hit → 0 thêm LLM calls."""
    s = _settings()
    call_log: list = []
    payload = {
        "summary": "ok",
        "supporting_points": ["Điểm 1."],
        "risk_points": [],
    }
    client = _mock_client(payload, s, call_log=call_log)
    cache = LLMCache(600)
    svc = ExplanationService(settings=s, client=client, cache=cache)

    inp = _input()
    # Lần 1
    r1 = await svc.get_or_generate(inp)
    assert r1.source == "LLM"
    assert len(call_log) == 1

    # Lần 2 — phải hit cache
    r2 = await svc.get_or_generate(inp)
    assert r2.source == "CACHE"
    assert len(call_log) == 1  # không thêm call


@pytest.mark.asyncio
async def test_explanation_disabled_returns_disabled_status():
    """LLM disabled → DISABLED status, không gọi LLM."""
    s = _settings(enabled=False)
    call_log: list = []
    client = _mock_client({}, s, call_log=call_log)
    svc = ExplanationService(settings=s, client=client, cache=LLMCache(60))

    result = await svc.get_or_generate(_input())
    assert result.status == ExplanationStatus.DISABLED
    assert len(call_log) == 0


@pytest.mark.asyncio
async def test_explanation_schema_error_returns_failed():
    """LLM trả schema sai → FAILED, không crash."""
    s = _settings()
    bad_payload = {"wrong_field": "value"}
    client = _mock_client(bad_payload, s)
    svc = ExplanationService(settings=s, client=client, cache=LLMCache(60))

    result = await svc.get_or_generate(_input())
    assert result.status == ExplanationStatus.FAILED
    assert result.error_code == "SCHEMA_ERROR"


@pytest.mark.asyncio
async def test_explanation_different_comparisons_different_cache():
    """Comparisons khác nhau → cache key khác, gọi LLM lại."""
    s = _settings()
    call_log: list = []
    payload = {
        "summary": "ok",
        "supporting_points": [],
        "risk_points": [],
    }
    client = _mock_client(payload, s, call_log=call_log)
    cache = LLMCache(600)
    svc = ExplanationService(settings=s, client=client, cache=cache)

    inp1 = ExplanationInput(
        pair_id="p001", primary_group="D", decision_subgroup="D1",
        decision="REVIEW", match_weight=5.0,
        comparisons={"name": "HS", "phone": "CF"},
    )
    inp2 = ExplanationInput(
        pair_id="p001", primary_group="D", decision_subgroup="D1",
        decision="REVIEW", match_weight=5.0,
        comparisons={"name": "HS", "phone": "EX"},  # phone khác
    )
    await svc.get_or_generate(inp1)
    await svc.get_or_generate(inp2)
    assert len(call_log) == 2  # 2 LLM calls vì comparisons khác


# ─── GroupExplanationService ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_group_explanation_success():
    s = _settings()
    payload = {
        "pattern_summary": "Nhóm này chủ yếu có xung đột số điện thoại.",
        "common_signals": ["phone: CF", "email: EX", "dob: EX"],
        "review_guidance": "Kiểm tra số điện thoại trước khi quyết định.",
    }
    client = _mock_client(payload, s)
    svc = GroupExplanationService(settings=s, client=client, cache=LLMCache(60))

    group_data = {
        "group": "D",
        "subgroup": "D1",
        "pair_count": 326,
        "dominant_signature": "N:HS|P:CF|E:EX|DOB:EX|A:HS",
        "stats": {"phone_CF": 326, "email_EX": 301, "dob_EX": 287},
    }
    result = await svc.get_or_generate(group_data)
    assert result.status == ExplanationStatus.READY
    assert result.explanation is not None
    assert "pattern_summary" in result.explanation


@pytest.mark.asyncio
async def test_group_explanation_cache_hit():
    s = _settings()
    call_log: list = []
    payload = {
        "pattern_summary": "ok",
        "common_signals": [],
        "review_guidance": "Check.",
    }
    client = _mock_client(payload, s, call_log=call_log)
    cache = LLMCache(600)
    svc = GroupExplanationService(settings=s, client=client, cache=cache)

    group_data = {
        "group": "D", "subgroup": "D1",
        "pair_count": 100, "dominant_signature": "N:HS|P:CF",
        "stats": {},
    }
    await svc.get_or_generate(group_data)
    r2 = await svc.get_or_generate(group_data)
    assert r2.source == "CACHE"
    assert len(call_log) == 1


@pytest.mark.asyncio
async def test_group_explanation_fail_does_not_raise():
    s = _settings()
    client = _timeout_client(s)
    svc = GroupExplanationService(settings=s, client=client, cache=LLMCache(60))

    group_data = {
        "group": "D", "subgroup": "D1",
        "pair_count": 10, "dominant_signature": "N:HS",
        "stats": {},
    }
    result = await svc.get_or_generate(group_data)
    assert result.status == ExplanationStatus.FAILED
    assert result.explanation is None
