"""LLM subsystem config — derived from root Settings.

Acceptance criteria Phase L0:
- app start fail-fast nếu LLM_ENABLED=true nhưng thiếu OPENROUTER_API_KEY
- app vẫn start nếu LLM_ENABLED=false hoặc LLM_SEMANTIC_ENABLED=false
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from masterone.config import Settings, get_settings


class LLMConfigError(RuntimeError):
    """Raised on startup khi LLM config không hợp lệ."""


@dataclass(frozen=True)
class LLMSettings:
    enabled: bool
    semantic_enabled: bool
    explanation_enabled: bool

    openrouter_api_key: str
    openrouter_base_url: str

    semantic_model: str
    explanation_model: str

    semantic_batch_size: int
    max_concurrency: int
    request_timeout_seconds: int
    max_retries: int

    cache_ttl_seconds: int
    allow_auto_merge_evidence: bool

    max_semantic_calls_per_run: int
    max_explanation_calls_per_run: int
    max_input_tokens_per_request: int
    max_output_tokens_semantic: int
    max_output_tokens_explanation: int

    def require_api_key(self) -> str:
        """Return API key hoặc raise nếu thiếu."""
        if not self.openrouter_api_key:
            raise LLMConfigError(
                "OPENROUTER_API_KEY is required when LLM_ENABLED=true. "
                "Set LLM_ENABLED=false to skip LLM integration."
            )
        return self.openrouter_api_key


def _validate(s: LLMSettings) -> None:
    """Fail-fast validation — chạy một lần khi app khởi động."""
    if not s.enabled:
        return
    if not s.openrouter_api_key:
        raise LLMConfigError(
            "LLM_ENABLED=true nhưng OPENROUTER_API_KEY chưa được set. "
            "Thêm OPENROUTER_API_KEY vào .env hoặc set LLM_ENABLED=false."
        )
    if s.allow_auto_merge_evidence:
        # Chỉ cảnh báo — không block — vì cần benchmark trước khi bật
        import warnings
        warnings.warn(
            "LLM_ALLOW_AUTO_MERGE_EVIDENCE=true: LLM evidence có thể kích hoạt "
            "auto merge. Chỉ bật sau khi đã benchmark accuracy trên production data.",
            stacklevel=3,
        )


@lru_cache(maxsize=1)
def get_llm_settings(*, _settings: Settings | None = None) -> LLMSettings:
    """Return validated LLMSettings singleton.

    Truyền _settings để override trong tests; production dùng get_settings().
    """
    root = _settings or get_settings()

    s = LLMSettings(
        enabled=root.llm_enabled,
        semantic_enabled=root.llm_semantic_enabled,
        explanation_enabled=root.llm_explanation_enabled,
        openrouter_api_key=root.openrouter_api_key,
        openrouter_base_url=root.openrouter_base_url,
        semantic_model=root.llm_semantic_model,
        explanation_model=root.llm_explanation_model,
        semantic_batch_size=root.llm_semantic_batch_size,
        max_concurrency=root.llm_max_concurrency,
        request_timeout_seconds=root.llm_request_timeout_seconds,
        max_retries=root.llm_max_retries,
        cache_ttl_seconds=root.llm_cache_ttl_seconds,
        allow_auto_merge_evidence=root.llm_allow_auto_merge_evidence,
        max_semantic_calls_per_run=root.llm_max_semantic_calls_per_run,
        max_explanation_calls_per_run=root.llm_max_explanation_calls_per_run,
        max_input_tokens_per_request=root.llm_max_input_tokens_per_request,
        max_output_tokens_semantic=root.llm_max_output_tokens_semantic,
        max_output_tokens_explanation=root.llm_max_output_tokens_explanation,
    )
    _validate(s)
    return s
