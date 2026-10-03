"""Phase L1 acceptance tests — LLMClient.

Tests dùng httpx.MockTransport để không gọi OpenRouter thật.
Acceptance criteria:
- mock client test: success path, timeout, 5xx retry, 429 retry, schema error
- audit record được ghi sau mỗi call
- LLMDisabledError khi llm_enabled=False
- provenance metadata (__llm_meta__) có trong result
- retry count đúng với max_retries
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest
import pytest_asyncio

from masterone.llm import audit as llm_audit
from masterone.llm.circuit_breaker import CircuitBreaker
from masterone.llm.client import (
    LLMClient,
    LLMDisabledError,
    LLMHTTPError,
    LLMSchemaError,
    LLMTimeoutError,
    reset_client,
)
from masterone.llm.config import LLMSettings
from masterone.llm.contracts import LLMRequestStatus
from masterone.llm.metrics import LLMMetrics


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _make_settings(enabled: bool = True, max_retries: int = 2) -> LLMSettings:
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
        max_retries=max_retries,
        cache_ttl_seconds=604800,
        allow_auto_merge_evidence=False,
        max_semantic_calls_per_run=2000,
        max_explanation_calls_per_run=500,
        max_input_tokens_per_request=8000,
        max_output_tokens_semantic=500,
        max_output_tokens_explanation=500,
    )


def _openrouter_response(content: dict) -> httpx.Response:
    body = {
        "choices": [{"message": {"content": json.dumps(content)}}],
        "usage": {"prompt_tokens": 50, "completion_tokens": 30},
    }
    return httpx.Response(200, json=body)


_SIMPLE_SCHEMA = {
    "type": "object",
    "properties": {"result": {"type": "string"}},
    "required": ["result"],
}

_MESSAGES = [{"role": "user", "content": "test"}]


def _make_client(transport: httpx.AsyncMockTransport, enabled: bool = True, max_retries: int = 2) -> LLMClient:
    s = _make_settings(enabled=enabled, max_retries=max_retries)
    client = LLMClient.__new__(LLMClient)
    client._settings = s
    client._breaker = CircuitBreaker(failure_threshold=5, recovery_timeout_seconds=60.0)
    client._metrics = LLMMetrics()
    client._http = httpx.AsyncClient(
        base_url=s.openrouter_base_url,
        transport=transport,
        timeout=s.request_timeout_seconds,
    )
    return client


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clear_audit():
    llm_audit.clear_log()
    reset_client()
    yield
    llm_audit.clear_log()
    reset_client()


# ─── Tests ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_success_path():
    expected = {"result": "LIKELY_EQUIVALENT"}
    transport = httpx.MockTransport(lambda req: _openrouter_response(expected))
    client = _make_client(transport)

    result = await client.generate_structured(
        model="openai/gpt-5.6-luna",
        messages=_MESSAGES,
        json_schema=_SIMPLE_SCHEMA,
        request_type="semantic_fallback",
    )

    assert result["result"] == "LIKELY_EQUIVALENT"
    assert "__llm_meta__" in result
    assert result["__llm_meta__"]["model"] == "openai/gpt-5.6-luna"
    assert result["__llm_meta__"]["provider"] == "openrouter"


@pytest.mark.asyncio
async def test_audit_record_on_success():
    transport = httpx.MockTransport(lambda req: _openrouter_response({"result": "ok"}))
    client = _make_client(transport)

    await client.generate_structured(
        model="openai/gpt-5.4-nano",
        messages=_MESSAGES,
        json_schema=_SIMPLE_SCHEMA,
        request_type="pair_explanation",
        pair_id="p001",
    )

    log = llm_audit.get_log()
    assert len(log) == 1
    rec = log[0]
    assert rec.status == LLMRequestStatus.SUCCESS
    assert rec.model == "openai/gpt-5.4-nano"
    assert rec.pair_id == "p001"
    assert rec.input_tokens == 50
    assert rec.output_tokens == 30
    assert rec.latency_ms is not None and rec.latency_ms >= 0


@pytest.mark.asyncio
async def test_disabled_raises_immediately():
    transport = httpx.MockTransport(lambda req: _openrouter_response({}))
    client = _make_client(transport, enabled=False)

    with pytest.raises(LLMDisabledError):
        await client.generate_structured(
            model="openai/gpt-5.6-luna",
            messages=_MESSAGES,
            json_schema=_SIMPLE_SCHEMA,
        )


@pytest.mark.asyncio
async def test_429_retried_then_raises():
    call_count = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(429, text="rate limit")

    client = _make_client(httpx.MockTransport(handler), max_retries=2)

    with pytest.raises(LLMHTTPError) as exc_info:
        await client.generate_structured(
            model="openai/gpt-5.6-luna",
            messages=_MESSAGES,
            json_schema=_SIMPLE_SCHEMA,
        )

    assert exc_info.value.status_code == 429
    # 1 initial + 2 retries = 3 total calls
    assert call_count == 3


@pytest.mark.asyncio
async def test_5xx_retried():
    call_count = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            return httpx.Response(503, text="unavailable")
        return _openrouter_response({"result": "ok"})

    client = _make_client(httpx.MockTransport(handler), max_retries=2)

    result = await client.generate_structured(
        model="openai/gpt-5.6-luna",
        messages=_MESSAGES,
        json_schema=_SIMPLE_SCHEMA,
    )
    assert result["result"] == "ok"
    assert call_count == 3


@pytest.mark.asyncio
async def test_timeout_raises_and_audits():
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=req)

    client = _make_client(httpx.MockTransport(handler), max_retries=0)

    with pytest.raises(LLMTimeoutError):
        await client.generate_structured(
            model="openai/gpt-5.6-luna",
            messages=_MESSAGES,
            json_schema=_SIMPLE_SCHEMA,
        )

    log = llm_audit.get_log()
    assert any(r.status == LLMRequestStatus.TIMEOUT for r in log)


@pytest.mark.asyncio
async def test_malformed_json_raises_schema_error():
    def handler(req: httpx.Request) -> httpx.Response:
        body = {
            "choices": [{"message": {"content": "not valid json {{"}}],
            "usage": {},
        }
        return httpx.Response(200, json=body)

    client = _make_client(httpx.MockTransport(handler), max_retries=0)

    with pytest.raises(LLMSchemaError):
        await client.generate_structured(
            model="openai/gpt-5.6-luna",
            messages=_MESSAGES,
            json_schema=_SIMPLE_SCHEMA,
        )

    log = llm_audit.get_log()
    assert any(r.error_code == "JSON_DECODE_ERROR" for r in log)


@pytest.mark.asyncio
async def test_non_retryable_4xx_raises_immediately():
    call_count = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(401, text="unauthorized")

    client = _make_client(httpx.MockTransport(handler), max_retries=2)

    with pytest.raises(LLMHTTPError) as exc_info:
        await client.generate_structured(
            model="openai/gpt-5.6-luna",
            messages=_MESSAGES,
            json_schema=_SIMPLE_SCHEMA,
        )

    assert exc_info.value.status_code == 401
    # 401 không retry
    assert call_count == 1


@pytest.mark.asyncio
async def test_routing_mode_sent_in_request():
    captured_body: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured_body.update(json.loads(req.content))
        return _openrouter_response({"result": "ok"})

    client = _make_client(httpx.MockTransport(handler))

    await client.generate_structured(
        model="openai/gpt-5.6-luna",
        messages=_MESSAGES,
        json_schema=_SIMPLE_SCHEMA,
        routing_mode="throughput",
    )

    assert captured_body.get("provider", {}).get("sort") == "throughput"


@pytest.mark.asyncio
async def test_provenance_metadata_in_result():
    transport = httpx.MockTransport(lambda req: _openrouter_response({"result": "ok"}))
    client = _make_client(transport)

    result = await client.generate_structured(
        model="openai/gpt-5.6-luna",
        messages=_MESSAGES,
        json_schema=_SIMPLE_SCHEMA,
        prompt_version="SEMANTIC_V1",
        schema_version="SEMANTIC_SCHEMA_V1",
    )

    meta = result["__llm_meta__"]
    assert meta["prompt_version"] == "SEMANTIC_V1"
    assert meta["schema_version"] == "SEMANTIC_SCHEMA_V1"
    assert meta["provider"] == "openrouter"
    assert len(meta["request_id"]) == 36  # UUID format
    assert len(meta["request_hash"]) == 64  # SHA256 hex
