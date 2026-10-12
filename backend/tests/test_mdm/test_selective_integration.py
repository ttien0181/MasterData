"""Safety checks for imported normalization, blocking and optional semantics."""

from masterone.config import Settings
from masterone.mdm.pipeline.blocking import generate_candidates
from masterone.mdm.pipeline.matching import score_pair
from masterone.mdm.engine_adapter import EngineResultAdapter
from masterone.mdm.pipeline.semantic_address import annotate, baseline_hash, with_annotations
from masterone.mdm.pipeline.standardization import standardize

from tests.test_mdm.test_resolution_items import seed_run


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
    assert score_pair(left, right, {"exact_phone"})["decision"] == "REVIEW"


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


CONTEXT = {"run_id": "run-1", "input_hash": "in", "review_hash": "rv",
           "reference_version": "v1", "reference_hash": "rh"}


def _ambiguous_pair():
    return {"id": "pair-a-b", "left_id": "a", "right_id": "b", "decision": "REVIEW",
            "score": 4.0, "match_weight": 4.0,
            "field_evidence": [{"field": "name", "state": "MS", "similarity": 0.8},
                               {"field": "phone", "state": "EX", "similarity": 1.0},
                               {"field": "email", "state": "MI", "similarity": 0.0},
                               {"field": "address", "state": "MS", "provenance": "RULE", "similarity": 0.7}]}


def _records():
    left = record("a", "CRM", "Nguyễn Minh An", "0901234567", "", "12 Le Loi Ha Noi")
    right = record("b", "ERP", "Nguyễn Minh Anh", "0901234567", "", "12 Le Loi Quan Ba Dinh")
    return {"a": left, "b": right}


def _settings(**overrides):
    values = {"_env_file": None, "llm_enabled": True, "semantic_address_enabled": True}
    values.update(overrides)
    return Settings(**values)


def test_llm_only_checks_ambiguous_address_and_never_promotes_to_auto():
    pair = _ambiguous_pair()
    records = _records()
    sent = []

    def answer(rows, _settings, _context):
        sent.extend(rows)
        return {"pair-a-b": "EQUIVALENT"}, {"provider": "gemini", "model_version": "m", "request_id": "r"}

    result = annotate([pair], lambda ids: {rid: records[rid] for rid in ids if rid in records},
                      run_context=CONTEXT, settings=_settings(), requester=answer)
    assert result.stats["checked"] == 1
    assert pair == _ambiguous_pair()  # the decision payload is never mutated by the LLM pass
    annotated = next(with_annotations([pair], result.annotations))
    annotation = annotated["semantic_annotations"][0]
    assert annotation["result"] == "EQUIVALENT"
    assert annotation["eligible_for_auto_merge"] is False
    assert annotation["baseline_hash"] == baseline_hash(pair)
    assert EngineResultAdapter().pair(annotated)["decision"] == "REVIEW"
    assert set(sent[0]) == {"pair_id", "left_address", "right_address"}


def test_invalid_llm_response_keeps_rule_evidence():
    pair = _ambiguous_pair()
    records = _records()

    def wrong_id(rows, _settings, _context):
        return {"wrong-id": "EQUIVALENT"}, {"provider": "gemini", "model_version": "m", "request_id": "r"}

    result = annotate([pair], lambda ids: {rid: records[rid] for rid in ids if rid in records},
                      run_context=CONTEXT, settings=_settings(), requester=wrong_id)
    assert result.stats["errors"] == 1
    assert result.annotations == {}
    assert pair == _ambiguous_pair()


def test_disabled_llm_never_looks_up_records_or_calls_provider():
    pair = _ambiguous_pair()

    def unreachable_lookup(ids):
        raise AssertionError("no lookup when LLM is off")

    def unreachable_requester(rows, _settings, _context):
        raise AssertionError("no provider call when LLM is off")

    result = annotate([pair], unreachable_lookup, run_context=CONTEXT,
                      settings=_settings(llm_enabled=False), requester=unreachable_requester)
    assert result.annotations == {} and result.stats["stopped"] == "DISABLED"


def test_item_explanation_uses_only_aggregate_states_and_is_cached(client, monkeypatch):
    from masterone.config import get_settings
    run_id = seed_run(client)
    calls = []

    def explain(payload, _settings, *, context, factory=None):
        calls.append(payload)
        return {"status": "READY", "explanation": {"summary_vi": "Cần xem kỹ liên hệ.",
                                                   "attention_fields": ["phone"], "limitations": []},
                "model_version": "mock", "usage": {}}

    monkeypatch.setattr(get_settings(), "llm_enabled", True)
    monkeypatch.setattr(get_settings(), "llm_explanation_enabled", True)
    monkeypatch.setattr("masterone.api.mdm_resolution.explain_cohort", explain)
    url = f"/api/v1/mdm/runs/{run_id}/items/review/case-bc/explanation"
    first = client.post(url)
    second = client.post(url)
    assert first.status_code == 200, first.text
    assert second.status_code == 200
    assert first.json()["explanation"] == second.json()["explanation"]
    assert first.json()["status"] == "READY"
    assert len(calls) == 1
    assert set(calls[0]) == {"record_count", "pair_count", "field_state_counts",
                             "reason_counts", "score_is_probability"}
