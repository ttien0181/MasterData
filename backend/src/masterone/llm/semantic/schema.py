"""JSON Schema cho Semantic Fallback structured output.

Dùng strict=True trên OpenRouter → model phải conform chính xác.
Không có numeric confidence trong MVP.

Versioning: tăng SCHEMA_VERSION khi thay đổi schema → cache key invalid.
"""

SCHEMA_VERSION = "SEMANTIC_SCHEMA_V1"

# Single item schema — dùng cho non-batch (nếu cần)
SEMANTIC_ITEM_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "pair_id": {"type": "string"},
        "field": {
            "type": "string",
            "enum": ["address", "organization_name", "free_text"],
        },
        "result": {
            "type": "string",
            "enum": ["EQUIVALENT", "LIKELY_EQUIVALENT", "DIFFERENT", "UNCERTAIN"],
        },
        "reason_code": {
            "type": "string",
            "enum": [
                "ABBREVIATION_EQUIVALENT",
                "TOKEN_REORDER",
                "ADMINISTRATIVE_VARIANT",
                "SEMANTIC_NAME_EQUIVALENT",
                "MEANING_DIFFERENT",
                "INSUFFICIENT_CONTEXT",
            ],
        },
        "short_reason": {"type": "string", "maxLength": 240},
    },
    "required": ["pair_id", "field", "result", "reason_code", "short_reason"],
    "additionalProperties": False,
}

# Batch schema — wrap items array
SEMANTIC_BATCH_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": SEMANTIC_ITEM_SCHEMA,
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}
