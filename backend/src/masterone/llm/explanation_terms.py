"""Human-readable, aggregate-only evidence sent to the explanation model."""

FIELD_LABELS = {
    "name": "họ tên",
    "phone": "số điện thoại",
    "email": "email",
    "address": "địa chỉ",
}

STATE_LABELS = {
    "EX": "khớp chính xác",
    "NX": "khớp sau chuẩn hóa cách viết",
    "NA": "tên tương đương sau khi bỏ dấu",
    "HS": "tương đồng cao",
    "MS": "tương đồng vừa",
    "WS": "tương đồng thấp",
    "CL": "gần khớp nhưng cần kiểm tra",
    "CF": "có xung đột đáng kể",
    "DF": "khác nhau nhưng chưa đủ để loại trừ",
    "MI": "thiếu dữ liệu để đối chiếu",
    "IV": "dữ liệu không hợp lệ",
    "SH": "giá trị dùng chung hoặc kém tin cậy",
    "NC": "không thể so sánh",
}

SIGNAL_LABELS = {
    "exact": "khớp chính xác",
    "similar": "tương đồng",
    "different": "khác nhau",
    "ambiguous": "chưa rõ ràng",
    "missing": "thiếu dữ liệu",
}

GROUP_LABELS = {
    "A": "có tín hiệu định danh mạnh",
    "C": "cần xem xét thêm tín hiệu định danh",
    "D": "có tín hiệu xung đột cần xem xét",
}

SUBGROUP_LABELS = {
    "A1_NAME_PHONE": "họ tên và số điện thoại hỗ trợ đối chiếu",
    "A2_NAME_EMAIL": "họ tên và email hỗ trợ đối chiếu",
    "A3_NAME_PHONE_DOB": "họ tên, số điện thoại và ngày sinh hỗ trợ đối chiếu",
    "A3_NAME_GOV_ID": "họ tên và mã định danh hỗ trợ đối chiếu",
    "C1_EMAIL_DOB": "email và ngày sinh hỗ trợ đối chiếu nhưng thiếu tín hiệu khác",
    "C2_PHONE_NAME": "số điện thoại và họ tên hỗ trợ đối chiếu nhưng thiếu tín hiệu khác",
    "C3_GOV_ID_ONLY": "chỉ có tín hiệu từ mã định danh",
    "C_MULTI": "có nhiều tín hiệu yếu cần xem xét",
    "C_NAME_ANCHOR_MISSING": "thiếu tín hiệu họ tên cần thiết",
    "C_NAME_ANCHOR_WEAK": "tín hiệu họ tên còn yếu",
    "D1_NAME_PHONE_CONFLICT": "số điện thoại xung đột với tín hiệu họ tên",
    "D2_NAME_CONFLICT": "họ tên xung đột với tín hiệu liên hệ",
    "D3_IDENTITY_CONFLICT": "thông tin định danh xung đột",
    "D4_DOB_CONFLICT": "ngày sinh xung đột",
    "D_MULTI": "có nhiều trường xung đột",
}

REASON_LABELS = {
    "ELIGIBLE": "đáp ứng điều kiện của quy tắc đối chiếu",
    "NO_ELIGIBLE_SUBGROUP": "chưa đáp ứng điều kiện của nhóm quy tắc",
    "NO_VALID_IDENTITY_ANCHOR": "thiếu tín hiệu định danh đủ tin cậy",
    "WEIGHT_BELOW_FLOOR": "chưa đạt ngưỡng bằng chứng tối thiểu",
    "HARD_CONFLICT": "có xung đột định danh rõ ràng",
    "NON_MATCH": "có quyết định không liên kết trước đó",
    "HARD_VERIFIED_PHONE": "số điện thoại đã xác minh xung đột",
    "HARD_VERIFIED_EMAIL": "email đã xác minh xung đột",
    "NON_HARD_CONFLICT": "có khác biệt cần xem xét",
    "MISSING_REQUIRED_NAME_ANCHOR": "thiếu tín hiệu họ tên cần thiết",
    "SAFE_COMPONENT_EDGE": "liên kết trong nhóm đã qua kiểm tra quy tắc",
    "CLUSTER_RISK": "nhóm có rủi ro khi liên kết",
    "CLUSTER_NOT_CONFIRMED": "nhóm chưa được xác nhận",
    "CLUSTER_GOV_ID_CONFLICT": "mã định danh trong nhóm xung đột",
    "CLUSTER_NEGATIVE_EDGE": "nhóm có cặp đã được xác định không liên kết",
    "CLUSTER_SHARED_VALUE_DOMINANT": "liên kết phụ thuộc nhiều vào thông tin liên hệ dùng chung",
    "CLUSTER_SIZE_EXCEEDED": "nhóm vượt giới hạn kích thước",
    "CLUSTER_BRIDGE_MISSING_EVIDENCE": "thiếu bằng chứng kết nối giữa các phần của nhóm",
    "CLUSTER_PAIR_PERMISSION_CONFLICT": "có cặp trong nhóm không được phép liên kết",
    "MANUAL_SPLIT": "nhóm đã được tách thủ công",
    "REVIEW_REQUIRED": "cần kiểm tra thêm",
}


def _reason_label(code):
    if code.startswith("CLUSTER_SOURCE_UNIQUE_CONFLICT_"):
        return "có xung đột dữ liệu từ cùng nguồn"
    if code.startswith("CLUSTER_SOURCE_DOMINANCE_"):
        return "một nguồn dữ liệu chiếm ưu thế trong nhóm"
    return REASON_LABELS.get(code, "lý do cần kiểm tra thêm")


def _labeled_counts(counts, labels):
    result = {}
    for code, count in counts.items():
        label = labels.get(code, "trạng thái cần kiểm tra thêm")
        result[label] = result.get(label, 0) + count
    return result


def explanation_facts(payload):
    """Translate validated aggregate codes; never expose source values or unknown codes."""
    facts = {}
    for key, label in (("record_count", "số bản ghi"), ("unique_record_count", "số bản ghi duy nhất"),
                       ("pair_count", "số cặp đối chiếu")):
        if key in payload:
            facts[label] = payload[key]
    for key, label, labels in (("field_state_counts", "trạng thái theo trường", STATE_LABELS),
                               ("field_signal_counts", "tín hiệu theo trường", SIGNAL_LABELS)):
        if key in payload:
            facts[label] = {FIELD_LABELS[field]: _labeled_counts(counts, labels)
                            for field, counts in payload[key].items()}
    if "reason_counts" in payload:
        reasons = {}
        for code, count in payload["reason_counts"].items():
            label = _reason_label(code)
            reasons[label] = reasons.get(label, 0) + count
        facts["lý do theo quy tắc"] = reasons
    if "group" in payload:
        facts["nhóm tín hiệu"] = GROUP_LABELS.get(payload["group"], "nhóm cần kiểm tra thêm")
    if "subgroup" in payload:
        facts["kiểu tín hiệu"] = SUBGROUP_LABELS.get(payload["subgroup"], "kiểu tín hiệu cần kiểm tra thêm")
    return facts
