"""Explanations of aggregate evidence for steward review.

The model sees counts and bands only. It never sees source values, never recommends merge or reject,
and its output is a suggestion the steward may ignore. Failures return a rule-based template, so the
review queue keeps working without the model.
"""

from masterone.llm.gateway import generate
from masterone.llm.policy import PolicyDenied
from masterone.logging_config import get_logger

logger = get_logger(__name__)


def template_explanation(code="LLM_DISABLED", attempts=0):
    return {
        "status": "TEMPLATE",
        "explanation": {
            "summary_vi": "Hãy đối chiếu các thông tin trùng, khác và còn thiếu trong bản ghi trước khi quyết định.",
            "attention_fields": [],
            "limitations": ["Giải thích theo quy tắc; chưa có kết luận từ AI."],
        },
        "model_version": "deterministic-template-v1",
        "usage": {"dispatch_attempts": attempts},
        "error_code": code,
    }


def explain_cohort(payload, settings, *, context, factory=None):
    """Never raises. Returns READY, TEMPLATE or STALE with the output's provenance."""
    try:
        result = generate("EXPLANATION", payload, settings, context=context, factory=factory)
    except PolicyDenied as exc:
        if exc.code in {"STALE", "RUN_INACTIVE"}:
            return {"status": "STALE", "explanation": None, "model_version": None, "usage": {}}
        if exc.code not in {"LLM_DISABLED", "PURPOSE_DISABLED"}:
            logger.warning("llm_explanation_fallback code=%s attempts=%d", exc.code, exc.attempts,
                           extra={"event": "llm_explanation_fallback", "run_id": context.get("run_id")})
        return template_explanation(exc.code, attempts=exc.attempts)
    return {
        "status": "READY",
        "explanation": result["output"],
        "model_version": result["model_version"],
        "provider": result["provider"],
        "usage": {"dispatch_attempts": result["attempts"], **{k: v for k, v in result["usage"].items()
                                                                if v is not None}},
        "request_id": result["request_id"],
    }
