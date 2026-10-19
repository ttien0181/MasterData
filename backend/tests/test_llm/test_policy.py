"""Static policy rules: approval, payload allowlist and output validation. No database, no network."""

import pytest
from masterone.llm.policy import PolicyDenied, allowed_payload, digest, enforce, validate_output


def test_disabled_llm_is_denied_for_address_compare(llm_settings):
    with pytest.raises(PolicyDenied) as info:
        enforce(llm_settings.model_copy(update={"llm_enabled": False}), "gemini", "ADDRESS_COMPARE")
    assert info.value.code == "LLM_DISABLED"


def test_explanation_only_needs_its_flag_gemini_model_and_key(llm_settings):
    settings = llm_settings.model_copy(update={
        "llm_enabled": False,
        "llm_approved_providers": "",
        "llm_approved_models": "",
        "gemini_retention_verified": False,
        "gemini_retention_contract": "",
        "gemini_token_price_microusd": 0,
        "openrouter_api_key": "",
    })
    enforce(settings, "gemini", "EXPLANATION")
    with pytest.raises(PolicyDenied) as info:
        enforce(settings, "openrouter", "EXPLANATION")
    assert info.value.code == "PROVIDER_DENIED"
    with pytest.raises(PolicyDenied) as info:
        enforce(settings.model_copy(update={"gemini_api_key": ""}), "gemini", "EXPLANATION")
    assert info.value.code == "PROVIDER_UNAVAILABLE"


@pytest.mark.parametrize("update,code", [
    ({"semantic_address_enabled": False}, "PURPOSE_DISABLED"),
    ({"llm_approved_providers": "openrouter"}, "PROVIDER_DENIED"),
    ({"llm_approved_models": "some-other-model"}, "MODEL_DENIED"),
    ({"gemini_retention_verified": False}, "RETENTION_UNVERIFIED"),
    ({"gemini_retention_contract": "   "}, "RETENTION_UNVERIFIED"),
    ({"gemini_token_price_microusd": 0}, "PRICE_UNVERIFIED"),
    ({"gemini_api_key": ""}, "PROVIDER_UNAVAILABLE"),
])
def test_each_approval_gap_has_its_own_fixed_code(llm_settings, update, code):
    with pytest.raises(PolicyDenied) as info:
        enforce(llm_settings.model_copy(update=update), "gemini", "ADDRESS_COMPARE")
    assert info.value.code == code


def test_openrouter_is_approved_independently_of_gemini(llm_settings):
    settings = llm_settings.model_copy(update={"gemini_retention_verified": False})
    enforce(settings, "openrouter", "ADDRESS_COMPARE")  # no exception: its own retention is verified


def test_unknown_purpose_is_denied(llm_settings):
    with pytest.raises(PolicyDenied) as info:
        enforce(llm_settings, "gemini", "FREE_FORM_CHAT")
    assert info.value.code == "PURPOSE_DENIED"


def test_address_payload_carries_only_pair_id_and_two_addresses():
    rows = [{"pair_id": "p1", "left_address": "12 Lê Lợi Hà Nội", "right_address": "12 Le Loi Ha Noi"}]
    assert allowed_payload("ADDRESS_COMPARE", rows) == rows
    with pytest.raises(PolicyDenied):
        allowed_payload("ADDRESS_COMPARE", [{**rows[0], "phone": "0901234567"}])
    with pytest.raises(PolicyDenied):
        allowed_payload("ADDRESS_COMPARE", [{**rows[0], "left_address": "Email me at a@b.com 12"}])


def test_explanation_payload_drops_free_text_and_forces_probability_false():
    payload = {"pair_count": 2, "reason_counts": {"EXACT_PHONE": 2, "not a code!": 1},
               "field_signal_counts": {"name": {"exact": 1}}, "score_is_probability": True,
               "group": "A"}
    result = allowed_payload("EXPLANATION", payload)
    assert result["reason_counts"] == {"EXACT_PHONE": 2}
    assert result["score_is_probability"] is False
    assert result["group"] == "A"
    with pytest.raises(PolicyDenied):
        allowed_payload("EXPLANATION", {**payload, "source_name": "Nguyen Van An"})


def test_address_output_must_cover_exactly_the_sent_pairs():
    payload = [{"pair_id": "p1", "left_address": "a 1", "right_address": "b 1"},
               {"pair_id": "p2", "left_address": "a 2", "right_address": "b 2"}]
    ok = {"items": [{"pair_id": "p1", "result": "EQUIVALENT"}, {"pair_id": "p2", "result": "UNCERTAIN"}]}
    assert validate_output("ADDRESS_COMPARE", ok, payload) == ok
    with pytest.raises(PolicyDenied):
        validate_output("ADDRESS_COMPARE", {"items": [{"pair_id": "p1", "result": "EQUIVALENT"}]}, payload)
    with pytest.raises(PolicyDenied):
        validate_output("ADDRESS_COMPARE", {"items": [{"pair_id": "p1", "result": "MERGE"},
                                                      {"pair_id": "p2", "result": "UNCERTAIN"}]}, payload)


@pytest.mark.parametrize("summary", [
    "Nên gộp hai bản ghi này vì tên trùng khớp.",
    "Khuyến nghị: từ chối cặp này.",
    "You should merge these records.",
])
def test_explanation_that_recommends_merge_or_reject_is_rejected(summary):
    output = {"summary_vi": summary, "attention_fields": ["name"], "limitations": []}
    with pytest.raises(PolicyDenied) as info:
        validate_output("EXPLANATION", output, {})
    assert info.value.code == "SCHEMA_INVALID"


def test_clean_explanation_passes_and_limits_are_enforced():
    clean = {"summary_vi": "Tên khác nhau ở một ký tự.", "attention_fields": ["name"],
             "limitations": ["Chỉ dựa trên số đếm."]}
    assert validate_output("EXPLANATION", clean, {}) == clean
    with pytest.raises(PolicyDenied):
        validate_output("EXPLANATION", {**clean, "attention_fields": ["salary"]}, {})
    with pytest.raises(PolicyDenied):
        validate_output("EXPLANATION", {**clean, "extra": "field"}, {})


def test_naming_a_contact_field_is_not_a_leak_but_a_contact_value_is():
    field_mention = {"summary_vi": "Số điện thoại và email đều trùng khớp.", "attention_fields": ["phone"],
                     "limitations": []}
    assert validate_output("EXPLANATION", field_mention, {}) == field_mention
    with pytest.raises(PolicyDenied):
        validate_output("EXPLANATION", {"summary_vi": "Mã số 0901234567 trùng với bản ghi khác.",
                                        "attention_fields": [], "limitations": []}, {})


def test_negated_advice_is_a_disclaimer_and_positive_advice_is_rejected():
    disclaimer = {"summary_vi": "Dữ liệu chỉ cho thấy tên khác nhau.", "attention_fields": ["name"],
                  "limitations": ["Không đề xuất gộp hoặc từ chối; quyết định thuộc về người xem xét."]}
    assert validate_output("EXPLANATION", disclaimer, {}) == disclaimer
    with pytest.raises(PolicyDenied):
        validate_output("EXPLANATION", {"summary_vi": "Nên gộp hai bản ghi này.", "attention_fields": [],
                                        "limitations": []}, {})


def test_digest_is_order_independent_and_rejects_non_finite_numbers():
    assert digest({"a": 1, "b": 2}) == digest({"b": 2, "a": 1})
    with pytest.raises(ValueError):
        digest({"score": float("nan")})
