"""Map LLM SemanticOutputItem → SemanticEvidence với provenance đầy đủ.

Mapping nằm ở backend, không nằm trong prompt.
LLM DIFFERENT → DF (không phải CF).
"""
from __future__ import annotations

from masterone.llm.contracts import (
    SEMANTIC_STATE_MAP,
    LLM_STATE_MAP_VERSION,
    SemanticEvidence,
    SemanticOutputItem,
)
from masterone.llm.semantic.prompt import PROMPT_VERSION
from masterone.llm.semantic.schema import SCHEMA_VERSION


def map_to_evidence(
    item: SemanticOutputItem,
    *,
    model: str,
    request_id: str,
    request_hash: str,
    response_hash: str,
) -> SemanticEvidence:
    """Convert LLM output item → SemanticEvidence với provenance."""
    mapped_state = SEMANTIC_STATE_MAP[item.result]

    # Guard: đảm bảo không sinh CF (defense-in-depth, state map đã đúng)
    assert mapped_state != "CF", (
        f"CRITICAL: mapped_state=CF từ LLM result {item.result} — vi phạm kiến trúc"
    )

    return SemanticEvidence(
        pair_id=item.pair_id,
        field=item.field.value,
        mapped_state=mapped_state,
        semantic_result=item.result,
        reason_code=item.reason_code,
        short_reason=item.short_reason,
        provenance="LLM",
        model=model,
        prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA_VERSION,
        map_version=LLM_STATE_MAP_VERSION,
        provider="openrouter",
        request_id=request_id,
        request_hash=request_hash,
        response_hash=response_hash,
        eligible_for_auto_merge=False,
    )
