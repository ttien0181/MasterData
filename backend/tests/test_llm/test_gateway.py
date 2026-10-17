"""Gateway behaviour against a real durable ledger and a scripted transport.

The transport is replaced at the opener boundary, so request bodies, status codes and timeouts are exact.
"""

import json
from datetime import UTC, datetime
from urllib.error import HTTPError

import pytest
from masterone.llm.gateway import generate
from masterone.llm.policy import PolicyDenied
from masterone.mdm.models import LLMPolicyControl, LLMRequest, LLMReservation, ResolutionTask

from tests.test_llm.conftest import run_context

GEMINI_URL = "https://generativelanguage.googleapis.com"
ADDRESS_PAYLOAD = [{"pair_id": "p1", "left_address": "12 Lê Lợi Hà Nội", "right_address": "12 Le Loi Ha Noi"}]
ADDRESS_OUTPUT = {"items": [{"pair_id": "p1", "result": "EQUIVALENT"}]}
EXPLANATION_PAYLOAD = {"pair_count": 2, "record_count": 3, "reason_counts": {"EXACT_PHONE": 2},
                       "field_signal_counts": {"name": {"exact": 1}}, "score_is_probability": False}
EXPLANATION_OUTPUT = {"summary_vi": "Tên khác nhau ở một ký tự.", "attention_fields": ["name"],
                      "limitations": ["Chỉ dựa trên số đếm."]}


class _Response:
    def __init__(self, body):
        self._body = body

    def read(self, size=-1):
        return self._body if size is None or size < 0 else self._body[:size]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Opener:
    def __init__(self, transport):
        self.transport = transport

    def open(self, request, timeout=None):
        return self.transport.open(request, timeout)


class ScriptedTransport:
    """Each call consumes one scripted answer: bytes for a 200, or an exception to raise."""

    def __init__(self, monkeypatch, answers):
        self.answers = list(answers)
        self.requests = []
        monkeypatch.setattr("masterone.llm.gateway.build_opener", lambda handler: _Opener(self))

    def open(self, request, timeout):
        self.requests.append(request)
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return _Response(answer)


def gemini_ok(output):
    return json.dumps({
        "candidates": [{"content": {"parts": [{"text": json.dumps(output)}]}, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 12, "candidatesTokenCount": 4},
        "modelVersion": "gemini-test",
    }).encode()


def openrouter_ok(output):
    return json.dumps({
        "choices": [{"message": {"content": json.dumps(output)}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 4},
        "model": "google/gemini-test",
    }).encode()


def test_gemini_parse_skips_thought_and_joins_output_parts():
    from masterone.llm.gateway import _parse
    raw = json.dumps(EXPLANATION_OUTPUT, ensure_ascii=False)
    body = {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [
        {'thought': True, 'text': 'internal reasoning is not the answer'},
        {'text': raw[:30]}, {'text': raw[30:]},
    ]}}], 'usageMetadata': {'promptTokenCount': 4, 'candidatesTokenCount': 8, 'thoughtsTokenCount': 3}}
    output, usage, _ = _parse('gemini', body, 'test-model')
    assert output == EXPLANATION_OUTPUT
    assert usage == {'input': 4, 'output': 11}


@pytest.mark.parametrize('parts', [[None], [42], {'text': '{}'}, None, []])
def test_gemini_parse_rejects_malformed_or_missing_parts(parts):
    from masterone.llm.gateway import _InvalidError, _parse
    body = {'candidates': [{'content': {'parts': parts}, 'finishReason': 'STOP'}]}
    with pytest.raises(_InvalidError):
        _parse('gemini', body, 'test-model')


def test_predispatch_denial_can_resume_without_replaying_a_dispatched_request(llm_factory, llm_settings, monkeypatch):
    from masterone.llm.policy import PolicyEnforcer

    transport = ScriptedTransport(monkeypatch, [gemini_ok(EXPLANATION_OUTPUT)])
    context = run_context(llm_factory)
    original = PolicyEnforcer.attempt

    def blocked(*args, **kwargs):
        raise PolicyDenied('CIRCUIT_OPEN')

    monkeypatch.setattr(PolicyEnforcer, 'attempt', blocked)
    with pytest.raises(PolicyDenied, match='CIRCUIT_OPEN'):
        generate('EXPLANATION', EXPLANATION_PAYLOAD, llm_settings, context=context, factory=llm_factory)
    assert not transport.requests
    with llm_factory() as db:
        row = db.query(LLMReservation).one()
        assert row.state == 'RESERVED' and row.calls == 0
        assert db.query(LLMRequest).count() == 0

    monkeypatch.setattr(PolicyEnforcer, 'attempt', original)
    result = generate('EXPLANATION', EXPLANATION_PAYLOAD, llm_settings, context=context, factory=llm_factory)
    assert result['output'] == EXPLANATION_OUTPUT
    assert len(transport.requests) == 1
    with llm_factory() as db:
        row = db.query(LLMReservation).one()
        row.state = 'DISPATCHING'  # Simulate an interrupted, already dispatched request.
        db.commit()
    with pytest.raises(PolicyDenied, match='REQUEST_DISPATCHING'):
        generate('EXPLANATION', EXPLANATION_PAYLOAD, llm_settings, context=context, factory=llm_factory)
    assert len(transport.requests) == 1


def test_two_reservers_of_an_undispatched_request_cannot_dispatch_twice(llm_factory, llm_settings):
    from masterone.llm.policy import PolicyEnforcer

    context = run_context(llm_factory)
    gate = PolicyEnforcer(llm_settings, llm_factory)
    args = ('EXPLANATION', EXPLANATION_PAYLOAD, context, 'test-prompt', 'test-schema')
    first, _ = gate.reserve(*args)
    second, _ = gate.reserve(*args)
    assert first == second
    dispatch = (first, 'EXPLANATION', 'gemini', EXPLANATION_PAYLOAD, context, 'test-prompt', 'test-schema')
    gate.attempt(*dispatch)
    with pytest.raises(PolicyDenied, match='REQUEST_DISPATCHING'):
        gate.attempt(*dispatch)
    with llm_factory() as db:
        assert db.query(LLMRequest).count() == 1
        assert db.query(LLMReservation).one().calls == 1


def quota():
    return HTTPError(GEMINI_URL, 429, "RESOURCE_EXHAUSTED", {}, None)


def test_per_purpose_run_caps_do_not_consume_other_purposes_allowance(llm_factory, llm_settings, monkeypatch):
    settings = llm_settings.model_copy(update={
        'llm_max_explanation_calls_per_run': 1, 'llm_max_semantic_calls_per_run': 1})
    transport = ScriptedTransport(monkeypatch, [gemini_ok(EXPLANATION_OUTPUT), gemini_ok(ADDRESS_OUTPUT)])
    context = run_context(llm_factory)
    generate('EXPLANATION', EXPLANATION_PAYLOAD, settings, context=context, factory=llm_factory)
    generate('ADDRESS_COMPARE', ADDRESS_PAYLOAD, settings, context=context, factory=llm_factory)
    another = [{**ADDRESS_PAYLOAD[0], 'pair_id': 'p2'}]
    with pytest.raises(PolicyDenied, match='CALL_CAP'):
        generate('ADDRESS_COMPARE', another, settings, context=context, factory=llm_factory)
    assert len(transport.requests) == 2
    with llm_factory() as db:
        assert db.query(LLMRequest).count() == 2


def test_gemini_request_uses_documented_json_output_fields(llm_factory, llm_settings, monkeypatch):
    transport = ScriptedTransport(monkeypatch, [gemini_ok(ADDRESS_OUTPUT)])
    result = generate("ADDRESS_COMPARE", ADDRESS_PAYLOAD, llm_settings, context=run_context(llm_factory),
                      factory=llm_factory)
    request = transport.requests[0]
    config = json.loads(request.data)["generationConfig"]
    assert request.full_url.endswith("/models/gemini-test:generateContent")
    assert request.get_header("X-goog-api-key") == "gemini-test-key"
    assert config["responseMimeType"] == "application/json"
    assert config["responseSchema"]["type"] == "OBJECT"
    assert config["responseSchema"]["properties"]["items"]["items"]["properties"]["result"]["enum"] == [
        "EQUIVALENT", "DIFFERENT", "UNCERTAIN"]
    assert config["temperature"] == 0 and config["maxOutputTokens"] == 512
    assert config["thinkingConfig"] == {"thinkingBudget": 0}
    assert "responseFormat" not in config
    assert "additionalProperties" not in json.dumps(config["responseSchema"])
    assert result["provider"] == "gemini"
    assert result["output"] == ADDRESS_OUTPUT


def test_success_is_recorded_and_identical_request_is_served_from_cache(llm_factory, llm_settings, monkeypatch):
    transport = ScriptedTransport(monkeypatch, [gemini_ok(ADDRESS_OUTPUT)])
    context = run_context(llm_factory)
    first = generate("ADDRESS_COMPARE", ADDRESS_PAYLOAD, llm_settings, context=context, factory=llm_factory)
    second = generate("ADDRESS_COMPARE", ADDRESS_PAYLOAD, llm_settings, context=context, factory=llm_factory)
    assert first["cached"] is False and second["cached"] is True
    assert second["output"] == first["output"]
    assert len(transport.requests) == 1
    with llm_factory() as db:
        attempts = db.query(LLMRequest).all()
        assert [(row.provider, row.status) for row in attempts] == [("gemini", "SUCCESS")]
        reservations = db.query(LLMReservation).all()
        assert [row.state for row in reservations] == ["SUCCESS"]
        assert reservations[0].calls == 1


def test_gemini_quota_falls_back_to_openrouter_with_strict_schema(llm_factory, llm_settings, monkeypatch):
    transport = ScriptedTransport(monkeypatch, [quota(), openrouter_ok(ADDRESS_OUTPUT)])
    result = generate("ADDRESS_COMPARE", ADDRESS_PAYLOAD, llm_settings, context=run_context(llm_factory),
                      factory=llm_factory)
    assert result["provider"] == "openrouter"
    backup = transport.requests[1]
    assert backup.full_url == "https://openrouter.test/api/v1/chat/completions"
    assert backup.get_header("Authorization") == "Bearer openrouter-test-key"
    body = json.loads(backup.data)
    assert body["model"] == "google/gemini-test"
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["temperature"] == 0
    with llm_factory() as db:
        statuses = [(row.provider, row.status) for row in db.query(LLMRequest).order_by(LLMRequest.created_at)]
        assert statuses == [("gemini", "PROVIDER_QUOTA"), ("openrouter", "SUCCESS")]


def test_quota_cools_gemini_down_for_later_requests(llm_factory, llm_settings, monkeypatch):
    transport = ScriptedTransport(monkeypatch, [quota(), openrouter_ok(ADDRESS_OUTPUT),
                                                openrouter_ok({"items": [{"pair_id": "p2", "result": "DIFFERENT"}]})])
    context = run_context(llm_factory)
    generate("ADDRESS_COMPARE", ADDRESS_PAYLOAD, llm_settings, context=context, factory=llm_factory)
    second_payload = [{"pair_id": "p2", "left_address": "5 Trần Hưng Đạo", "right_address": "9 Nguyễn Huệ"}]
    result = generate("ADDRESS_COMPARE", second_payload, llm_settings, context=context, factory=llm_factory)
    assert result["provider"] == "openrouter"
    assert not any(GEMINI_URL in request.full_url for request in transport.requests[2:])
    with llm_factory() as db:
        control = db.get(LLMPolicyControl, "global")
        blocked = control.gemini_blocked_until
        blocked = blocked.replace(tzinfo=UTC) if blocked.tzinfo is None else blocked
        assert blocked > datetime.now(UTC)


def test_quota_without_an_approved_backup_fails_closed(llm_factory, llm_settings, monkeypatch):
    settings = llm_settings.model_copy(update={"llm_approved_providers": "gemini"})
    transport = ScriptedTransport(monkeypatch, [quota()])
    with pytest.raises(PolicyDenied) as info:
        generate("ADDRESS_COMPARE", ADDRESS_PAYLOAD, settings, context=run_context(llm_factory),
                 factory=llm_factory)
    assert info.value.code == "PROVIDER_DENIED"
    assert len(transport.requests) == 1


def test_timeout_never_falls_back_because_the_provider_may_have_processed_it(llm_factory, llm_settings, monkeypatch):
    transport = ScriptedTransport(monkeypatch, [TimeoutError("slow")])
    with pytest.raises(PolicyDenied) as info:
        generate("ADDRESS_COMPARE", ADDRESS_PAYLOAD, llm_settings, context=run_context(llm_factory),
                 factory=llm_factory)
    assert info.value.code == "PROVIDER_TIMEOUT"
    assert len(transport.requests) == 1


def test_output_that_recommends_merge_is_rejected_without_fallback(llm_factory, llm_settings, monkeypatch):
    unsafe = {"summary_vi": "Nên gộp hai bản ghi này vì tên trùng khớp.", "attention_fields": ["name"],
              "limitations": []}
    transport = ScriptedTransport(monkeypatch, [gemini_ok(unsafe)])
    with pytest.raises(PolicyDenied) as info:
        generate("EXPLANATION", EXPLANATION_PAYLOAD, llm_settings, context=run_context(llm_factory),
                 factory=llm_factory)
    assert info.value.code == "SCHEMA_INVALID"
    assert len(transport.requests) == 1


def test_server_errors_retry_inside_the_provider_then_stop(llm_factory, llm_settings, monkeypatch):
    monkeypatch.setattr("masterone.llm.gateway.time.sleep", lambda _seconds: None)
    settings = llm_settings.model_copy(update={"gemini_max_retries": 1})
    busy = HTTPError(GEMINI_URL, 503, "busy", {}, None)
    transport = ScriptedTransport(monkeypatch, [busy, busy])
    with pytest.raises(PolicyDenied) as info:
        generate("ADDRESS_COMPARE", ADDRESS_PAYLOAD, settings, context=run_context(llm_factory),
                 factory=llm_factory)
    assert info.value.code == "PROVIDER_HTTP_ERROR"
    assert len(transport.requests) == 2


def test_disabled_switch_denies_before_any_network_call(llm_factory, llm_settings, monkeypatch):
    transport = ScriptedTransport(monkeypatch, [])
    context = run_context(llm_factory)
    with llm_factory.begin() as db:
        db.get(LLMPolicyControl, "global").enabled = False
    with pytest.raises(PolicyDenied) as info:
        generate("ADDRESS_COMPARE", ADDRESS_PAYLOAD, llm_settings, context=context, factory=llm_factory)
    assert info.value.code == "LLM_GLOBAL_DISABLED"
    assert transport.requests == []


def test_llm_master_flag_off_denies_with_its_own_code(llm_factory, llm_settings, monkeypatch):
    ScriptedTransport(monkeypatch, [])
    settings = llm_settings.model_copy(update={"llm_enabled": False})
    with pytest.raises(PolicyDenied) as info:
        generate("ADDRESS_COMPARE", ADDRESS_PAYLOAD, settings, context=run_context(llm_factory),
                 factory=llm_factory)
    assert info.value.code == "LLM_DISABLED"


def test_cancelled_run_cannot_start_a_new_request(llm_factory, llm_settings, monkeypatch):
    transport = ScriptedTransport(monkeypatch, [])
    context = run_context(llm_factory)
    with llm_factory.begin() as db:
        db.query(ResolutionTask).filter_by(run_id="run-1").one().cancel_requested = True
    with pytest.raises(PolicyDenied) as info:
        generate("ADDRESS_COMPARE", ADDRESS_PAYLOAD, llm_settings, context=context, factory=llm_factory)
    assert info.value.code == "RUN_INACTIVE"
    assert transport.requests == []


def test_change_in_switch_revision_makes_a_pending_context_stale(llm_factory, llm_settings, monkeypatch):
    transport = ScriptedTransport(monkeypatch, [])
    context = run_context(llm_factory)
    with llm_factory.begin() as db:
        db.get(LLMPolicyControl, "global").revision = 2
    with pytest.raises(PolicyDenied) as info:
        generate("ADDRESS_COMPARE", ADDRESS_PAYLOAD, llm_settings, context=context, factory=llm_factory)
    assert info.value.code == "STALE"
    assert transport.requests == []


def test_retention_gap_on_both_providers_is_reported_as_retention(llm_factory, llm_settings, monkeypatch):
    ScriptedTransport(monkeypatch, [])
    settings = llm_settings.model_copy(update={"gemini_retention_verified": False,
                                               "openrouter_retention_verified": False})
    with pytest.raises(PolicyDenied) as info:
        generate("ADDRESS_COMPARE", ADDRESS_PAYLOAD, settings, context=run_context(llm_factory),
                 factory=llm_factory)
    assert info.value.code == "RETENTION_UNVERIFIED"


def test_explanation_success_returns_validated_output(llm_factory, llm_settings, monkeypatch):
    ScriptedTransport(monkeypatch, [gemini_ok(EXPLANATION_OUTPUT)])
    result = generate("EXPLANATION", EXPLANATION_PAYLOAD, llm_settings, context=run_context(llm_factory),
                      factory=llm_factory)
    assert result["output"] == EXPLANATION_OUTPUT
    assert result["usage"] == {"input": 12, "output": 4}


def test_one_rate_limit_is_retried_and_does_not_cool_gemini_down(llm_factory, llm_settings, monkeypatch):
    from masterone.mdm.models import LLMPolicyControl
    monkeypatch.setattr("masterone.llm.gateway.time.sleep", lambda _seconds: None)
    settings = llm_settings.model_copy(update={"gemini_max_retries": 2})
    transport = ScriptedTransport(monkeypatch, [quota(), gemini_ok(ADDRESS_OUTPUT)])
    result = generate("ADDRESS_COMPARE", ADDRESS_PAYLOAD, settings, context=run_context(llm_factory),
                      factory=llm_factory)
    assert result["provider"] == "gemini"
    assert len(transport.requests) == 2
    with llm_factory() as db:
        assert db.get(LLMPolicyControl, "global").gemini_blocked_until is None
        statuses = [row.status for row in db.query(LLMRequest).order_by(LLMRequest.created_at)]
        assert statuses == ["HTTP_RETRYABLE", "SUCCESS"]


def test_rate_limit_that_persists_through_all_retries_becomes_quota_and_falls_back(llm_factory, llm_settings,
                                                                                   monkeypatch):
    from masterone.mdm.models import LLMPolicyControl
    monkeypatch.setattr("masterone.llm.gateway.time.sleep", lambda _seconds: None)
    settings = llm_settings.model_copy(update={"gemini_max_retries": 2})
    transport = ScriptedTransport(monkeypatch, [quota(), quota(), quota(),
                                                openrouter_ok(ADDRESS_OUTPUT)])
    result = generate("ADDRESS_COMPARE", ADDRESS_PAYLOAD, settings, context=run_context(llm_factory),
                      factory=llm_factory)
    assert result["provider"] == "openrouter"
    assert sum(1 for r in transport.requests if "generativelanguage" in r.full_url) == 3
    with llm_factory() as db:
        assert db.get(LLMPolicyControl, "global").gemini_blocked_until is not None


def test_daily_quota_is_not_retried_and_falls_back_at_once(llm_factory, llm_settings, monkeypatch):
    import io

    from masterone.mdm.models import LLMPolicyControl
    sleeps = []
    monkeypatch.setattr("masterone.llm.gateway.time.sleep", lambda seconds: sleeps.append(seconds))
    settings = llm_settings.model_copy(update={"gemini_max_retries": 3})
    daily = b'{"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "details": [{"violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}]}}'
    daily_error = HTTPError(GEMINI_URL, 429, "RESOURCE_EXHAUSTED", {}, io.BytesIO(daily))
    transport = ScriptedTransport(monkeypatch, [daily_error, openrouter_ok(ADDRESS_OUTPUT)])
    result = generate("ADDRESS_COMPARE", ADDRESS_PAYLOAD, settings, context=run_context(llm_factory),
                      factory=llm_factory)
    assert result["provider"] == "openrouter"
    assert sleeps == []
    assert sum(1 for r in transport.requests if "generativelanguage" in r.full_url) == 1
    with llm_factory() as db:
        assert db.get(LLMPolicyControl, "global").gemini_blocked_until is not None
