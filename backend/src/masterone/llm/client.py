"""LLM Client abstraction — single point of contact với OpenRouter.

Không gọi OpenRouter trực tiếp từ nhiều module.
Mọi service khác chỉ dùng interface LLMClient.generate_structured().

OpenRouter tương thích OpenAI-style API:
  base_url = https://openrouter.ai/api/v1
  Authorization: Bearer <OPENROUTER_API_KEY>

Routing:
  interactive (semantic fallback, explanation): provider.sort = "latency"
  background batch:                             provider.sort = "throughput"
"""
from __future__ import annotations

import time
from typing import Any, Literal

import httpx
from pydantic import ValidationError

from masterone.llm import audit as llm_audit
from masterone.llm.audit import LLMAuditRecord, hash_request, hash_response, new_request_id
from masterone.llm.circuit_breaker import CircuitBreaker, LLMCircuitOpenError, get_circuit_breaker
from masterone.llm.config import LLMSettings
from masterone.llm.contracts import LLMRequestStatus
from masterone.llm.metrics import LLMMetrics, get_metrics
from masterone.llm.retry import RETRYABLE_HTTP_CODES, retry_async

RoutingMode = Literal["latency", "throughput", "price"]

# ─── Exceptions ───────────────────────────────────────────────────────────────

class LLMError(Exception):
    """Base class cho mọi lỗi từ LLM subsystem."""


class LLMHTTPError(LLMError):
    def __init__(self, status_code: int, body: str) -> None:
        super().__init__(f"HTTP {status_code}: {body[:200]}")
        self.status_code = status_code


class _LLMNonRetryableHTTPError(LLMError):
    """4xx không được retry (401, 400, 403...). Không expose ra ngoài module."""
    def __init__(self, status_code: int, body: str) -> None:
        super().__init__(f"HTTP {status_code}: {body[:200]}")
        self.status_code = status_code


class LLMTimeoutError(LLMError):
    pass


class LLMSchemaError(LLMError):
    """Structured output không conform schema sau max_retries."""


class LLMDisabledError(LLMError):
    """LLM bị tắt qua feature flag."""


# ─── Client ───────────────────────────────────────────────────────────────────

class LLMClient:
    """Async OpenRouter client với structured output, retry, audit.

    Dùng một instance duy nhất (singleton qua get_llm_client()).
    """

    def __init__(
        self,
        settings: LLMSettings,
        *,
        circuit_breaker: CircuitBreaker | None = None,
        metrics: LLMMetrics | None = None,
    ) -> None:
        self._settings = settings
        self._breaker = circuit_breaker or get_circuit_breaker()
        self._metrics = metrics or get_metrics()
        self._http = httpx.AsyncClient(
            base_url=settings.openrouter_base_url,
            headers={
                "Authorization": f"Bearer {settings.require_api_key()}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://masterone.internal",
                "X-Title": "MasterOne-MDM",
            },
            timeout=settings.request_timeout_seconds,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def generate_structured(
        self,
        *,
        model: str,
        messages: list[dict],
        json_schema: dict,
        routing_mode: RoutingMode = "latency",
        request_type: str = "unknown",
        prompt_version: str = "V1",
        schema_version: str = "V1",
        pair_id: str | None = None,
    ) -> dict:
        """Gọi OpenRouter và trả về JSON dict đã validate schema.

        Raises:
            LLMDisabledError — feature flag tắt
            LLMTimeoutError  — timeout sau retries
            LLMHTTPError     — 4xx/5xx không retry được
            LLMSchemaError   — output không conform schema sau retries
        """
        if not self._settings.enabled:
            raise LLMDisabledError("LLM_ENABLED=false")

        # Circuit breaker check — trước khi allocate bất kỳ resource nào
        self._breaker.check()

        request_id = new_request_id()
        req_payload = {
            "model": model,
            "messages": messages,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": f"response_{schema_version.lower()}",
                    "strict": True,
                    "schema": json_schema,
                },
            },
            "provider": {"sort": routing_mode},
        }
        req_hash = hash_request(req_payload)
        start = time.monotonic()

        async def _call() -> dict:
            try:
                resp = await self._http.post("/chat/completions", json=req_payload)
            except httpx.TimeoutException as exc:
                raise LLMTimeoutError(str(exc)) from exc
            except httpx.RequestError as exc:
                raise LLMError(f"Network error: {exc}") from exc

            if resp.status_code in RETRYABLE_HTTP_CODES:
                raise LLMHTTPError(resp.status_code, resp.text)
            if resp.status_code >= 400:
                # 4xx không retryable (e.g. 401 bad key, 400 bad request)
                # Raise exception riêng không phải LLMHTTPError để retry_async không bắt
                body = resp.text
                _write_audit(
                    request_id, request_type, model, prompt_version, schema_version,
                    req_hash, start, pair_id,
                    status=LLMRequestStatus.LLM_ERROR,
                    error_code=f"HTTP_{resp.status_code}",
                )
                raise _LLMNonRetryableHTTPError(resp.status_code, body)

            return resp.json()

        raw: dict
        try:
            raw = await retry_async(
                _call,
                max_retries=self._settings.max_retries,
                # _LLMNonRetryableHTTPError không nằm trong list → không retry
                retryable_exc=(LLMHTTPError, LLMTimeoutError),
            )
        except LLMTimeoutError:
            latency_ms = int((time.monotonic() - start) * 1000)
            _write_audit(
                request_id, request_type, model, prompt_version, schema_version,
                req_hash, start, pair_id,
                status=LLMRequestStatus.TIMEOUT,
                error_code="TIMEOUT",
            )
            self._breaker.record_failure()
            self._metrics.record_request(
                request_type=request_type, status="TIMEOUT",
                latency_ms=latency_ms,
            )
            raise
        except _LLMNonRetryableHTTPError as exc:
            # Đã audit bên trong _call(); re-raise as LLMHTTPError cho caller
            self._breaker.record_failure()
            self._metrics.record_request(
                request_type=request_type, status=f"LLM_ERROR_{exc.status_code}",
                latency_ms=int((time.monotonic() - start) * 1000),
            )
            raise LLMHTTPError(exc.status_code, str(exc)) from exc
        except LLMHTTPError as exc:
            latency_ms = int((time.monotonic() - start) * 1000)
            _write_audit(
                request_id, request_type, model, prompt_version, schema_version,
                req_hash, start, pair_id,
                status=LLMRequestStatus.LLM_ERROR,
                error_code=f"HTTP_{exc.status_code}",
            )
            self._breaker.record_failure()
            status_key = "LLM_ERROR_429" if exc.status_code == 429 else "LLM_ERROR"
            self._metrics.record_request(
                request_type=request_type, status=status_key,
                latency_ms=latency_ms,
            )
            raise

        # Parse content
        try:
            content_str: str = raw["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            _write_audit(
                request_id, request_type, model, prompt_version, schema_version,
                req_hash, start, pair_id,
                status=LLMRequestStatus.LLM_ERROR,
                error_code="MISSING_CONTENT",
            )
            raise LLMSchemaError("Missing choices[0].message.content") from exc

        import json
        try:
            result: dict = json.loads(content_str)
        except json.JSONDecodeError as exc:
            _write_audit(
                request_id, request_type, model, prompt_version, schema_version,
                req_hash, start, pair_id,
                status=LLMRequestStatus.LLM_ERROR,
                error_code="JSON_DECODE_ERROR",
            )
            raise LLMSchemaError(f"Response không phải JSON hợp lệ: {exc}") from exc

        usage = raw.get("usage", {})
        in_tok = usage.get("prompt_tokens") or 0
        out_tok = usage.get("completion_tokens") or 0
        latency_ms = int((time.monotonic() - start) * 1000)
        _write_audit(
            request_id, request_type, model, prompt_version, schema_version,
            req_hash, start, pair_id,
            status=LLMRequestStatus.SUCCESS,
            response_hash=hash_response(result),
            input_tokens=in_tok,
            output_tokens=out_tok,
        )
        self._breaker.record_success()
        self._metrics.record_request(
            request_type=request_type, status="SUCCESS",
            latency_ms=latency_ms,
            input_tokens=in_tok,
            output_tokens=out_tok,
        )

        # Attach provenance metadata vào result để các service upstream dùng
        result["__llm_meta__"] = {
            "request_id": request_id,
            "request_hash": req_hash,
            "response_hash": hash_response(result),
            "model": model,
            "prompt_version": prompt_version,
            "schema_version": schema_version,
            "provider": "openrouter",
        }
        return result


# ─── Audit helper ─────────────────────────────────────────────────────────────

def _write_audit(
    request_id: str,
    request_type: str,
    model: str,
    prompt_version: str,
    schema_version: str,
    request_hash: str,
    start: float,
    pair_id: str | None,
    *,
    status: LLMRequestStatus,
    error_code: str | None = None,
    response_hash: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> None:
    latency_ms = int((time.monotonic() - start) * 1000)
    llm_audit.record(LLMAuditRecord(
        request_id=request_id,
        request_type=request_type,
        model=model,
        provider="openrouter",
        prompt_version=prompt_version,
        schema_version=schema_version,
        request_hash=request_hash,
        pair_id=pair_id,
        status=status,
        response_hash=response_hash,
        latency_ms=latency_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        error_code=error_code,
    ))


# ─── Singleton ────────────────────────────────────────────────────────────────

_client: LLMClient | None = None


def get_llm_client(settings: LLMSettings | None = None) -> LLMClient:
    """Trả về singleton LLMClient. Khởi tạo lần đầu với settings."""
    global _client
    if _client is None:
        from masterone.llm.config import get_llm_settings
        _client = LLMClient(settings or get_llm_settings())
    return _client


def reset_client() -> None:
    """Reset singleton — dùng trong tests."""
    global _client
    _client = None
