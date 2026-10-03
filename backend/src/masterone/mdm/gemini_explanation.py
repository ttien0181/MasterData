"""Privacy-minimized, on-demand Gemini explanations for review cohorts.

Theo mục 25.1: LLM chỉ giải thích cho Steward, KHÔNG được dùng để bật Auto Merge.
  - allow_llm_evidence=False  (mặc định) → provenance "LLM" không eligible auto merge
  - Hàm explain_cohort() chỉ trả về explanation text, không trả về decision hay state CF/DF
"""

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import HTTPException

from masterone.config import Settings

ALLOWED_FIELDS = {"name", "phone", "email", "address"}

# Khi True: pair có LLM evidence vẫn eligible auto merge (phải benchmark trước khi bật)
# Mặc định False theo nguyên tắc 11 kiến trúc v3.1
ALLOW_LLM_EVIDENCE_DEFAULT: bool = False


def is_llm_eligible_for_auto_merge(policy_allow_llm: bool = ALLOW_LLM_EVIDENCE_DEFAULT) -> bool:
    """Trả về False nếu policy chưa cho phép dùng LLM làm evidence auto merge."""
    return policy_allow_llm


def explain_cohort(payload: dict, settings: Settings) -> dict:
    if not settings.gemini_api_key:
        raise HTTPException(503, "GEMINI_NOT_CONFIGURED")
    model = settings.gemini_model.strip()
    if not model or any(char in model for char in "?/&#"):
        raise HTTPException(503, "GEMINI_MODEL_INVALID")
    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    schema = {"type": "OBJECT", "properties": {
        "summary_vi": {"type": "STRING"},
        "attention_fields": {"type": "ARRAY", "items": {"type": "STRING"}},
        "limitations": {"type": "ARRAY", "items": {"type": "STRING"}},
    }, "required": ["summary_vi", "attention_fields", "limitations"]}
    prompt = ("Giải thích ngắn gọn bằng tiếng Việt các tín hiệu để Data Steward xem xét. "
              "Không đề xuất gộp/từ chối, không kết luận thực thể, không bịa dữ liệu. "
              "Score là điểm rule, không phải xác suất. Chỉ dựa trên các số liệu tổng hợp sau: "
              + json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    request = Request(endpoint, data=json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "responseSchema": schema},
    }).encode(), headers={"Content-Type": "application/json", "x-goog-api-key": settings.gemini_api_key}, method="POST")
    try:
        with urlopen(request, timeout=25) as response:
            body = json.loads(response.read())
    except HTTPError as exc:
        code = "GEMINI_RATE_LIMIT" if exc.code == 429 else "GEMINI_HTTP_ERROR"
        raise HTTPException(502, code) from None
    except (URLError, TimeoutError):
        raise HTTPException(502, "GEMINI_UNAVAILABLE") from None
    except (json.JSONDecodeError, OSError):
        raise HTTPException(502, "GEMINI_BAD_RESPONSE") from None
    try:
        text = body["candidates"][0]["content"]["parts"][0]["text"]
        explanation = json.loads(text)
        summary = explanation["summary_vi"]
        attention = explanation["attention_fields"]
        limitations = explanation["limitations"]
        if not isinstance(summary, str) or len(summary) > 1200 or not isinstance(attention, list) or not isinstance(limitations, list):
            raise ValueError
        if not set(attention) <= ALLOWED_FIELDS or any(not isinstance(item, str) for item in limitations):
            raise ValueError
        usage = body.get("usageMetadata", {})
        return {
            "explanation": {"summary_vi": summary, "attention_fields": attention,
                            "limitations": limitations},
            "model_version": body.get("modelVersion", settings.gemini_model),
            # Mục 25.1: LLM không bật auto merge theo mặc định
            "eligible_for_auto_merge": ALLOW_LLM_EVIDENCE_DEFAULT,
            "usage": {"prompt_tokens": usage.get("promptTokenCount", 0),
                      "candidate_tokens": usage.get("candidatesTokenCount", 0)},
        }
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
        raise HTTPException(502, "GEMINI_SCHEMA_ERROR") from None
