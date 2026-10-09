"""Phase L4 acceptance tests — Metrics, Circuit Breaker, Cost Guard, Audit.

Acceptance criteria:
- Circuit breaker OPEN sau N consecutive failures
- Circuit OPEN → LLMCircuitOpenError ngay, không gọi API
- Circuit tự reset sau recovery_timeout
- record_success() reset consecutive failures
- Metrics tích lũy đúng: requests_total, latency, tokens, gate_rate, cache_hit_rate
- Metrics snapshot có đủ required keys cho dashboard
- Budget guard: pairs vượt budget → None (Review), không crash
- Audit DB flush helper không raise khi session_factory fail
"""
from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest

from masterone.llm import audit as llm_audit
from masterone.llm.cache import LLMCache, reset_caches
from masterone.llm.circuit_breaker import (
    CircuitBreaker,
    CircuitState,
    LLMCircuitOpenError,
    reset_circuit_breaker,
)
from masterone.llm.client import LLMClient, LLMHTTPError, LLMTimeoutError, reset_client
from masterone.llm.config import LLMSettings
from masterone.llm.metrics import LLMMetrics, get_metrics, reset_metrics


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clean_state():
    llm_audit.clear_log()
    reset_client()
    reset_caches()
    reset_circuit_breaker()
    reset_metrics()
    yield
    llm_audit.clear_log()
    reset_client()
    reset_caches()
    reset_circuit_breaker()
    reset_metrics()


def _settings() -> LLMSettings:
    return LLMSettings(
        enabled=True,
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


def _make_client(
    handler,
    settings: LLMSettings | None = None,
    *,
    breaker: CircuitBreaker | None = None,
    metrics: LLMMetrics | None = None,
) -> LLMClient:
    s = settings or _settings()
    client = LLMClient.__new__(LLMClient)
    client._settings = s
    client._breaker = breaker or CircuitBreaker(failure_threshold=3, recovery_timeout_seconds=1.0)
    client._metrics = metrics or LLMMetrics()
    client._http = httpx.AsyncClient(
        base_url=s.openrouter_base_url,
        transport=httpx.MockTransport(handler),
        timeout=s.request_timeout_seconds,
    )
    return client


def _ok_handler(req: httpx.Request) -> httpx.Response:
    body = {
        "choices": [{"message": {"content": json.dumps({"result": "ok"})}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }
    return httpx.Response(200, json=body)


def _fail_handler(req: httpx.Request) -> httpx.Response:
    return httpx.Response(503, text="unavailable")


_SCHEMA = {"type": "object", "properties": {"result": {"type": "string"}}, "required": ["result"]}
_MSGS = [{"role": "user", "content": "test"}]


# ─── Circuit Breaker unit tests ───────────────────────────────────────────────

class TestCircuitBreaker:

    def test_starts_closed(self):
        cb = CircuitBreaker()
        assert cb.state == CircuitState.CLOSED

    def test_opens_after_threshold(self):
        cb = CircuitBreaker(failure_threshold=3)
        for _ in range(3):
            cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_not_open_before_threshold(self):
        cb = CircuitBreaker(failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED

    def test_check_raises_when_open(self):
        cb = CircuitBreaker(failure_threshold=1)
        cb.record_failure()
        with pytest.raises(LLMCircuitOpenError):
            cb.check()

    def test_check_passes_when_closed(self):
        cb = CircuitBreaker()
        cb.check()  # must not raise

    def test_success_resets_failures(self):
        cb = CircuitBreaker(failure_threshold=5)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        cb.record_failure()
        # consecutive_failures reset → hanya 1, belum threshold
        assert cb.state == CircuitState.CLOSED

    def test_auto_reset_after_timeout(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout_seconds=0.05)
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        time.sleep(0.1)
        # Accessing .state triggers auto-reset
        assert cb.state == CircuitState.CLOSED

    def test_check_auto_reset_after_timeout(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout_seconds=0.05)
        cb.record_failure()
        time.sleep(0.1)
        cb.check()  # must not raise after timeout

    def test_stats_keys(self):
        cb = CircuitBreaker()
        stats = cb.stats()
        assert "state" in stats
        assert "consecutive_failures" in stats
        assert "threshold" in stats


# ─── Circuit breaker integration với LLMClient ────────────────────────────────

@pytest.mark.asyncio
async def test_client_records_failure_on_5xx():
    breaker = CircuitBreaker(failure_threshold=3, recovery_timeout_seconds=60.0)
    client = _make_client(_fail_handler, breaker=breaker)

    with pytest.raises(LLMHTTPError):
        await client.generate_structured(
            model="m", messages=_MSGS, json_schema=_SCHEMA,
        )
    assert breaker.stats()["consecutive_failures"] == 1


@pytest.mark.asyncio
async def test_client_circuit_open_raises_immediately():
    """Circuit OPEN → LLMCircuitOpenError tanpa API call."""
    call_count = 0

    def counting_handler(req: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return _ok_handler(req)

    breaker = CircuitBreaker(failure_threshold=1, recovery_timeout_seconds=60.0)
    breaker.record_failure()  # force OPEN
    client = _make_client(counting_handler, breaker=breaker)

    with pytest.raises(LLMCircuitOpenError):
        await client.generate_structured(
            model="m", messages=_MSGS, json_schema=_SCHEMA,
        )
    assert call_count == 0  # tidak ada HTTP call


@pytest.mark.asyncio
async def test_client_records_success_resets_breaker():
    breaker = CircuitBreaker(failure_threshold=5)
    breaker.record_failure()
    breaker.record_failure()

    client = _make_client(_ok_handler, breaker=breaker)
    await client.generate_structured(
        model="m", messages=_MSGS, json_schema=_SCHEMA,
    )
    assert breaker.stats()["consecutive_failures"] == 0
    assert breaker.state == CircuitState.CLOSED


# ─── Metrics unit tests ───────────────────────────────────────────────────────

class TestLLMMetrics:

    def test_record_request_increments_total(self):
        m = LLMMetrics()
        m.record_request(request_type="semantic_fallback", status="SUCCESS", latency_ms=120)
        assert m.requests_total == 1
        assert m.requests_by_type["semantic_fallback"] == 1

    def test_latency_p50_p95(self):
        m = LLMMetrics()
        for i in range(100):
            m.record_request(request_type="t", status="SUCCESS", latency_ms=float(i + 1))
        assert m.latency_p50() is not None
        assert m.latency_p95() is not None
        assert m.latency_p95() > m.latency_p50()

    def test_gate_rate(self):
        m = LLMMetrics()
        m.record_gate(passed=True)
        m.record_gate(passed=True)
        m.record_gate(passed=False)
        assert abs(m.gate_rate() - 2 / 3) < 0.001

    def test_cache_hit_rate(self):
        m = LLMMetrics()
        m.record_cache(hit=True)
        m.record_cache(hit=False)
        assert m.cache_hit_rate() == 0.5

    def test_error_rate(self):
        m = LLMMetrics()
        m.record_request(request_type="t", status="SUCCESS", latency_ms=10)
        m.record_request(request_type="t", status="TIMEOUT", latency_ms=15000)
        assert m.error_rate() == 0.5

    def test_timeout_increments_timeout_count(self):
        m = LLMMetrics()
        m.record_request(request_type="t", status="TIMEOUT", latency_ms=15000)
        assert m.timeout_count == 1

    def test_snapshot_required_keys(self):
        """Snapshot phải có đủ keys cho demo dashboard."""
        m = LLMMetrics()
        snap = m.snapshot()
        required = {
            "requests_total", "gate_rate_pct", "cache_hit_rate_pct",
            "error_rate_pct", "latency_p50_ms", "latency_p95_ms",
            "input_tokens_total", "output_tokens_total",
            "semantic_result_distribution", "explanation_generated",
        }
        assert required.issubset(snap.keys()), f"Missing: {required - snap.keys()}"

    def test_tokens_accumulate(self):
        m = LLMMetrics()
        m.record_request(request_type="t", status="SUCCESS", latency_ms=10,
                         input_tokens=100, output_tokens=50)
        m.record_request(request_type="t", status="SUCCESS", latency_ms=10,
                         input_tokens=200, output_tokens=75)
        assert m.input_tokens_total == 300
        assert m.output_tokens_total == 125

    def test_reset_clears_all(self):
        m = LLMMetrics()
        m.record_request(request_type="t", status="SUCCESS", latency_ms=10)
        m.reset()
        assert m.requests_total == 0
        assert m.latency_samples == []


# ─── Metrics integration với LLMClient ───────────────────────────────────────

@pytest.mark.asyncio
async def test_client_metrics_success():
    m = LLMMetrics()
    client = _make_client(_ok_handler, metrics=m)

    await client.generate_structured(
        model="m", messages=_MSGS, json_schema=_SCHEMA,
        request_type="semantic_fallback",
    )
    assert m.requests_total == 1
    assert m.requests_by_type["semantic_fallback"] == 1
    assert m.requests_by_status["SUCCESS"] == 1
    assert m.latency_p50() is not None
    assert m.input_tokens_total == 10
    assert m.output_tokens_total == 5


@pytest.mark.asyncio
async def test_client_metrics_timeout():
    def timeout_handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=req)

    m = LLMMetrics()
    client = _make_client(timeout_handler, metrics=m)

    with pytest.raises(LLMTimeoutError):
        await client.generate_structured(
            model="m", messages=_MSGS, json_schema=_SCHEMA,
        )
    assert m.timeout_count == 1


# ─── Budget guard (trong SemanticFallbackService) ─────────────────────────────

@pytest.mark.asyncio
async def test_budget_guard_limits_llm_calls():
    """Pairs vượt budget → None (Review Queue), không crash."""
    from masterone.llm.gate import PairFieldContext
    from masterone.llm.semantic.service import SemanticFallbackService

    s = _settings()

    def ok_handler(req: httpx.Request) -> httpx.Response:
        # Trả response hợp lệ để không kẹt ở retry
        body_data = json.loads(req.content)
        msgs = body_data.get("messages", [])
        content_text = msgs[-1].get("content", "") if msgs else ""
        # Đếm số pair_id xuất hiện trong user message
        import re
        pair_ids = re.findall(r"pair_id: (p\d+)", content_text)
        items = [
            {"pair_id": pid, "field": "address", "result": "LIKELY_EQUIVALENT",
             "reason_code": "ABBREVIATION_EQUIVALENT", "short_reason": "ok"}
            for pid in pair_ids
        ]
        body = {
            "choices": [{"message": {"content": json.dumps({"items": items})}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
        return httpx.Response(200, json=body)

    client = _make_client(ok_handler, s)
    cache = LLMCache(60)
    svc = SemanticFallbackService(settings=s, client=client, cache=cache)

    # 15 contexts nhưng budget chỉ cho 5 (< batch_size=10 → 1 batch với 5 pairs, 10 bị cắt)
    contexts = [
        PairFieldContext(
            pair_id=f"p{i:03d}",
            field="address",
            current_state="WS",
            current_provenance="FUZZY",
            fuzzy_score=0.72,
            hard_conflict=False,
            has_supporting_evidence=True,
            left_value=f"Addr A {i}",
            right_value=f"Addr B {i}",
        )
        for i in range(15)
    ]

    results = await svc.run(contexts, llm_call_budget=5)

    # Tất cả phải có entry
    assert len(results) == 15
    # 10 pairs vượt budget phải là None
    none_count = sum(1 for v in results.values() if v is None)
    assert none_count >= 10


# ─── Audit flush helper ───────────────────────────────────────────────────────

def test_flush_to_db_no_raise_on_factory_error():
    """flush_to_db không raise dù session_factory fail."""
    from masterone.llm.audit import flush_to_db
    from masterone.llm.contracts import LLMRequestStatus
    from masterone.llm.audit import LLMAuditRecord

    llm_audit.record(LLMAuditRecord(
        request_id="test-id",
        request_type="semantic_fallback",
        model="m",
        provider="openrouter",
        prompt_version="V1",
        schema_version="V1",
        request_hash="abc",
        status=LLMRequestStatus.SUCCESS,
    ))

    def bad_factory():
        raise RuntimeError("DB down")

    # Không raise dù factory fail
    result = flush_to_db(bad_factory)
    assert result == 0  # 0 records flushed, không crash


def test_flush_to_db_empty_log():
    """Không có records → flush trả 0."""
    from masterone.llm.audit import flush_to_db
    result = flush_to_db(lambda: None)
    assert result == 0
