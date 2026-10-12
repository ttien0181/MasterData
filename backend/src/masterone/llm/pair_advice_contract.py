"""Grounded symbolic reasons; provider prose cannot assert invented verified facts."""
FIELDS = ['name', 'phone', 'email', 'dob', 'address', 'government_id']
STATES = ['EX', 'NX', 'NA', 'HS', 'MS', 'WS', 'CL', 'DF', 'CF', 'MI', 'IV', 'SH', 'NC']
REASONS = {
    'CORROBORATING_SIGNALS': 'Có nhiều tín hiệu phù hợp cần đối chiếu.',
    'CONFLICTING_SIGNALS': 'Có thông tin khác nhau hoặc mâu thuẫn.',
    'SPARSE_SIGNALS': 'Dữ liệu hữu ích còn ít.',
    'NORMALIZATION_VARIANT': 'Có biến thể tương đương sau chuẩn hóa.',
    'UNVERIFIED_IDENTIFIER': 'Giấy tờ chưa xác minh cần được đối chiếu thêm.',
    'NO_VERIFIED_ANCHOR': 'Chưa có điểm neo giấy tờ đã xác minh trong dữ liệu gửi đánh giá.',
}
LIMITATIONS = {
    'SOURCE_NOT_VERIFIED': 'Đề xuất không xác minh tính đúng đắn của dữ liệu nguồn.',
    'NO_CONTACT_VALUES': 'Chỉ nhận tín hiệu so sánh, không nhận đầy đủ liên hệ hoặc số giấy tờ.',
    'HUMAN_REVIEW_REQUIRED': 'Người duyệt quyết định; các ràng buộc backend vẫn được giữ.',
}


def grounded_output(output):
    return {'recommendation': output['recommendation'],
            'evidence_fields': [item['field'] for item in output['evidence']], 'evidence': output['evidence'],
            'reason_codes': output['reason_codes'], 'reason_vi': ' '.join(REASONS[code] for code in output['reason_codes']),
            'limitations': [LIMITATIONS[code] for code in output['limitation_codes']]}


SCHEMA = {'type': 'object', 'properties': {
    'pair_token': {'type': 'string'},
    'recommendation': {'type': 'string', 'enum': ['LIKELY_SAME_PERSON', 'LIKELY_DIFFERENT_PERSON', 'INSUFFICIENT_BASIS']},
    'evidence': {'type': 'array', 'items': {'type': 'object', 'properties': {
        'field': {'type': 'string', 'enum': FIELDS}, 'state': {'type': 'string', 'enum': STATES}},
        'required': ['field', 'state'], 'additionalProperties': False}},
    'reason_codes': {'type': 'array', 'items': {'type': 'string', 'enum': list(REASONS)}},
    'limitation_codes': {'type': 'array', 'items': {'type': 'string', 'enum': list(LIMITATIONS)}}},
    'required': ['pair_token', 'recommendation', 'evidence', 'reason_codes', 'limitation_codes'],
    'additionalProperties': False}
