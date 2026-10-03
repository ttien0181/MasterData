"""JSON Schema cho Explanation structured output.

LLM chỉ được output summary + supporting_points + risk_points.
Không có recommendation MERGE/REJECT, không có generated numeric score.

Versioning: tăng SCHEMA_VERSION khi thay đổi schema → cache key invalid.
"""

SCHEMA_VERSION = "EXPLANATION_SCHEMA_V1"
POLICY_VERSION = "POLICY_V1"

EXPLANATION_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "string",
            "maxLength": 500,
        },
        "supporting_points": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 4,
        },
        "risk_points": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 4,
        },
    },
    "required": ["summary", "supporting_points", "risk_points"],
    "additionalProperties": False,
}

GROUP_EXPLANATION_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "pattern_summary": {
            "type": "string",
            "maxLength": 400,
        },
        "common_signals": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 4,
        },
        "review_guidance": {
            "type": "string",
            "maxLength": 300,
        },
    },
    "required": ["pattern_summary", "common_signals", "review_guidance"],
    "additionalProperties": False,
}
