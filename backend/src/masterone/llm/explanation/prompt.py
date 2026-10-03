"""Explanation prompts — tiếng Việt, không có recommendation merge/reject.

Versioning: tăng PROMPT_VERSION khi thay đổi bất kỳ string nào.

Nguyên tắc:
- LLM chỉ diễn giải evidence đã được backend cung cấp.
- Không tự tạo score, count, probability.
- Không đề xuất MERGE hoặc REJECT.
- Ngắn gọn, phù hợp Data Steward đọc trong enterprise UI.
"""

PROMPT_VERSION = "EXPLANATION_V1"
GROUP_PROMPT_VERSION = "GROUP_EXPLANATION_V1"

# ── Pair explanation ──────────────────────────────────────────────────────────

PAIR_SYSTEM_PROMPT = """Bạn là trợ lý giải thích bằng chứng entity resolution cho Data Steward.

Nguyên tắc bắt buộc:
- Chỉ diễn giải các bằng chứng đã được backend cung cấp. Không bịa thêm số liệu, score, hoặc xác suất.
- Không đề xuất gộp (merge) hoặc từ chối. Quyết định là của Data Steward và policy engine.
- Dùng tiếng Việt ngắn gọn, phù hợp giao diện review doanh nghiệp.
- Không được nhắc lại toàn bộ dữ liệu thô — chỉ giải thích ý nghĩa của các tín hiệu.
- Trả về đúng cấu trúc JSON yêu cầu."""


def build_pair_messages(input_data: dict) -> list[dict]:
    """Xây dựng messages cho pair explanation.

    input_data: dict từ ExplanationInput (đã serialize).
    Backend đã tính tất cả số liệu — LLM chỉ diễn giải.
    """
    comparisons = input_data.get("comparisons", {})
    comp_lines = "\n".join(
        f"  {field}: {state}" for field, state in comparisons.items()
    )

    norm_tags = input_data.get("normalization_tags", [])
    blocking = input_data.get("blocking_reasons", [])
    policy = input_data.get("policy_result", {})

    user_content = f"""Giải thích bằng chứng cho cặp bản ghi sau cho Data Steward:

Nhóm phân loại: {input_data.get('primary_group', '')} / {input_data.get('decision_subgroup', '')}
Quyết định pipeline: {input_data.get('decision', '')}
Match weight (do backend tính): {input_data.get('match_weight', 0):.1f}

So sánh từng trường:
{comp_lines}

Normalization tags: {', '.join(norm_tags) if norm_tags else 'Không có'}
Blocking reasons: {', '.join(blocking) if blocking else 'Không có'}
Kết quả policy: eligible={policy.get('eligible', False)}, lý do block={policy.get('blocked_reason', 'Không có')}

Hãy giải thích ngắn gọn:
- summary: tóm tắt tình trạng cặp này (tối đa 500 ký tự)
- supporting_points: tối đa 4 điểm ủng hộ là cùng người
- risk_points: tối đa 4 điểm rủi ro hoặc cần chú ý

Không đề xuất merge hoặc từ chối. Không bịa số liệu ngoài những gì đã cung cấp."""

    return [
        {"role": "system", "content": PAIR_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


# ── Group explanation ─────────────────────────────────────────────────────────

GROUP_SYSTEM_PROMPT = """Bạn là trợ lý giải thích pattern của nhóm cặp bản ghi cho Data Steward.

Nguyên tắc:
- Chỉ dùng các số liệu tổng hợp do backend cung cấp. Không bịa thêm.
- Không đề xuất gộp hay từ chối.
- Dùng tiếng Việt ngắn gọn.
- Trả về đúng cấu trúc JSON."""


def build_group_messages(group_data: dict) -> list[dict]:
    """Xây dựng messages cho group/subgroup explanation.

    group_data: dict với group, subgroup, pair_count, dominant_signature, stats.
    """
    stats = group_data.get("stats", {})
    stats_lines = "\n".join(f"  {k}: {v}" for k, v in stats.items())

    user_content = f"""Mô tả pattern chung của nhóm sau cho Data Steward:

Nhóm: {group_data.get('group', '')} / {group_data.get('subgroup', '')}
Số cặp: {group_data.get('pair_count', 0)}
Signature chủ đạo: {group_data.get('dominant_signature', '')}

Thống kê tổng hợp (do backend tính):
{stats_lines if stats_lines else '  Không có'}

Hãy trả về:
- pattern_summary: mô tả ngắn pattern phổ biến nhất (tối đa 400 ký tự)
- common_signals: tối đa 4 tín hiệu chung trong nhóm này
- review_guidance: hướng dẫn ngắn cho Steward khi xem nhóm này (tối đa 300 ký tự)

Không đề xuất merge hay từ chối. Không bịa số liệu."""

    return [
        {"role": "system", "content": GROUP_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
