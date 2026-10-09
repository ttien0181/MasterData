from masterone.mdm.models import ResolutionPair, ResolutionRecord, ResolutionRun

from .test_resolution_items import seed_run


def seed_analytics(client):
    run_id = seed_run(client)
    with client.session_factory.begin() as db:
        db.get(ResolutionRun, run_id).record_count = 4
        db.get(ResolutionRecord, "a").normalized_fields = {"_field_quality": {
            "name": "VALID", "phone": "VALID", "email": "VALID", "address": "VALID"}}
        db.get(ResolutionRecord, "b").normalized_fields = {"_field_quality": {
            "name": "VALID", "phone": "VALID", "email": "MISSING", "address": "VALID"}}
        db.add(ResolutionRecord(id="standalone", run_id=run_id, file_id="file-items", external_id="D",
            source_system="CRM", raw_fields={}, normalized_fields={"_field_quality": {
                "name": "VALID", "phone": "VALID", "email": "INVALID", "address": "MISSING"}}))
        db.add(ResolutionPair(id="safe-edge", run_id=run_id, left_record_id="a", right_record_id="b",
            blocking_reasons=[], field_evidence=[], score=1, pair_decision="MATCH_CANDIDATE",
            decision_reason="SAFE", triage_status="HIGH_CONFIDENCE_PROPOSAL", triage_reason="SAFE"))
        pair = db.get(ResolutionPair, "uncertain-bc")
        pair.field_evidence = [{"field": "name", "state": "CF"}, {"field": "name", "state": "CF"},
                               {"field": "phone", "state": "EX"}, {"field": "email", "state": "NC"},
                               {"field": "address", "state": "CF"}]
    return f"/api/v1/mdm/runs/{run_id}"


def test_distribution_counts_each_source_record_once_with_review_precedence(client):
    prefix = seed_analytics(client)
    response = client.get(f"{prefix}/analytics")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["decisions"] == {"basis": "initial_classification", "unit": "source_record",
        "total": 4, "automatic": 1, "review": 2, "no_duplicate_detected": 1, "automatic_percent": 25.0}
    assert {row["field"]: row["count"] for row in data["conflicts"]["fields"]} == {
        "name": 1, "address": 1, "phone": 0, "email": 0}
    assert data["conflicts"]["review_pair_count"] == 1


def test_quality_uses_field_validation_not_source_share_or_fake_zero(client):
    prefix = seed_analytics(client)
    quality = client.get(f"{prefix}/analytics").json()["quality"]
    sources = {row["source"]: row for row in quality["sources"]}
    assert sources["CRM"]["score_percent"] == 75
    assert sources["CRM"]["valid_fields"] == 6
    assert sources["CRM"]["assessed_fields"] == 8
    assert sources["ERP"]["score_percent"] == 75
    assert sources["BILL"]["score_percent"] is None
    assert sources["BILL"]["coverage_percent"] == 0
    assert quality["fields"] == ["name", "phone", "email", "address"]


def test_deciding_pair_clears_pending_conflicts_without_rewriting_initial_distribution(client):
    prefix = seed_analytics(client)
    before = client.get(f"{prefix}/analytics").json()
    decision = client.post(f"{prefix}/review-pairs/uncertain-bc/decision", json={"decision": "no_merge", "revision": 1})
    assert decision.status_code == 200
    after = client.get(f"{prefix}/analytics").json()
    assert before["decisions"] == after["decisions"]
    assert after["conflicts"]["review_pair_count"] == 0
    assert all(row["count"] == 0 for row in after["conflicts"]["fields"])


def test_analytics_does_not_mix_runs_and_waits_for_completed_pipeline(client):
    prefix = seed_analytics(client)
    with client.session_factory.begin() as db:
        run = db.get(ResolutionRun, "run-items")
        db.add(ResolutionRun(id="empty-run", owner_user_id=run.owner_user_id,
            record_count=0, file_count=0, status="COMPLETED"))
        run.status = "RUNNING"
    assert client.get(f"{prefix}/analytics").status_code == 409
    empty = client.get("/api/v1/mdm/runs/empty-run/analytics").json()
    assert empty["decisions"]["total"] == 0
    assert empty["decisions"]["automatic_percent"] == 0
    assert empty["quality"]["sources"] == []
