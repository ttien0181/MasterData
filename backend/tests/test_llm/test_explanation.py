"""Explanations never raise and never turn a failure into a READY answer."""

from urllib.error import HTTPError

from masterone.llm.explanation import explain_cohort

from tests.test_llm.conftest import run_context
from tests.test_llm.test_gateway import (
    EXPLANATION_OUTPUT,
    EXPLANATION_PAYLOAD,
    ScriptedTransport,
    gemini_ok,
)


def test_success_is_ready_with_provenance(llm_factory, llm_settings, monkeypatch):
    ScriptedTransport(monkeypatch, [gemini_ok(EXPLANATION_OUTPUT)])
    result = explain_cohort(EXPLANATION_PAYLOAD, llm_settings, context=run_context(llm_factory),
                            factory=llm_factory)
    assert result["status"] == "READY"
    assert result["explanation"] == EXPLANATION_OUTPUT
    assert result["provider"] == "gemini"


def test_quota_with_no_backup_becomes_a_rule_template_not_an_error(llm_factory, llm_settings, monkeypatch):
    settings = llm_settings.model_copy(update={"llm_approved_providers": "gemini"})
    ScriptedTransport(monkeypatch, [HTTPError("https://x", 429, "quota", {}, None)])
    result = explain_cohort(EXPLANATION_PAYLOAD, settings, context=run_context(llm_factory),
                            factory=llm_factory)
    assert result["status"] == "TEMPLATE"
    assert result["error_code"] == "PROVIDER_DENIED"
    assert result["explanation"]["attention_fields"] == []


def test_disabled_explanations_are_a_template_without_any_call(llm_factory, llm_settings, monkeypatch):
    transport = ScriptedTransport(monkeypatch, [])
    settings = llm_settings.model_copy(update={"llm_explanation_enabled": False})
    result = explain_cohort(EXPLANATION_PAYLOAD, settings, context=run_context(llm_factory),
                            factory=llm_factory)
    assert result["status"] == "TEMPLATE" and result["error_code"] == "PURPOSE_DISABLED"
    assert transport.requests == []


def test_stale_context_is_reported_as_stale_not_as_an_answer(llm_factory, llm_settings, monkeypatch):
    from masterone.mdm.models import LLMPolicyControl
    ScriptedTransport(monkeypatch, [])
    context = run_context(llm_factory)
    with llm_factory.begin() as db:
        db.get(LLMPolicyControl, "global").revision = 9
    result = explain_cohort(EXPLANATION_PAYLOAD, llm_settings, context=context, factory=llm_factory)
    assert result["status"] == "STALE"
    assert result["explanation"] is None
