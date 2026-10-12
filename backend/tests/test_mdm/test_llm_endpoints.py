"""Explanation endpoints: the UI always receives a usable answer with an honest status, never a 502 for a provider failure."""

from masterone.config import get_settings
from masterone.llm.policy import PolicyDenied

from tests.test_mdm.test_resolution_items import seed_run


def _url(run_id):
    return f"/api/v1/mdm/runs/{run_id}/items/review/case-bc/explanation"


def test_disabled_explanations_return_a_rule_template_without_any_provider_call(client, monkeypatch):
    run_id = seed_run(client)
    calls = []
    monkeypatch.setattr(get_settings(), "llm_enabled", False)
    monkeypatch.setattr("masterone.api.mdm_resolution.explain_cohort",
                        lambda *args, **kwargs: calls.append(args) or {"status": "READY"})
    response = client.post(_url(run_id))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "TEMPLATE"
    assert body["model_version"] == "deterministic-template-v1"
    assert calls == []


def test_provider_failure_becomes_a_template_not_an_error(client, monkeypatch):
    from masterone.llm.explanation import template_explanation

    run_id = seed_run(client)
    monkeypatch.setattr(get_settings(), "llm_enabled", True)
    monkeypatch.setattr(get_settings(), "llm_explanation_enabled", True)
    monkeypatch.setattr("masterone.api.mdm_resolution.explain_cohort",
                        lambda *args, **kwargs: template_explanation("PROVIDER_DENIED"))
    response = client.post(_url(run_id))
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "TEMPLATE"
    assert response.json()["explanation"]["summary_vi"]


def test_stale_answer_is_reported_and_not_stored_as_current(client, monkeypatch):
    run_id = seed_run(client)
    monkeypatch.setattr(get_settings(), "llm_enabled", True)
    monkeypatch.setattr(get_settings(), "llm_explanation_enabled", True)
    monkeypatch.setattr("masterone.api.mdm_resolution.explain_cohort",
                        lambda *args, **kwargs: {"status": "STALE", "explanation": None,
                                                 "model_version": None, "usage": {}})
    first = client.post(_url(run_id))
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "STALE"
    monkeypatch.setattr("masterone.api.mdm_resolution.explain_cohort",
                        lambda *args, **kwargs: {"status": "READY", "explanation": {
                            "summary_vi": "Mới.", "attention_fields": [], "limitations": []},
                            "model_version": "m", "usage": {}})
    second = client.post(_url(run_id))
    assert second.json()["status"] == "READY"
    assert second.json()["explanation"]["summary_vi"] == "Mới."


def test_policy_denial_inside_the_gateway_is_a_template_too(client, monkeypatch):
    run_id = seed_run(client)
    monkeypatch.setattr(get_settings(), "llm_enabled", True)
    monkeypatch.setattr(get_settings(), "llm_explanation_enabled", True)
    from masterone.llm import explanation as explanation_module

    def denied(*args, **kwargs):
        raise PolicyDenied("RETENTION_UNVERIFIED")

    monkeypatch.setattr(explanation_module, "generate", denied)
    monkeypatch.setattr(get_settings(), "gemini_api_key", "bench-only-key")
    response = client.post(_url(run_id))
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "TEMPLATE"
    assert response.json()["explanation"] is not None
