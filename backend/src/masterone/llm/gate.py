"""LLM Gate — quyết định có gọi Semantic Fallback không.

Nguyên tắc: phần lớn candidate pairs KHÔNG được gọi LLM.
Target: <= 1–5% candidate pairs gọi semantic fallback.

Gate trả FALSE nếu:
  - hard_conflict đã có
  - field đã có state strong (EX/NX/NA)
  - field missing / invalid / shared
  - pair dưới evidence floor
  - field không trong allowlist semantic
  - pair đã đủ evidence deterministic (không cần LLM)

Gate trả TRUE chỉ khi field ở "uncertain band" và pair có giá trị xem xét.
"""
from __future__ import annotations

from dataclasses import dataclass

# Fields được phép dùng semantic fallback
SEMANTIC_FIELD_ALLOWLIST = frozenset({"address", "organization_name", "free_text"})

# States "đã chắc chắn" — gate FALSE ngay
_CERTAIN_STATES = frozenset({"EX", "NX", "NA"})

# States "không có giá trị so sánh" — gate FALSE
_SKIP_STATES = frozenset({"MI", "IV", "SH", "NC", "CF", "DF"})

# Fuzzy uncertain band cho address (configurable)
_ADDRESS_FUZZY_LOW = 0.60
_ADDRESS_FUZZY_HIGH = 0.85


@dataclass(frozen=True)
class PairFieldContext:
    """Context cho một field trong một candidate pair.

    Attributes:
        pair_id: định danh pair
        field: tên field (phải trong SEMANTIC_FIELD_ALLOWLIST)
        current_state: state hiện tại từ deterministic pipeline
        current_provenance: "RULE" | "FUZZY" | "DICT" | ...
        fuzzy_score: giá trị fuzzy similarity (0–1), None nếu chưa tính
        hard_conflict: pair đã có hard conflict từ bất kỳ field nào
        has_supporting_evidence: pair có ít nhất 1 identity anchor EX/NX/NA/HS
        left_value: giá trị gốc bên trái
        right_value: giá trị gốc bên phải
        left_normalized: giá trị đã normalize
        right_normalized: giá trị đã normalize
    """
    pair_id: str
    field: str
    current_state: str
    current_provenance: str = "RULE"
    fuzzy_score: float | None = None
    hard_conflict: bool = False
    has_supporting_evidence: bool = False
    left_value: str = ""
    right_value: str = ""
    left_normalized: str = ""
    right_normalized: str = ""


class LLMGate:
    """Quyết định có nên gọi semantic fallback cho một field context không."""

    def should_run_semantic_fallback(self, ctx: PairFieldContext) -> bool:
        """Trả về True chỉ khi semantic fallback có ý nghĩa cho field này."""
        # 1. Hard conflict từ bất kỳ field nào → không gọi
        if ctx.hard_conflict:
            return False

        # 2. Field không trong allowlist
        if ctx.field not in SEMANTIC_FIELD_ALLOWLIST:
            return False

        # 3. State đã chắc chắn hoặc không có giá trị so sánh
        if ctx.current_state in _CERTAIN_STATES:
            return False
        if ctx.current_state in _SKIP_STATES:
            return False

        # 4. Giá trị trống
        if not ctx.left_value.strip() or not ctx.right_value.strip():
            return False

        # 5. Field-specific uncertain band check
        if ctx.field == "address":
            return self._gate_address(ctx)
        if ctx.field == "organization_name":
            return self._gate_org_name(ctx)
        if ctx.field == "free_text":
            return self._gate_free_text(ctx)

        return False

    # ─── Field-specific gates ──────────────────────────────────────────────────

    def _gate_address(self, ctx: PairFieldContext) -> bool:
        """Address: uncertain khi rule/dict chưa resolve và fuzzy trong [0.60, 0.85)."""
        # Rule đã resolve rõ ràng thành HS → không cần LLM
        if ctx.current_state == "HS" and ctx.current_provenance == "RULE":
            return False

        # Fuzzy trong uncertain band
        if ctx.fuzzy_score is not None:
            in_uncertain_band = _ADDRESS_FUZZY_LOW <= ctx.fuzzy_score < _ADDRESS_FUZZY_HIGH
            if not in_uncertain_band:
                return False

        # Cần ít nhất 1 identity anchor để pair đáng review
        if not ctx.has_supporting_evidence:
            return False

        return True

    def _gate_org_name(self, ctx: PairFieldContext) -> bool:
        """Organization name: MS/WS và có supporting evidence."""
        if ctx.current_state not in {"MS", "WS", "HS"}:
            return False
        # HS từ RULE đã đủ — không cần LLM
        if ctx.current_state == "HS" and ctx.current_provenance == "RULE":
            return False
        return ctx.has_supporting_evidence

    def _gate_free_text(self, ctx: PairFieldContext) -> bool:
        """Free text: chỉ gọi nếu có supporting evidence."""
        if ctx.current_state not in {"MS", "WS"}:
            return False
        return ctx.has_supporting_evidence
