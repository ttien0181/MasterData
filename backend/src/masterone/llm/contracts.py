"""LLM contracts — input/output schemas cho Semantic Fallback và Explanation.

Tất cả machine-oriented LLM response phải conform các Pydantic models này.
Không parse text tự do. Validation fail → retry → LLM_ERROR → Review Queue.

Nguyên tắc:
- SemanticResult KHÔNG có MERGE/DO_NOT_MERGE/CF
- ExplanationOutput KHÔNG có recommendation MERGE/REJECT
- Numeric confidence không có trong MVP (tránh calibration giả)
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


# ─── Enums ────────────────────────────────────────────────────────────────────

class SemanticField(str, Enum):
    ADDRESS = "address"
    ORGANIZATION_NAME = "organization_name"
    FREE_TEXT = "free_text"


class SemanticResult(str, Enum):
    """Chỉ 4 giá trị được phép — không có MERGE, CF, DO_NOT_MERGE."""
    EQUIVALENT = "EQUIVALENT"
    LIKELY_EQUIVALENT = "LIKELY_EQUIVALENT"
    DIFFERENT = "DIFFERENT"
    UNCERTAIN = "UNCERTAIN"


class SemanticReasonCode(str, Enum):
    ABBREVIATION_EQUIVALENT = "ABBREVIATION_EQUIVALENT"
    TOKEN_REORDER = "TOKEN_REORDER"
    ADMINISTRATIVE_VARIANT = "ADMINISTRATIVE_VARIANT"
    SEMANTIC_NAME_EQUIVALENT = "SEMANTIC_NAME_EQUIVALENT"
    MEANING_DIFFERENT = "MEANING_DIFFERENT"
    INSUFFICIENT_CONTEXT = "INSUFFICIENT_CONTEXT"


class LLMRequestStatus(str, Enum):
    SUCCESS = "SUCCESS"
    RETRY = "RETRY"
    LLM_ERROR = "LLM_ERROR"
    TIMEOUT = "TIMEOUT"
    CIRCUIT_OPEN = "CIRCUIT_OPEN"
    CACHED = "CACHED"
    SKIPPED_BY_GATE = "SKIPPED_BY_GATE"


# ─── Semantic Fallback ────────────────────────────────────────────────────────

class SemanticInputItem(BaseModel):
    """Một item trong batch request gửi lên OpenRouter."""
    pair_id: str
    field: SemanticField
    left_value: str
    right_value: str
    left_normalized: str = ""
    right_normalized: str = ""


class SemanticOutputItem(BaseModel):
    """Một item trong batch response từ LLM — strict schema."""
    pair_id: str
    field: SemanticField
    result: SemanticResult
    reason_code: SemanticReasonCode
    short_reason: str = Field(..., max_length=240)

    @field_validator("result")
    @classmethod
    def result_cannot_be_merge(cls, v: SemanticResult) -> SemanticResult:
        # Guard thêm — enum đã đủ, nhưng explicit để rõ intent
        forbidden = {"MERGE", "DO_NOT_MERGE", "AUTO_MERGE", "APPROVE", "REJECT", "CF"}
        if v.value in forbidden:
            raise ValueError(f"SemanticResult '{v.value}' bị cấm — LLM không được quyết định merge")
        return v


class SemanticBatchRequest(BaseModel):
    items: list[SemanticInputItem] = Field(..., min_length=1, max_length=50)


class SemanticBatchResponse(BaseModel):
    items: list[SemanticOutputItem]

    def validate_pair_ids(self, request: SemanticBatchRequest) -> None:
        """Kiểm tra pair_id set trong response == request. Raise nếu không khớp."""
        req_ids = {item.pair_id for item in request.items}
        res_ids = {item.pair_id for item in self.items}
        missing = req_ids - res_ids
        extra = res_ids - req_ids
        if missing or extra:
            raise ValueError(
                f"Batch pair_id mismatch — missing: {missing}, extra: {extra}. "
                "Không map theo index khi có pair_id."
            )


# ─── Evidence (lưu vào DB sau khi map sang Comparison State) ─────────────────

# Mapping LLM result → Comparison State (mục 10 plan)
# LLM DIFFERENT không sinh CF — chỉ DF hoặc NC
SEMANTIC_STATE_MAP: dict[SemanticResult, str] = {
    SemanticResult.EQUIVALENT: "HS",
    SemanticResult.LIKELY_EQUIVALENT: "HS",
    SemanticResult.DIFFERENT: "DF",
    SemanticResult.UNCERTAIN: "NC",
}

# Phiên bản mapping — tăng khi thay đổi SEMANTIC_STATE_MAP
LLM_STATE_MAP_VERSION = "LLM_STATE_MAP_V1"


class SemanticEvidence(BaseModel):
    """Evidence record lưu DB sau khi semantic fallback hoàn tất."""
    pair_id: str
    field: str
    # Comparison State đã map (HS/DF/NC) — KHÔNG phải CF
    mapped_state: str
    # Raw LLM result trước khi map
    semantic_result: SemanticResult
    reason_code: SemanticReasonCode
    short_reason: str

    # Provenance — bắt buộc theo mục 23
    provenance: str = "LLM"
    model: str
    prompt_version: str
    schema_version: str
    map_version: str = LLM_STATE_MAP_VERSION
    provider: str = "openrouter"
    request_id: str
    request_hash: str
    response_hash: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # LLM evidence không bao giờ được kích hoạt auto merge theo mặc định
    eligible_for_auto_merge: bool = False


# ─── Explanation ──────────────────────────────────────────────────────────────

class ExplanationInput(BaseModel):
    """Deterministic evidence từ backend — LLM chỉ diễn giải, không tự tính."""
    pair_id: str
    primary_group: str
    decision_subgroup: str
    decision: str
    match_weight: float
    comparisons: dict[str, str]  # field → state (HS/CF/EX...)
    normalization_tags: list[str] = []
    blocking_reasons: list[str] = []
    policy_result: dict[str, Any] = {}


class ExplanationOutput(BaseModel):
    """LLM output schema — không có recommendation MERGE/REJECT."""
    summary: str = Field(..., max_length=500)
    supporting_points: list[str] = Field(..., max_length=4)
    risk_points: list[str] = Field(..., max_length=4)

    @field_validator("supporting_points", "risk_points")
    @classmethod
    def validate_no_merge_recommendation(cls, v: list[str]) -> list[str]:
        forbidden_keywords = {"merge", "gộp", "từ chối", "reject", "approve"}
        for item in v:
            lower = item.lower()
            if any(kw in lower for kw in forbidden_keywords):
                raise ValueError(
                    f"ExplanationOutput không được chứa recommendation merge/reject: '{item}'"
                )
        return v
