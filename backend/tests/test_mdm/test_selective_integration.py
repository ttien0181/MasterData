"""Safety checks for imported normalization, blocking and optional semantics."""

from masterone.config import Settings
from masterone.mdm.pipeline.blocking import generate_candidates
from masterone.mdm.pipeline.matching import score_pair
from masterone.mdm.pipeline.semantic_address import enrich_pairs_with_semantic
from masterone.mdm.pipeline.standardization import standardize

from backend.tests.test_mdm.test_resolution_items import seed_run


def record(record_id: str, source: str, name: str, phone: str, email: str, address: str):
    raw = {"name": name, "phone": phone, "email": email, "address": address}
    normalized, flags = standardize(raw)
    return {"id": record_id, "source_system": source, "raw_fields": raw,
            "normalized_fields": normalized, "quality_flags": flags}


def test_same_source_duplicates_and_legacy_phone_are_candidates():
    left = record("a", "CRM", "Nguyễn Minh An", "01621234567", "", "Hà Nội")
    right = record("b", "CRM", "Nguyen Minh An", "0321234567", "", "Hà Nội")
    assert left["normalized_fields"]["phone"] == right["normalized_fields"]["phone"]
    assert ("a", "b") in generate_candidates([left, right])
    assert score_pair(left, right, {"exact_phone"})["decision"] == "MATCH_CANDIDATE"


def test_shared_and_placeholder_contacts_cannot_be_identity_anchors():
    left = record("a", "CRM", "Nguyễn Văn An", "0000000000", "info@example.com", "Hà Nội")
    right = record("b", "ERP", "Nguyễn Văn Bình", "0000000000", "info@example.com", "Hà Nội")
    reasons = generate_candidates([left, right], max_bucket_size=1).get(("a", "b"), set())
    assert "exact_phone" not in reasons and "exact_email" not in reasons
    result = score_pair(left, right, set())
    states = {item["field"]: item["state"] for item in result["field_evidence"]}
    assert states["phone"] == states["email"] == "SH"
    assert result["decision"] == "INSUFFICIENT_EVIDENCE"


def test_two_distinct_accented_names_do_not_merge_automatically():
    left = record("a", "CRM", "Nguyễn Minh An", "0901234567", "", "Hà Nội")
    right = record("b", "CRM", "Nguyễn Minh Ân", "0901234567", "", "Hà Nội")
    result = score_pair(left, right, {"exact_phone"})
    name = next(entry for entry in result["field_evidence"] if entry["field"] == "name")
    assert name["state"] == "MS"
    assert "NAME_DIACRITIC_CONFLICT" in name["normalization_tags"]
    assert result["decision"] == "REVIEW"


def test_llm_only_checks_ambiguous_address_and_never_promotes_to_auto():
    left = record("a", "CRM", "Nguyễn Minh An", "0901234567", "", "12 Le Loi Ha Noi")
    right = record("b", "ERP", "Nguyễn Minh Anh", "0901234567", "", "12 Le Loi Quan Ba Dinh")
    pair = {"id": "pair-a-b", "left_id": "a", "right_id": "b", "decision": "REVIEW",
            "score": 4.0, "match_weight": 4.0,
            "field_evidence": [{"field": "name", "state": "MS"},
                               {"field": "phone", "state": "EX"},
                               {"field": "email", "state": "MI"},
                               {"field": "address", "state": "MS", "provenance": "RULE"}]}
    sent = []

    def answer(batch, _settings):
        sent.extend(batch)
        return {"pair-a-b": "EQUIVALENT"}

    settings = Settings(semantic_address_enabled=True, gemini_api_key="test-only")
    stats = enrich_pairs_with_semantic([pair], {"a": left, "b": right}, settings, answer)
    assert stats["checked"] == 1
    assert pair["decision"] == "REVIEW"
    assert pair["field_evidence"][-1]["state"] == "HS"
    assert pair["field_evidence"][-1]["provenance"] == "LLM"
    assert set(sent[0]) == {"pair_id", "left_address", "right_address"}


def test_invalid_llm_response_keeps_rule_evidence():
    left = record("a", "CRM", "Nguyễn Minh An", "0901234567", "", "12 Le Loi Ha Noi")
    right = record("b", "ERP", "Nguyễn Minh Anh", "0901234567", "", "12 Le Loi Quan Ba Dinh")
    pair = {"id": "pair-a-b", "left_id": "a", "right_id": "b", "decision": "REVIEW",
            "score": 4.0, "match_weight": 4.0,
            "field_evidence": [{"field": "name", "state": "MS"},
                               {"field": "phone", "state": "EX"},
                               {"field": "email", "state": "MI"},
                               {"field": "address", "state": "MS", "provenance": "RULE"}]}
    settings = Settings(semantic_address_enabled=True, gemini_api_key="test-only")
    stats = enrich_pairs_with_semantic([pair], {"a": left, "b": right}, settings,
                                       lambda batch, config: {"wrong-id": "EQUIVALENT"})
    assert stats["errors"] == 1
    assert pair["field_evidence"][-1]["state"] == "MS"
    assert pair["field_evidence"][-1]["provenance"] == "RULE"


def test_item_explanation_uses_only_aggregate_states_and_is_cached(client, monkeypatch):
    run_id = seed_run(client)
    calls = []

    def explain(payload, _settings):
        calls.append(payload)
        return {"explanation": {"summary_vi": "Cần xem kỹ liên hệ.",
                                "attention_fields": ["phone"], "limitations": []},
                "model_version": "mock", "usage": {}}

    monkeypatch.setattr("masterone.api.mdm_resolution.explain_cohort", explain)
    url = f"/api/v1/mdm/runs/{run_id}/items/review/case-bc/explanation"
    first = client.post(url)
    second = client.post(url)
    assert first.status_code == 200, first.text
    assert second.status_code == 200
    assert first.json()["explanation"] == second.json()["explanation"]
    assert len(calls) == 1
    assert set(calls[0]) == {"record_count", "pair_count", "field_state_counts",
                             "reason_counts", "score_is_probability"}
