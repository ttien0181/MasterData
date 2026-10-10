"""Semantic pass selection and bounds. Provider calls are replaced by an injected requester."""

import pytest
from masterone.config import Settings
from masterone.llm.policy import PolicyDenied
from masterone.mdm.pipeline import semantic_address as sa
from masterone.mdm.pipeline.decision_store import DecisionStore

from tests.test_llm.conftest import run_context

META = {"provider": "gemini", "model_version": "gemini-test", "request_id": "req"}
FULL_CONTEXT = {"run_id": "run-1", "input_hash": "in", "review_hash": "rv",
                "reference_version": "v1", "reference_hash": "rh"}


def _evidence(address="WS", phone="EX"):
    return [{"field": "name", "state": "MS"}, {"field": "phone", "state": phone},
            {"field": "email", "state": "MI"}, {"field": "address", "state": address, "provenance": "RULE"}]


def _pair(pair_id, left, right, *, decision="REVIEW", evidence=None):
    return {"id": pair_id, "left_id": left, "right_id": right, "decision": decision, "score": 4.0,
            "match_weight": 4.0, "triage_status": "REVIEW", "field_evidence": evidence or _evidence()}


def _records(count):
    return {f"r{i}": {"id": f"r{i}", "normalized_fields": {"address": f"{i} Le Loi Ha Noi"}} for i in range(count)}


def _settings(**overrides):
    values = {"_env_file": None, "llm_enabled": True, "semantic_address_enabled": True,
              "semantic_address_max_pairs_per_run": 100}
    values.update(overrides)
    return Settings(**values)


def _recording_lookup(records):
    seen = []

    def lookup(ids):
        seen.append(list(ids))
        return {rid: records[rid] for rid in ids if rid in records}

    return lookup, seen


def _always_equivalent(rows, _settings, _context):
    return {row["pair_id"]: "EQUIVALENT" for row in rows}, META


def test_lookup_is_limited_to_pairs_that_pass_the_decision_checks():
    records = {**_records(4)}
    pairs = [
        _pair("p-not-review", "r0", "r1", decision="MERGEABLE"),
        _pair("p-no-contact", "r0", "r2", evidence=_evidence(phone="MI")),
        _pair("p-qualifies", "r2", "r3"),
    ]
    lookup, seen = _recording_lookup(records)
    result = sa.annotate(pairs, lookup, run_context=FULL_CONTEXT, settings=_settings(),
                         requester=_always_equivalent)
    assert seen == [["r2", "r3"]]  # only the qualifying pair's records were fetched
    assert result.stats["selected"] <= 1


def test_identical_address_pairs_are_sent_once_and_annotate_every_member(monkeypatch):
    monkeypatch.setattr(sa, "_address_pair", lambda left, right: ("12 Le Loi", "12 Le Loi Ha Noi"))
    records = _records(4)
    pairs = [_pair("p1", "r0", "r1"), _pair("p2", "r2", "r3")]
    sent = []

    def requester(rows, _settings, _context):
        sent.append(rows)
        return {row["pair_id"]: "UNCERTAIN" for row in rows}, META

    lookup, _ = _recording_lookup(records)
    result = sa.annotate(pairs, lookup, run_context=FULL_CONTEXT, settings=_settings(), requester=requester)
    assert len(sent) == 1 and len(sent[0]) == 1
    assert result.stats["selected"] == 2 and result.stats["unique_keys"] == 1
    assert set(result.annotations) == {"p1", "p2"}
    assert all(item["result"] == "UNCERTAIN" for item in result.annotations.values())


def test_selection_is_bounded_by_the_per_run_limit(monkeypatch):
    monkeypatch.setattr(sa, "_address_pair", lambda left, right: (left["id"], right["id"]))
    records = _records(8)
    pairs = [_pair(f"p{i}", f"r{2 * i}", f"r{2 * i + 1}") for i in range(4)]
    lookup, _ = _recording_lookup(records)
    result = sa.annotate(pairs, lookup, run_context=FULL_CONTEXT,
                         settings=_settings(semantic_address_max_pairs_per_run=2), requester=_always_equivalent)
    assert result.stats["selected"] == 2
    assert len(result.annotations) == 2


def test_decision_store_selection_matches_the_list_path(tmp_path, monkeypatch):
    monkeypatch.setattr(sa, "_address_pair", lambda left, right: ("a", "b"))
    records = _records(6)
    pairs = [_pair("p1", "r0", "r1"), _pair("p2", "r2", "r3", decision="MERGEABLE"),
             _pair("p3", "r4", "r5")]
    store = DecisionStore(tmp_path)
    for pair in pairs:
        store.add(pair)
    store.seal()
    lookup_list, _ = _recording_lookup(records)
    lookup_store, _ = _recording_lookup(records)
    from_list = sa.candidate_pairs(pairs, lookup_list, max_pairs=100)
    from_store = sa.candidate_pairs(store, lookup_store, max_pairs=100)
    assert [p["id"] for p, _ in from_list] == [p["id"] for p, _ in from_store] == ["p1", "p3"]
    store.close()


def test_stops_at_the_first_budget_denial_and_sends_no_more_batches(monkeypatch):
    monkeypatch.setattr(sa, "_address_pair", lambda left, right: (left["id"], right["id"]))
    records = _records(24)
    pairs = [_pair(f"p{i}", f"r{2 * i}", f"r{2 * i + 1}") for i in range(12)]
    calls = []

    def denied(rows, _settings, _context):
        calls.append(len(rows))
        raise PolicyDenied("CALL_CAP")

    lookup, _ = _recording_lookup(records)
    result = sa.annotate(pairs, lookup, run_context=FULL_CONTEXT, settings=_settings(), requester=denied)
    assert calls == [10]
    assert result.stats["stopped"] == "CALL_CAP"
    assert result.annotations == {}


def test_repeated_provider_errors_stop_at_the_failure_limit(monkeypatch):
    monkeypatch.setattr(sa, "_address_pair", lambda left, right: (left["id"], right["id"]))
    records = _records(30)
    pairs = [_pair(f"p{i}", f"r{2 * i}", f"r{2 * i + 1}") for i in range(15)]
    calls = []

    def flaky(rows, _settings, _context):
        calls.append(1)
        raise TimeoutError("slow")

    lookup, _ = _recording_lookup(records)
    result = sa.annotate(pairs, lookup, run_context=FULL_CONTEXT,
                         settings=_settings(llm_consecutive_failure_limit=1), requester=flaky)
    assert len(calls) == 1
    assert result.stats["stopped"] == "FAILURE_LIMIT"
    assert result.stats["errors"] == 10


def test_missing_run_context_is_refused_when_llm_is_on():
    with pytest.raises(PolicyDenied) as info:
        sa.annotate([_pair("p1", "r0", "r1")], lambda ids: {}, run_context=None, settings=_settings(),
                    requester=_always_equivalent)
    assert info.value.code == "CONTEXT_REQUIRED"


def test_annotation_carries_provenance_and_the_context_it_was_produced_under(llm_factory, monkeypatch):
    monkeypatch.setattr(sa, "_address_pair", lambda left, right: ("12 Le Loi", "12 Le Loi Ha Noi"))
    records = _records(2)
    pair = _pair("p1", "r0", "r1")
    context = run_context(llm_factory)
    lookup, _ = _recording_lookup(records)
    result = sa.annotate([pair], lookup, run_context=context, settings=_settings(),
                         requester=_always_equivalent)
    annotation = result.annotations["p1"]
    assert annotation["provider"] == "gemini" and annotation["model_version"] == "gemini-test"
    assert annotation["gateway_request_id"] == "req"
    assert annotation["context"]["input_hash"] == context["input_hash"]
    assert annotation["eligible_for_auto_merge"] is False
    assert annotation["baseline_hash"] == sa.baseline_hash(pair)


def test_off_switch_returns_before_any_lookup(monkeypatch):
    def unreachable(ids):
        raise AssertionError("no lookup when off")

    result = sa.annotate([_pair("p1", "r0", "r1")], unreachable, run_context={"run_id": "x"},
                         settings=_settings(semantic_address_enabled=False))
    assert result.annotations == {} and result.stats["stopped"] == "DISABLED"
