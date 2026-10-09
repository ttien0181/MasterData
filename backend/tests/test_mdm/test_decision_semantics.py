"""Decision meaning, manual parity, retry preservation and global union order."""
from copy import deepcopy
from itertools import permutations

import pytest
from masterone.config import get_settings
from masterone.mdm.models import (
    ResolutionAuditEvent,
    ResolutionNegativeEdge,
    ResolutionPair,
    ResolutionProposal,
    ResolutionRecord,
    ResolutionReviewCase,
    ResolutionRun,
    ResolutionTask,
)
from masterone.mdm.pipeline.clustering import build_proposals
from masterone.mdm.pipeline.decision_semantics import (
    ReviewDecision,
    adapt_evaluation,
    evaluate_prepared,
    identifier_key,
    load_cannot_links,
    person_constraints,
)
from masterone.mdm.pipeline.matching import (
    _decide_legacy_rule_result,
    compare_and_score_rule,
    score_pair,
    value_frequencies,
)
from masterone.mdm.pipeline.scoring_engine import MemorySnapshot, ScoringConfig, ScoringEngine, ScoringInputChangedError
from masterone.mdm.pipeline.stage_runner import StageRunner
from masterone.mdm.pipeline.standardization import standardize
from masterone.mdm.resolution_service import _compute, run_pipeline

from tests.test_mdm.test_resolution_items import seed_run


@pytest.fixture
def v2(monkeypatch):
    monkeypatch.setattr(get_settings(), "decision_semantics_v2_enabled", True)


def record(rid, **fields):
    raw = {"name": "Nguyen Van An", "phone": "0912345678", **fields}
    nf, flags = standardize(raw)
    return {"id": rid, "external_id": rid, "source_system": f"source-{rid}", "entity_type": "PERSON",
            "raw_fields": raw, "normalized_fields": nf, "quality_flags": flags}


@pytest.mark.parametrize("decision", ["REJECTED", "DEFERRED", "UNDECIDED", "ACCEPTED"])
def test_review_is_not_identity_or_cannot_link(v2, decision):
    a, b = record("a", email="a@example.test", dob="1990-01-02"), record("b", email="a@example.test", dob="1990-01-02")
    prepared = compare_and_score_rule(a, b, set())
    _, evaluation, eligibility = evaluate_prepared(prepared, a, b, review=ReviewDecision(decision))
    assert evaluation.conclusion == "MATCH"
    assert not evaluation.constraints.cannot_link
    assert eligibility.manual_allowed == (decision != "REJECTED")
    assert eligibility.auto_allowed == (decision == "UNDECIDED")


@pytest.mark.parametrize("decision", ["BLOCKED", "DEFERRED", "NOT_MERGEABLE", "INSUFFICIENT_EVIDENCE"])
def test_permission_and_routing_do_not_prove_non_match(decision):
    _, evaluation, _ = adapt_evaluation({"decision": decision})
    assert evaluation.conclusion == "UNDETERMINED"
    assert not evaluation.constraints.cannot_link


def test_explicit_non_match_retained_and_cannot_link_only_forbids(v2):
    _, evaluation, eligibility = adapt_evaluation({"decision": "REVIEW"}, review=ReviewDecision("NON_MATCH"))
    assert evaluation.conclusion == "NON_MATCH" and not eligibility.manual_allowed
    a, b = record("a"), record("b")
    links = frozenset({("a", "b")})
    _, evaluation, eligibility = evaluate_prepared(compare_and_score_rule(a, b, set(), negative_pairs=links),
        a, b, negative_pairs=links)
    assert evaluation.constraints.cannot_link
    assert evaluation.conclusion == "UNDETERMINED" and not eligibility.manual_allowed


@pytest.mark.parametrize("verified", [False, True])
@pytest.mark.parametrize("field,other", [("phone", "0987654321"), ("email", "other@example.test")])
def test_different_contacts_do_not_prove_different_people(v2, verified, field, other):
    a, b = record("a", email="a@example.test"), record("b", **{"email": "a@example.test", field: other})
    for r in (a, b):
        r["normalized_fields"][f"_{field}_verified"] = verified
    assert not person_constraints(a, b).forbids_merge
    result = score_pair(a, b, set())
    ev = next(e for e in result["field_evidence"] if e["field"] == field)
    assert ev["state"] == "DF" and ev["conflict_meaning"] == "CHANGE_OR_MULTI_CONTACT"


@pytest.mark.parametrize("verified", [False, True])
def test_dob_verified_conflict_vs_unverified_difference(v2, verified):
    a, b = record("a", dob="1990-01-02"), record("b", dob="1991-01-02")
    for r in (a, b):
        r["normalized_fields"]["_dob_verified"] = verified
    hard = person_constraints(a, b)
    assert hard.forbids_merge == verified
    result = score_pair(a, b, set())
    if not verified:
        assert (result["primary_group"], result["subgroup"]) == ("D", "D4_DOB_CONFLICT")
        assert next(e for e in result["field_evidence"] if e["field"] == "dob")["conflict_meaning"] == "UNVERIFIED_DIFFERENCE"
    else:
        assert result["decision"] == "NOT_MERGEABLE"
    assert adapt_evaluation(result, hard)[1].conclusion != "NON_MATCH"


@pytest.mark.parametrize("risk", ["_phone_shared", "_phone_shared_risk", "phone_shared_risk"])
def test_shared_phone_cannot_be_identity_anchor(v2, risk):
    a, b = record("a"), record("b")
    a["normalized_fields"][risk] = True
    result = score_pair(a, b, set())
    assert next(e for e in result["field_evidence"] if e["field"] == "phone")["state"] == "SH"
    assert result["decision"] != "MERGEABLE"


def test_shared_phone_uses_full_corpus(v2):
    records = [record(str(i)) for i in range(7)]
    result = score_pair(records[0], records[1], set(), value_frequencies(records))
    assert result["comparisons"]["phone"]["state"] == "SH"
    assert result["decision"] != "MERGEABLE"


@pytest.mark.parametrize("risk", [{"shared": True}, {"shared_risk": True}, {"historical": True},
                                {"valid_to": "2020-01-01"}, {"origin_id": "copied-origin"}])
def test_contact_risk_not_hidden_by_another_contact(v2, risk):
    a, b = record("a"), record("b")
    for r in (a, b):
        r["normalized_fields"]["phones"] = [
            {"value": "0912345678", "quality": "VALID"},
            {"value": "0987654321", "quality": "VALID", **risk}]
    assert score_pair(a, b, set())["comparisons"]["phone"]["state"] == "SH"


def test_unknown_id_namespace_and_multi_value_contract(v2):
    a, b = identified("a", "123456789012", "CUSTOM"), identified("b", "999888777666", "CUSTOM")
    assert identifier_key(a["normalized_fields"]) is None
    for r in (a, b):
        r["normalized_fields"]["government_id_identity_unique"] = True
    assert not person_constraints(a, b).forbids_merge
    assert score_pair(a, b, set())["comparisons"]["government_id"]["state"] == "DF"
    for r in (a, b):
        r["normalized_fields"]["government_id_single_valued"] = True
    assert person_constraints(a, b).forbids_merge


def test_prepared_evidence_cannot_override_current_constraints(v2):
    a, b = record("a"), record("b")
    prepared = compare_and_score_rule(a, b, set())
    payload, _, eligibility = evaluate_prepared(prepared, a, b, negative_pairs=frozenset({("a", "b")}))
    assert payload["decision"] == "NOT_MERGEABLE" and not eligibility.manual_allowed


def test_cluster_guard_checks_non_candidate_verified_dob(v2):
    records = [record("a", dob="1990-01-02"), record("b"), record("c", dob="1991-01-02")]
    for r in records:
        r["normalized_fields"]["_dob_verified"] = True
    from masterone.mdm.pipeline.cluster_guards import run_cluster_guards
    violations = run_cluster_guards(frozenset({"a", "b"}), frozenset({"c"}), {r["id"]: r for r in records})
    assert any(v.reason == "VERIFIED_DOB_CONFLICT" and v.severity == "BLOCK" for v in violations)


def identified(rid, value, namespace="VN", kind="CCCD_12", verified=True):
    r = record(rid, government_id=value)
    r["normalized_fields"].update(government_id_namespace=namespace, government_id_type=kind,
                                 _government_id_verified=verified)
    return r


@pytest.mark.parametrize("namespace,kind,verified,blocked", [
    ("VN", "CCCD_12", True, True), ("US", "CCCD_12", True, False),
    (None, "CCCD_12", True, False), ("VN", "CMND_9", True, False),
    ("VN", "CCCD_12", False, False),
])
def test_government_id_requires_type_verification_namespace(v2, namespace, kind, verified, blocked):
    a, b = identified("a", "123456789012"), identified("b", "999888777666", namespace, kind, verified)
    assert person_constraints(a, b).forbids_merge == blocked
    assert (score_pair(a, b, set())["decision"] == "NOT_MERGEABLE") == blocked
    from masterone.mdm.pipeline.cluster_guards import _unique_id_guard
    assert (_unique_id_guard([a, b]) is not None) == blocked


def test_equal_id_unknown_namespace_not_anchor(v2):
    a, b = identified("a", "123456789012", None), identified("b", "123456789012", None)
    assert identifier_key(a["normalized_fields"]) is None
    assert score_pair(a, b, set())["comparisons"]["government_id"]["state"] == "NC"


def test_person_evaluator_does_not_extend_to_organizations(v2):
    a, b = record("a"), record("b")
    a["entity_type"] = b["entity_type"] = "ORGANIZATION"
    assert person_constraints(a, b).violations == ("PERSON_POLICY_OUT_OF_SCOPE",)
    assert score_pair(a, b, set())["decision"] == "NOT_MERGEABLE"


@pytest.mark.parametrize("fields", [
    {}, {"name": "NGUYEN VAN AN"}, {"name": "Nguyen Van A", "email": "a@example.test"},
    {"name": "Le Thi Hoa"}, {"dob": "1990-01-02"}, {"name": "", "email": "a@example.test"},
])
def test_group_subgroup_policy_adapter_regression(v2, fields):
    a, b = record("a", email="a@example.test", dob="1991-01-02"), record("b", **fields)
    prepared = compare_and_score_rule(a, b, set())
    baseline = _decide_legacy_rule_result(prepared, a, b, set())
    current, _, _ = evaluate_prepared(prepared, a, b)
    for key in ("primary_group", "subgroup", "matched_subgroups", "decision_subgroup", "normalization_tags", "policy_id", "decision"):
        assert current[key] == baseline[key]


def test_pair_batches_complete_before_deterministic_cluster_union(v2):
    records = [record(c, email="a@example.test", dob="1990-01-02", address="Hanoi") for c in "abc"]
    engine = ScoringEngine(MemorySnapshot(records), ScoringConfig(batch_size=1))
    prepared = [engine.score_batch([{"left_id": a, "right_id": b, "reasons": ["exact_phone"]}])[0]
                for a, b in [("a", "b"), ("b", "c"), ("a", "c")]]
    by_id = {r["id"]: r for r in records}
    snapshots = []
    for completion_order in permutations(prepared):
        pairs = [{"id": s.left_id+s.right_id, **engine.decide(s, by_id)} for s in completion_order]
        snapshot = build_proposals(records, deepcopy(pairs), "run", "v1",
                                   execution_blocked_pairs=frozenset({("a", "c")}))
        snapshots.append(snapshot["proposals"])
        assert snapshot["proposals"]
        assert not any({"a", "c"} <= set(p["member_record_ids"]) for p in snapshot["proposals"])
    assert all(snapshot == snapshots[0] for snapshot in snapshots)


def test_changed_semantics_invalidates_inflight_score(v2, monkeypatch):
    engine = ScoringEngine(MemorySnapshot([record("a"), record("b")]))
    monkeypatch.setattr(get_settings(), "decision_semantics_v2_enabled", False)
    with pytest.raises(ScoringInputChangedError):
        engine.score_batch([{"left_id": "a", "right_id": "b", "reasons": []}])


def test_small_stage_evaluation_parity(v2, tmp_path):
    records = [identified("a", "123456789012"), identified("b", "999888777666")]
    small = _compute(records, "parity", "person-v1", scoring_output_dir=tmp_path / "small")
    runner = StageRunner("parity", "attempt", tmp_path / "stages")
    try:
        large = runner.run_all(records, policy_version="person-v1")
    finally:
        runner.con.close()
    assert [(p["id"], p["decision"], p["reason"]) for p in small["pairs"]] == [
        (p["id"], p["decision"], p["reason"]) for p in large["pairs"]]
    assert not small["proposals"] and not large["proposals"]


@pytest.mark.parametrize("decision", ["REJECTED", "ACCEPTED", "DEFERRED", "NON_MATCH"])
def test_retry_does_not_overwrite_review_golden_or_history(client, v2, decision):
    run_id = seed_run(client)
    with client.session_factory.begin() as db:
        for row in db.query(ResolutionRecord).filter_by(run_id=run_id):
            r = record(row.id)
            row.normalized_fields, row.raw_fields = r["normalized_fields"], r["raw_fields"]
        pair = db.get(ResolutionPair, "uncertain-bc")
        pair.user_decision, pair.revision = decision, 9
        proposal = db.get(ResolutionProposal, "safe-ab")
        proposal.status, proposal.revision = "BLOCKED", 4
        proposal.golden_fields = {"_golden_override": {"name": "Reviewer value"}}
        case = db.get(ResolutionReviewCase, "case-bc")
        case.status, case.revision, case.first_reviewer_id = "REJECTED", 6, "reviewer"
        db.add(ResolutionTask(run_id=run_id, status="RETRYABLE_FAILED"))
        db.add(ResolutionAuditEvent(run_id=run_id, actor_user_id=db.get(ResolutionRun, run_id).owner_user_id,
            action="TEST_REVIEW_SNAPSHOT", details={"decision": decision}))
    for _ in range(2):
        run_pipeline(run_id, client.session_factory, lambda *args: None)
    with client.session_factory() as db:
        pair = db.get(ResolutionPair, "uncertain-bc")
        assert (pair.user_decision, pair.revision) == (decision, 9)
        proposal = db.get(ResolutionProposal, "safe-ab")
        assert (proposal.status, proposal.revision) == ("BLOCKED", 4)
        assert proposal.golden_fields == {"_golden_override": {"name": "Reviewer value"}}
        case = db.get(ResolutionReviewCase, "case-bc")
        assert (case.status, case.revision, case.first_reviewer_id) == ("REJECTED", 6, "reviewer")
        assert db.query(ResolutionAuditEvent).filter_by(action="TEST_REVIEW_SNAPSHOT").count() == 1
        assert not db.query(ResolutionProposal).filter_by(run_id=run_id, status="AUTO_READY").count()
        assert not db.query(ResolutionNegativeEdge).filter_by(run_id=run_id).count()


def test_load_explicit_negative_edges_not_rejected_proposals(client, v2):
    run_id = seed_run(client)
    with client.session_factory.begin() as db:
        db.get(ResolutionPair, "uncertain-bc").user_decision = "REJECTED"
        db.get(ResolutionProposal, "safe-ab").status = "REJECTED"
        db.add(ResolutionNegativeEdge(run_id=run_id, left_record_id="a", right_record_id="c",
            created_by="reviewer", reason="SPLIT", active=True))
        db.add(ResolutionNegativeEdge(run_id=run_id, left_record_id="a", right_record_id="b",
            created_by="reviewer", reason="SPLIT", active=False))
    with client.session_factory() as db:
        assert load_cannot_links(db, run_id) == frozenset({("a", "c")})


def test_historical_non_match_blocks_preview_and_remains_visible(client):
    from types import SimpleNamespace

    from masterone.mdm.resolution_preview import _guard

    run_id = seed_run(client)
    with client.session_factory.begin() as db:
        pair = db.get(ResolutionPair, "uncertain-bc")
        pair.user_decision = "NON_MATCH"
        assert _guard([SimpleNamespace(id="abc", member_record_ids=["a", "b", "c"])], [pair]) == {
            "abc": ["uncertain-bc"]}
    assert client.get(f"/api/v1/mdm/runs/{run_id}/items/no_merge").json()["total"] == 1


def test_stage_materialization_retry_keeps_original_pair_identity(client, v2, tmp_path):
    import sqlite3

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    run_id = seed_run(client)
    with client.session_factory.begin() as db:
        pair = db.get(ResolutionPair, "uncertain-bc")
        pair.user_decision, pair.revision = "REJECTED", 7
    # Clone the fixture into a disposable file so separately owned engines share
    # durable state. A second sqlite:// in-memory engine is a different database.
    path = tmp_path / "materialization.db"
    connection = client.session_factory.kw["bind"].raw_connection()
    try:
        with sqlite3.connect(path) as destination:
            connection.driver_connection.backup(destination)
    finally:
        connection.close()
    url = "sqlite:///" + path.as_posix()
    records = [record(c, email="a@example.test", dob="1990-01-02", address="Hanoi") for c in "abc"]
    for attempt in ("first", "retry"):
        runner = StageRunner(run_id, attempt, tmp_path / attempt, db_url=url)
        try:
            runner.run_all(records, policy_version="person-v1", return_data=False)
        finally:
            runner.con.close()
    engine = create_engine(url)
    try:
        with Session(engine) as db:
            pair = db.get(ResolutionPair, "uncertain-bc")
            assert (pair.user_decision, pair.revision) == ("REJECTED", 7)
            assert db.query(ResolutionPair).filter_by(run_id=run_id, left_record_id="b", right_record_id="c").count() == 1
            assert db.query(ResolutionProposal).filter_by(run_id=run_id).count() == 1
    finally:
        engine.dispose()


@pytest.mark.parametrize("verified,status", [(True, 409), (False, 200)])
def test_manual_review_uses_current_evaluator_across_proposal_members(client, v2, verified, status):
    run_id = seed_run(client)
    with client.session_factory.begin() as db:
        # A-C has no candidate edge. B-C review expands existing A-B proposal.
        for rid, value in [("a", "123456789012"), ("b", "123456789012"), ("c", "999888777666")]:
            r = identified(rid, value, verified=verified)
            db.get(ResolutionRecord, rid).normalized_fields = r["normalized_fields"]
    response = client.post(f"/api/v1/mdm/runs/{run_id}/review-pairs/uncertain-bc/decision",
                           json={"decision": "merge", "revision": 1})
    assert response.status_code == status, response.text
    with client.session_factory() as db:
        assert db.get(ResolutionPair, "uncertain-bc").user_decision == ("UNDECIDED" if verified else "ACCEPTED")
