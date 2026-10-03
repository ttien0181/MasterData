"""Phase L0 acceptance tests.

Acceptance criteria:
- app start fail-fast nếu LLM_ENABLED=true nhưng thiếu OPENROUTER_API_KEY
- app vẫn start nếu LLM_ENABLED=false
- contracts: SemanticResult không có MERGE/CF
- contracts: batch pair_id integrity
- contracts: SEMANTIC_STATE_MAP không map sang CF
"""
import pytest
from pydantic import ValidationError

from masterone.config import Settings
from masterone.llm.config import LLMConfigError, LLMSettings, get_llm_settings
from masterone.llm.contracts import (
    SEMANTIC_STATE_MAP,
    SemanticBatchRequest,
    SemanticBatchResponse,
    SemanticField,
    SemanticInputItem,
    SemanticOutputItem,
    SemanticReasonCode,
    SemanticResult,
    ExplanationOutput,
)


# ─── helpers ──────────────────────────────────────────────────────────────────

def _settings(**overrides) -> Settings:
    """Tạo Settings với giá trị mặc định an toàn cho test."""
    defaults = dict(
        openrouter_api_key="",
        llm_enabled=False,
        llm_semantic_enabled=False,
        llm_explanation_enabled=False,
    )
    defaults.update(overrides)
    return Settings.model_construct(**defaults)


def _llm_settings(**overrides) -> LLMSettings:
    root = _settings(**overrides)
    # bypass lru_cache
    from masterone.llm import config as llm_cfg
    s = llm_cfg.LLMSettings(
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
    return s


# ─── Config / startup ─────────────────────────────────────────────────────────

def test_fail_fast_when_enabled_without_api_key():
    """LLM_ENABLED=true + thiếu key → LLMConfigError ngay khi validate."""
    from masterone.llm import config as llm_cfg
    s = _llm_settings(llm_enabled=True, openrouter_api_key="")
    with pytest.raises(LLMConfigError, match="OPENROUTER_API_KEY"):
        llm_cfg._validate(s)


def test_no_error_when_disabled():
    """LLM_ENABLED=false + thiếu key → không raise."""
    from masterone.llm import config as llm_cfg
    s = _llm_settings(llm_enabled=False, openrouter_api_key="")
    llm_cfg._validate(s)  # must not raise


def test_no_error_when_key_provided():
    """LLM_ENABLED=true + có key → không raise."""
    from masterone.llm import config as llm_cfg
    s = _llm_settings(llm_enabled=True, openrouter_api_key="sk-or-test-key")
    llm_cfg._validate(s)  # must not raise


def test_allow_auto_merge_evidence_defaults_false():
    s = _llm_settings(llm_enabled=False)
    assert s.allow_auto_merge_evidence is False


# ─── SemanticResult enum ──────────────────────────────────────────────────────

def test_semantic_result_only_four_values():
    allowed = {"EQUIVALENT", "LIKELY_EQUIVALENT", "DIFFERENT", "UNCERTAIN"}
    assert {r.value for r in SemanticResult} == allowed


def test_semantic_result_no_merge_values():
    forbidden = {"MERGE", "DO_NOT_MERGE", "AUTO_MERGE", "APPROVE", "REJECT", "CF"}
    for v in SemanticResult:
        assert v.value not in forbidden


# ─── SEMANTIC_STATE_MAP ───────────────────────────────────────────────────────

def test_state_map_never_produces_cf():
    """LLM DIFFERENT không sinh CF — chỉ DF hoặc NC (mục 10 plan)."""
    for result, state in SEMANTIC_STATE_MAP.items():
        assert state != "CF", (
            f"SemanticResult.{result.value} → '{state}': "
            "LLM không được sinh CF"
        )


def test_state_map_covers_all_results():
    for result in SemanticResult:
        assert result in SEMANTIC_STATE_MAP, f"Missing mapping for {result}"


# ─── Batch pair_id integrity ──────────────────────────────────────────────────

def _make_item(pair_id: str) -> SemanticInputItem:
    return SemanticInputItem(
        pair_id=pair_id,
        field=SemanticField.ADDRESS,
        left_value="12 N.Trãi, HN",
        right_value="12 Nguyễn Trãi, Hà Nội",
    )


def _make_output_item(pair_id: str) -> SemanticOutputItem:
    return SemanticOutputItem(
        pair_id=pair_id,
        field=SemanticField.ADDRESS,
        result=SemanticResult.LIKELY_EQUIVALENT,
        reason_code=SemanticReasonCode.ABBREVIATION_EQUIVALENT,
        short_reason="Tên đường viết tắt tương đương.",
    )


def test_batch_pair_id_integrity_ok():
    req = SemanticBatchRequest(items=[_make_item("p001"), _make_item("p002")])
    res = SemanticBatchResponse(items=[_make_output_item("p001"), _make_output_item("p002")])
    res.validate_pair_ids(req)  # must not raise


def test_batch_pair_id_missing_item():
    req = SemanticBatchRequest(items=[_make_item("p001"), _make_item("p002")])
    res = SemanticBatchResponse(items=[_make_output_item("p001")])  # thiếu p002
    with pytest.raises(ValueError, match="missing"):
        res.validate_pair_ids(req)


def test_batch_pair_id_extra_item():
    req = SemanticBatchRequest(items=[_make_item("p001")])
    res = SemanticBatchResponse(items=[_make_output_item("p001"), _make_output_item("p999")])
    with pytest.raises(ValueError, match="extra"):
        res.validate_pair_ids(req)


def test_batch_pair_id_unknown_id():
    req = SemanticBatchRequest(items=[_make_item("p001")])
    res = SemanticBatchResponse(items=[_make_output_item("p_UNKNOWN")])
    with pytest.raises(ValueError):
        res.validate_pair_ids(req)


# ─── ExplanationOutput safety ─────────────────────────────────────────────────

def test_explanation_no_merge_recommendation():
    with pytest.raises(ValidationError):
        ExplanationOutput(
            summary="Hai bản ghi có khả năng là một người.",
            supporting_points=["Email khớp chính xác.", "Hãy merge ngay."],
            risk_points=[],
        )


def test_explanation_valid():
    out = ExplanationOutput(
        summary="Số điện thoại có xung đột cần kiểm tra.",
        supporting_points=["Email khớp chính xác.", "Ngày sinh khớp."],
        risk_points=["Số điện thoại hiện hành bị đánh dấu xung đột."],
    )
    assert out.summary
