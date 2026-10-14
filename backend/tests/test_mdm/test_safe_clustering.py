from copy import deepcopy
from dataclasses import replace
from itertools import permutations

import duckdb
import pytest
from masterone.config import get_settings
from masterone.mdm.models import ResolutionPair, ResolutionProposal, ResolutionRecord, ResolutionTask
from masterone.mdm.pipeline.cluster_policy import ClusterPolicy
from masterone.mdm.pipeline.clustering import build_proposals
from masterone.mdm.pipeline.decision_store import DecisionStore
from masterone.mdm.pipeline.golden import build_golden
from masterone.mdm.pipeline.golden_v2 import timestamp
from masterone.mdm.pipeline.matching import score_pair
from masterone.mdm.pipeline.stage_runner import StageRunner
from masterone.mdm.resolution_service import _compute, run_pipeline

from tests.test_mdm.test_decision_semantics import record
from tests.test_mdm.test_resolution_items import seed_run


@pytest.fixture
def safety(monkeypatch):
    monkeypatch.setattr(get_settings(), "decision_semantics_v2_enabled", True)
    monkeypatch.setattr(get_settings(), "cluster_safety_v2_enabled", True)


def edge(a, b, weight=12, pid=None):
    result = score_pair(a, b, set())
    return {**result, "id": pid or a["id"]+b["id"], "left_id": a["id"], "right_id": b["id"],
            "decision": "MERGEABLE", "match_weight": weight}


def chain():
    return [record("a", phone="0912345678", email=""),
            record("b", phone="0912345678", email="bridge@example.test"),
            record("c", phone="0987654321", email="bridge@example.test")]


def test_bridge_missing_direct_evidence(safety):
    a, b, c = chain()
    pairs = [edge(a, b, 15), edge(b, c, 14)]
    result = build_proposals([a, b, c], pairs, "r", "v")
    assert [p["member_record_ids"] for p in result["proposals"]] == [["a", "b"]]
    assert pairs[1]["triage_reason"] == "CLUSTER_BRIDGE_MISSING_EVIDENCE"


@pytest.mark.parametrize("kind,reason", [("cannot", "CLUSTER_CONFIRMED_CANNOT_LINK"), ("review", "CLUSTER_REVIEW_VETO")])
def test_negative_review_separate_from_cannot_link(safety, kind, reason):
    a, b, c = [record(r) for r in "abc"]
    pairs = [edge(a, b, 15), edge(b, c, 14)]
    links = frozenset({("a", "c")})
    result = build_proposals([a, b, c], pairs, "r", "v",
        negative_pairs=links if kind == "cannot" else None,
        execution_blocked_pairs=links if kind == "review" else frozenset())
    assert [p["member_record_ids"] for p in result["proposals"]] == [["a", "b"]]
    assert pairs[1]["triage_reason"] == reason
    assert pairs[1]["decision"] == "MERGEABLE"  # permission/routing changed, no NON_MATCH inference


def test_same_source_duplicates_are_allowed_without_domain_contract(safety):
    a, b = record("a"), record("b")
    a["source_system"] = b["source_system"] = "CRM"
    assert build_proposals([a, b], [edge(a, b)], "r", "v")["proposals"]
    unique = ClusterPolicy(unique_sources=(("CRM", ("PERSON",)),))
    assert not build_proposals([a, b], [edge(a, b)], "r", "v", cluster_policy=unique)["proposals"]
    other = replace(unique, unique_sources=(("CRM", ("ORGANIZATION",)),))
    assert build_proposals([a, b], [edge(a, b)], "r", "v", cluster_policy=other)["proposals"]


def test_order_invariant_record_ids_not_pair_hashes(safety):
    a, b, c = [record(r) for r in "abc"]
    pairs = [edge(a, b, 12, "z-hash"), edge(a, c, 12, "a-hash"), edge(b, c, None, "m-hash")]
    results = [build_proposals([c, a, b], deepcopy(list(order)), "r", "v")["proposals"] for order in permutations(pairs)]
    assert all(r == results[0] for r in results)
    assert [e["pair_id"] for e in results[0][0]["edge_order"]] == ["z-hash", "a-hash"]


@pytest.mark.parametrize("policy,reason", [
    (ClusterPolicy(max_cluster_size=2), "CLUSTER_SIZE_LIMIT"),
    (ClusterPolicy(max_pair_checks_per_union=1), "CLUSTER_GUARD_UNION_BUDGET"),
    (ClusterPolicy(max_pair_checks_per_run=2), "CLUSTER_GUARD_RUN_BUDGET"),
])
def test_size_and_guard_cost_bounds(safety, policy, reason):
    a, b, c = [record(r) for r in "abc"]
    pairs = [edge(a, b, 15), edge(b, c, 14)]
    result = build_proposals([a, b, c], pairs, "r", "v", cluster_policy=policy)
    assert pairs[1]["triage_reason"] == reason
    assert result["cluster_metrics"]["visited"] <= policy.max_pair_checks_per_run


def test_cached_consistency_does_not_consume_new_check_budget(safety):
    from masterone.mdm.pipeline.cluster_guards import check_component
    from masterone.mdm.pipeline.cluster_policy import GuardBudget
    from masterone.mdm.pipeline.matching import value_frequencies
    a, b = record('a'), record('b')
    policy, budget = ClusterPolicy(max_pair_checks_per_run=1), GuardBudget()
    store = DecisionStore()
    try:
        store.add(edge(a, b))
        store.seal()
        for _ in range(3):
            assert check_component({'a', 'b'}, {'a': a, 'b': b}, store, policy, budget,
                                   value_frequencies([a, b])) == ''
        assert budget.checked == 1 and budget.cache_hits == 2
    finally:
        store.close()


def test_cursor_requires_complete_barrier_and_rejects_duplicate(safety, tmp_path):
    a, b = record("a"), record("b")
    store = DecisionStore(tmp_path)
    try:
        store.add(edge(a, b))
        with pytest.raises(ValueError, match="barrier"):
            list(store)
        import sqlite3
        with pytest.raises(sqlite3.IntegrityError):
            store.add(edge(a, b))
        store.seal()
        assert build_proposals([a, b], store, "r", "v")["proposals"]
        assert store.get("a", "b")["triage_status"] == "HIGH_CONFIDENCE_PROPOSAL"
    finally:
        store.close()


def test_golden_source_verification_real_timestamp_and_ties(safety):
    a, b = record("a", name="First"), record("b", name="Second")
    a["source_system"] = b["source_system"] = "CRM"
    a["external_id"], b["external_id"] = "ZZZ", "AAA"
    a["source_updated_at"], b["source_updated_at"] = "2020-01-01T00:00:00Z", "2024-01-01T00:00:00+07:00"
    by_id = {r["id"]: r for r in (a, b)}
    assert build_golden(["b", "a"], by_id)["fields"]["name"]["value"] == "Second"
    a["normalized_fields"]["_name_verified"] = True
    assert build_golden(["a", "b"], by_id)["fields"]["name"]["value"] == "First"
    a["source_system"] = "BILL"
    assert build_golden(["a", "b"], by_id)["fields"]["name"]["value"] == "First"
    b["source_updated_at"] = "not-a-timestamp"
    assert timestamp(b["source_updated_at"]) is None
    assert timestamp("2024-01-01") is None


def test_golden_all_contacts_sources_alternatives_and_override(safety):
    a, b = record("a", phone="0912345678"), record("b", phone="0987654321")
    b["normalized_fields"]["phones"].append({"value": "0912345678", "quality": "VALID", "verification": "verified"})
    by_id = {r["id"]: r for r in (a, b)}
    golden = build_golden(["b", "a"], by_id, existing_golden={"_golden_override": {"phone": "Reviewer contact", "name": "Chosen name"}})
    phone = golden["fields"]["phone"]
    assert {v["value"] for v in phone["values"]} == {"0912345678", "0987654321"}
    common = next(v for v in phone["values"] if v["value"] == "0912345678")
    assert {s["source_record_id"] for s in common["sources"]} == {"a", "b"}
    assert common["verified"]
    assert phone["value"] == "Reviewer contact" and phone["provenance"] == "STEWARD_OVERRIDE"
    assert phone["source_record_id"] is None
    assert golden["_golden_override"] == {"phone": "Reviewer contact", "name": "Chosen name"}
    assert phone["alternatives"]
    assert build_golden(["a", "b"], by_id, existing_golden=golden) == golden


def test_stage_cursor_and_small_equivalence_no_dataframe(safety, tmp_path, monkeypatch):
    records = [record(r, email="a@example.test", dob="1990-01-01", address="Hanoi") for r in "abc"]
    small = _compute(records, "r", "v", scoring_output_dir=tmp_path / "small")
    runner = StageRunner("r", "attempt", tmp_path / "large")
    monkeypatch.setattr(duckdb.DuckDBPyConnection, "df", lambda *args: (_ for _ in ()).throw(AssertionError("all-row dataframe forbidden")))
    try:
        large = runner.run_all(records, policy_version="v")
        assert isinstance(large["pairs"], DecisionStore)
        assert list(small["pairs"]) == list(large["pairs"])
        assert small["proposals"] == large["proposals"]
    finally:
        small["pairs"].close()
        runner.close_decisions()
        runner.con.close()


def test_rerun_preserves_golden_user_override(client, safety):
    run_id = seed_run(client)
    with client.session_factory.begin() as db:
        db.get(ResolutionProposal, "safe-ab").golden_fields = {"_golden_override": {"name": "Reviewed name"}}
        db.get(ResolutionPair, "uncertain-bc").user_decision = "DEFERRED"
        for row in db.query(ResolutionRecord).filter_by(run_id=run_id):
            row.normalized_fields = record(row.id)["normalized_fields"]
        db.add(ResolutionTask(run_id=run_id, status="RETRYABLE_FAILED"))
    run_pipeline(run_id, client.session_factory, lambda *args: None)
    run_pipeline(run_id, client.session_factory, lambda *args: None)
    with client.session_factory() as db:
        assert db.get(ResolutionProposal, "safe-ab").golden_fields == {"_golden_override": {"name": "Reviewed name"}}


def test_manual_merge_carries_override_and_multiple_contacts(client, safety):
    run_id = seed_run(client)
    with client.session_factory.begin() as db:
        db.get(ResolutionProposal, "safe-ab").golden_fields = {"_golden_override": {"name": "Reviewed name"}}
        for row in db.query(ResolutionRecord).filter_by(run_id=run_id):
            row.normalized_fields = record(row.id, email="common@example.test")["normalized_fields"]
    response = client.post(f"/api/v1/mdm/runs/{run_id}/review-pairs/uncertain-bc/decision", json={"decision": "merge", "revision": 1})
    assert response.status_code == 200, response.text
    with client.session_factory() as db:
        golden = db.get(ResolutionProposal, response.json()["proposal_id"]).golden_fields
        assert golden["_golden_override"]["name"] == "Reviewed name"
        assert golden["fields"]["phone"]["values"]


def test_rollback_retains_legacy_same_source_and_golden(safety, monkeypatch):
    a, b = record("a"), record("b")
    a["source_system"] = b["source_system"] = "CRM"
    monkeypatch.setattr(get_settings(), "cluster_safety_v2_enabled", False)
    assert not build_proposals([a, b], [edge(a, b)], "r", "v")["proposals"]
    assert "survivorship_version" not in build_golden(["a", "b"], {"a": a, "b": b})
