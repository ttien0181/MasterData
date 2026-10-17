"""The final CSV includes each source row once across review decisions."""

import csv
from io import StringIO

from masterone.auth.models import User
from masterone.mdm.models import (
    ResolutionExport,
    ResolutionFile,
    ResolutionPair,
    ResolutionProposal,
    ResolutionRecord,
    ResolutionReviewCase,
    ResolutionRun,
)
from masterone.mdm.pipeline.golden import build_golden


def seed_run(client):
    with client.session_factory() as db:
        owner = db.query(User).one()
        run = ResolutionRun(id="run-items", owner_user_id=owner.id, status="COMPLETED",
                            phase="completed", record_count=3, file_count=1)
        file = ResolutionFile(id="file-items", run_id=run.id, filename="source.csv",
                              sha256="a" * 64, row_count=3)
        db.add_all([run, file])
        records = [
            ResolutionRecord(id="a", run_id=run.id, file_id=file.id, external_id="A",
                             source_system="CRM", raw_fields={"name": "An", "phone": "111", "email": "", "address": ""}, normalized_fields={}),
            ResolutionRecord(id="b", run_id=run.id, file_id=file.id, external_id="B",
                             source_system="ERP", raw_fields={"name": "An", "phone": "111", "email": "", "address": ""}, normalized_fields={}),
            ResolutionRecord(id="c", run_id=run.id, file_id=file.id, external_id="C",
                             source_system="BILL", raw_fields={"name": "An", "phone": "222", "email": "", "address": ""}, normalized_fields={}),
        ]
        db.add_all(records)
        payload = {row.id: {"id": row.id, "external_id": row.external_id,
                            "source_system": row.source_system, "raw_fields": row.raw_fields} for row in records}
        db.add(ResolutionProposal(id="safe-ab", run_id=run.id, member_record_ids=["a", "b"],
                                  golden_fields=build_golden(["a", "b"], payload), status="AUTO_READY", origin="AUTO"))
        db.add(ResolutionPair(id="uncertain-bc", run_id=run.id, left_record_id="b", right_record_id="c",
                              blocking_reasons=[], field_evidence=[], score=0.6, pair_decision="REVIEW",
                              decision_reason="AMBIGUOUS", triage_status="REVIEW", triage_reason="AMBIGUOUS"))
        db.add(ResolutionReviewCase(id="case-bc", run_id=run.id, pair_ids=["uncertain-bc"],
                                    member_record_ids=["b", "c"], primary_group="SHARED_VALUE",
                                    subgroup="PHONE", signature_hash="signature", triage_reason="AMBIGUOUS"))
        db.commit()
    return run.id


def read_csv(response):
    return list(csv.DictReader(StringIO(response.content.decode("utf-8-sig"))))


def test_pair_review_compares_only_two_records_and_keeps_other_edges_pending(client):
    run_id = seed_run(client)
    prefix = f"/api/v1/mdm/runs/{run_id}"
    with client.session_factory.begin() as db:
        db.add(ResolutionPair(id="uncertain-ac", run_id=run_id, left_record_id="a", right_record_id="c",
            blocking_reasons=[], field_evidence=[], score=0.6, pair_decision="REVIEW",
            decision_reason="AMBIGUOUS", triage_status="REVIEW", triage_reason="AMBIGUOUS"))
        case = db.get(ResolutionReviewCase, "case-bc")
        case.pair_ids = ["uncertain-bc", "uncertain-ac"]
        case.member_record_ids = ["a", "b", "c"]
    listing = client.get(f"{prefix}/items/review_pairs").json()
    assert listing["total"] == 2
    assert all(item["member_count"] == 2 for item in listing["items"])
    detail = client.get(f"{prefix}/items/review_pairs/uncertain-bc").json()
    assert set(detail["member_record_ids"]) == {"b", "c"}
    assert detail["affected_record_count"] == 3
    result = client.post(f"{prefix}/review-pairs/uncertain-bc/decision",
                         json={"decision": "no_merge", "revision": detail["revision"]})
    assert result.status_code == 200, result.text
    assert [item["id"] for item in client.get(f"{prefix}/items/review_pairs").json()["items"]] == ["uncertain-ac"]
    assert client.get(f"{prefix}/items/no_merge").json()["total"] == 1
    with client.session_factory() as db:
        case = db.get(ResolutionReviewCase, "case-bc")
        assert case.status == "OPEN"
        assert case.pair_ids == ["uncertain-ac"]
    # A+C would also merge B because A+B is already a safe group. The rejected B+C blocks that.
    blocked = client.post(f"{prefix}/review-pairs/uncertain-ac/decision", json={"decision": "merge", "revision": 1})
    assert blocked.status_code == 409


def test_pair_approval_creates_one_disjoint_proposal_and_audit(client):
    run_id = seed_run(client)
    prefix = f"/api/v1/mdm/runs/{run_id}"
    result = client.post(f"{prefix}/review-pairs/uncertain-bc/decision", json={"decision": "merge", "revision": 1})
    assert result.status_code == 200, result.text
    assert result.json()["affected_record_count"] == 3
    assert client.get(f"{prefix}/items/review_pairs").json()["total"] == 0
    groups = client.get(f"{prefix}/items/merge").json()["items"]
    assert len(groups) == 1
    assert set(groups[0]["member_record_ids"]) == {"a", "b", "c"}
    assert groups[0]["origin"] == "REVIEW"
    assert client.get(f"{prefix}/audit").json()["items"][0]["action"] == "PAIR_ACCEPTED"
    assert client.post(f"{prefix}/review-pairs/uncertain-bc/decision", json={"decision": "no_merge", "revision": 1}).status_code == 409
    response = client.post(f"{prefix}/exports", json={"choices": {groups[0]["id"]: {"phone": "c"}}})
    assert response.status_code == 200, response.text
    assert read_csv(response)[0]["source_record_ids"] == "A|B|C"


def test_stage_all_filtered_pairs_across_pages_without_merging(client):
    run_id = seed_run(client)
    prefix = f"/api/v1/mdm/runs/{run_id}"
    with client.session_factory.begin() as db:
        db.get(ResolutionRun, run_id).record_count = 32
        db.get(ResolutionFile, "file-items").row_count = 32
        for index in range(29):
            record_id = f"extra-record-{index:02d}"
            db.add(ResolutionRecord(id=record_id, run_id=run_id, file_id="file-items",
                external_id=record_id, source_system="BILL",
                raw_fields={"name": "An", "phone": "222", "email": "", "address": ""},
                normalized_fields={}))
            db.add(ResolutionPair(id=f"extra-{index:02d}", run_id=run_id,
                left_record_id="b", right_record_id=record_id, blocking_reasons=[],
                field_evidence=[], score=0.6, pair_decision="REVIEW", decision_reason="AMBIGUOUS",
                triage_status="REVIEW", triage_reason="AMBIGUOUS", primary_group="A"))
    response = client.post(f"{prefix}/review-pairs/stage", json={
        "all_matching": True, "primary_group": "A", "excluded_ids": ["extra-01"]})
    assert response.status_code == 200, response.text
    assert response.json()["staged_count"] == 28
    assert client.get(f"{prefix}/items/review_pairs").json()["total"] == 2
    staged = client.get(f"{prefix}/items/merge").json()
    assert staged["total"] == 29
    assert client.get(f"{prefix}/overview").json()["counts"]["auto"] == 29
    assert staged["items"][0]["status"] == "STAGED_FOR_MERGE"
    with client.session_factory() as db:
        assert db.get(ResolutionPair, "extra-00").user_decision == "UNDECIDED"
        assert db.get(ResolutionProposal, "safe-ab").status == "AUTO_READY"
    returned = client.post(f"{prefix}/review-pairs/extra-00/unstage")
    assert returned.status_code == 200, returned.text
    assert client.get(f"{prefix}/items/review_pairs").json()["total"] == 3


def test_staged_pair_is_checked_and_merged_only_at_csv_preview(client):
    run_id = seed_run(client)
    prefix = f"/api/v1/mdm/runs/{run_id}"
    staged = client.post(f"{prefix}/review-pairs/stage", json={"pair_ids": ["uncertain-bc"]})
    assert staged.status_code == 200, staged.text
    assert client.post(f"{prefix}/exports", json={"acknowledge_unreviewed": True}).status_code == 409
    with client.session_factory() as db:
        assert db.get(ResolutionPair, "uncertain-bc").user_decision == "UNDECIDED"
        assert db.get(ResolutionProposal, "safe-ab").status == "AUTO_READY"
    preview = client.post(f"{prefix}/previews", json={"acknowledge_unreviewed": True})
    assert preview.status_code == 200, preview.text
    assert preview.json()["accepted_pair_count"] == 1
    assert preview.json()["eligible_count"] == 1
    with client.session_factory() as db:
        pair = db.get(ResolutionPair, "uncertain-bc")
        assert pair.user_decision == "UNDECIDED"
        assert pair.staged_for_merge is True
        assert db.get(ResolutionProposal, "safe-ab").status == "AUTO_READY"
    executed = client.post(f"{prefix}/previews/{preview.json()['preview_id']}/execute",
                           json={"execute_request_id": "once"})
    assert executed.status_code == 200, executed.text
    with client.session_factory() as db:
        pair = db.get(ResolutionPair, "uncertain-bc")
        assert pair.user_decision == "ACCEPTED"
        assert pair.staged_for_merge is False
    rows = read_csv(client.get(f"{prefix}/exports/{preview.json()['preview_id']}/download"))
    assert len(rows) == 1
    assert rows[0]["source_record_ids"] == "A|B|C"
    assert rows[0]["phone_number"] == "111"


def test_staged_conflict_blocks_preview_without_accepting_pair(client):
    run_id = seed_run(client)
    prefix = f"/api/v1/mdm/runs/{run_id}"
    with client.session_factory.begin() as db:
        db.add(ResolutionPair(id="negative-ac", run_id=run_id,
            left_record_id="a", right_record_id="c", blocking_reasons=[],
            field_evidence=[], score=0.1, pair_decision="NON_MATCH", decision_reason="NON_MATCH",
            user_decision="NON_MATCH", triage_status="REJECTED", triage_reason="NON_MATCH"))
    assert client.post(f"{prefix}/review-pairs/stage", json={
        "pair_ids": ["uncertain-bc"]}).status_code == 200
    preview = client.post(f"{prefix}/previews", json={"acknowledge_unreviewed": True})
    assert preview.status_code == 409, preview.text
    with client.session_factory() as db:
        pair = db.get(ResolutionPair, "uncertain-bc")
        assert pair.staged_for_merge is True
        assert pair.user_decision == "UNDECIDED"
        assert db.get(ResolutionProposal, "safe-ab").status == "AUTO_READY"


def test_newly_staged_pair_invalidates_an_older_preview(client):
    run_id = seed_run(client)
    prefix = f"/api/v1/mdm/runs/{run_id}"
    with client.session_factory.begin() as db:
        db.add(ResolutionPair(id="uncertain-ac", run_id=run_id,
            left_record_id="a", right_record_id="c", blocking_reasons=[],
            field_evidence=[], score=0.6, pair_decision="REVIEW", decision_reason="AMBIGUOUS",
            triage_status="REVIEW", triage_reason="AMBIGUOUS"))
    preview = client.post(f"{prefix}/previews", json={"acknowledge_unreviewed": True})
    assert preview.status_code == 200, preview.text
    assert client.post(f"{prefix}/review-pairs/stage", json={
        "pair_ids": ["uncertain-ac"]}).status_code == 200
    response = client.post(f"{prefix}/previews/{preview.json()['preview_id']}/execute",
                           json={"execute_request_id": "stale"})
    assert response.status_code == 409, response.text


def test_pair_review_checks_revision_role_and_run_ownership(client):
    run_id = seed_run(client)
    prefix = f"/api/v1/mdm/runs/{run_id}"
    assert client.post(f"{prefix}/review-pairs/uncertain-bc/decision", json={"decision": "merge", "revision": 99}).status_code == 409
    with client.session_factory.begin() as db:
        db.query(User).one().role = "VIEWER"
    assert client.get(f"{prefix}/items/review_pairs").status_code == 200
    assert client.post(f"{prefix}/review-pairs/uncertain-bc/decision", json={"decision": "merge", "revision": 1}).status_code == 403
    with client.session_factory.begin() as db:
        owner = db.query(User).one()
        owner.role = "STEWARD"
        other = ResolutionRun(id="other-pair-run", owner_user_id=owner.id, status="COMPLETED", record_count=0, file_count=0)
        db.add(other)
    assert client.post("/api/v1/mdm/runs/other-pair-run/review-pairs/uncertain-bc/decision", json={"decision": "merge", "revision": 1}).status_code == 404


def test_review_choice_survives_move_to_merge_and_final_csv(client):
    run_id = seed_run(client)
    prefix = f"/api/v1/mdm/runs/{run_id}"
    review = client.get(f"{prefix}/items/review").json()
    assert review["total"] == 1
    assert review["items"][0]["member_count"] == 3
    moved = client.post(f"{prefix}/review-items/transition", json={"item_ids": ["case-bc"], "decision": "merge"})
    assert moved.status_code == 200, moved.text
    proposal_id = moved.json()["changed_ids"][0]
    detail = client.get(f"{prefix}/items/merge/{proposal_id}").json()
    assert detail["choices"] == {}
    assert client.post(f"{prefix}/export-readiness", json={}).json()["can_export"] is False
    assert client.post(f"{prefix}/exports", json={}).status_code == 409
    assert client.post(f"{prefix}/exports", json={"choices": {proposal_id: {"phone": "outside"}}}).status_code == 422
    assert client.post(f"{prefix}/export-readiness", json={"choices": {proposal_id: {"phone": "c"}}}).json()["can_export"] is True
    response = client.post(f"{prefix}/exports", json={"choices": {proposal_id: {"phone": "c"}}})
    assert response.status_code == 200, response.text
    rows = read_csv(response)
    assert len(rows) == 1
    assert rows[0]["phone_number"] == "222"
    assert rows[0]["source_record_ids"] == "A|B|C"
    assert rows[0]["record_count"] == "3"
    export_id = client.get(f"{prefix}/exports").json()["items"][0]["id"]
    assert client.get(f"{prefix}/exports/{export_id}/download").content == response.content
    comparison = client.get(f"{prefix}/exports/{export_id}/compare").json()
    assert comparison["source_count"] == 3
    assert comparison["output_count"] == 1
    assert comparison["source_sample"][0]["id"] == "A"
    source_rows = read_csv(client.get(f"{prefix}/source-records.csv"))
    assert len(source_rows) == 3


def test_compare_reads_export_snapshot_from_r2_when_database_bytes_are_cleared(client, monkeypatch):
    run_id = seed_run(client)
    owner_id = client.get("/api/v1/auth/me").json()["id"]
    export_bytes = (
        b"master_record_id,full_name,phone_number,source_record_ids,source_systems,record_count\n"
        b"merged-1,An,111,A|B,CRM|ERP,2\n"
    )
    with client.session_factory.begin() as db:
        db.add(ResolutionExport(
            id="r2-export", run_id=run_id, actor_user_id=owner_id,
            csv_bytes=None, r2_key=f"exports/{run_id}/r2-export.csv", row_count=1,
            merged_group_count=1, pending_review_count=0,
        ))

    class FakeR2:
        def download_file(self, key, path):
            assert key == f"exports/{run_id}/r2-export.csv"
            with open(path, "wb") as stream:
                stream.write(export_bytes)

    monkeypatch.setattr("masterone.api.mdm_resolution.get_store", lambda _settings: FakeR2())
    response = client.get(f"/api/v1/mdm/runs/{run_id}/exports/r2-export/compare")
    assert response.status_code == 200, response.text
    assert response.json()["source_count"] == 3
    assert response.json()["output_count"] == 1
    assert response.json()["output_sample"][0]["source_record_ids"] == "A|B"


def test_automatic_non_match_rebuilds_field_evidence_without_changing_decision(client):
    run_id = seed_run(client)
    with client.session_factory() as db:
        db.add(ResolutionPair(id="auto-no", run_id=run_id, left_record_id="a", right_record_id="c",
                              blocking_reasons=["name"], field_evidence=[], score=0.1,
                              pair_decision="NON_MATCH", decision_reason="WEAK_FIELD_EVIDENCE",
                              triage_status="NON_MATCH_PROPOSAL", triage_reason="WEAK_FIELD_EVIDENCE"))
        db.commit()
    detail = client.get(f"/api/v1/mdm/runs/{run_id}/items/no_merge/auto-no").json()
    assert detail["score"] == 0.1
    assert detail["field_evidence"][0]["field"] == "name"
    assert len(detail["field_evidence"]) == 6  # name, phone, email, dob, address, government_id


def test_rejecting_uncertain_link_keeps_safe_group_and_exports_singleton(client):
    run_id = seed_run(client)
    prefix = f"/api/v1/mdm/runs/{run_id}"
    moved = client.post(f"{prefix}/review-items/transition", json={"item_ids": ["case-bc"], "decision": "no_merge"})
    assert moved.status_code == 200, moved.text
    assert client.get(f"{prefix}/items/merge").json()["total"] == 1
    assert client.get(f"{prefix}/items/no_merge").json()["total"] == 1
    assert client.get(f"{prefix}/items/review").json()["total"] == 0
    rows = read_csv(client.post(f"{prefix}/exports", json={}))
    assert sorted(row["source_record_ids"] for row in rows) == ["A|B", "C"]


def test_separate_records_are_actual_unmerged_rows_not_every_rejected_pair_member(client):
    run_id = seed_run(client)
    prefix = f"/api/v1/mdm/runs/{run_id}"
    before = client.get(f"{prefix}/separate-records").json()
    assert before["total"] == 1
    assert [item["external_id"] for item in before["items"]] == ["C"]
    assert before["items"][0]["review_pending"] is True
    assert client.get(f"{prefix}/overview").json()["counts"]["separate"] == 1

    client.post(f"{prefix}/review-items/transition",
                json={"item_ids": ["case-bc"], "decision": "no_merge"})
    after = client.get(f"{prefix}/separate-records", params={"q": "C"}).json()
    assert after["total"] == 1
    assert after["items"][0]["review_pending"] is False
    assert client.get(f"{prefix}/separate-records", params={"q": "CRM"}).json()["total"] == 0


def test_readiness_names_the_item_with_a_negative_link(client):
    run_id = seed_run(client)
    with client.session_factory() as db:
        db.add(ResolutionPair(id="blocked-ab", run_id=run_id, left_record_id="a", right_record_id="b",
                              blocking_reasons=[], field_evidence=[], score=0.1,
                              pair_decision="NON_MATCH", decision_reason="CONFLICT",
                              triage_status="NON_MATCH_PROPOSAL", triage_reason="CONFLICT"))
        db.commit()
    prefix = f"/api/v1/mdm/runs/{run_id}"
    readiness = client.post(f"{prefix}/export-readiness", json={}).json()
    assert readiness["can_export"] is False
    assert readiness["blockers"][0]["item_id"] == "safe-ab"
    assert readiness["blockers"][0]["kind"] == "NEGATIVE_LINK"
    assert readiness["blockers"][0]["label"] == "An"
    assert client.post(f"{prefix}/previews", json={"acknowledge_unreviewed": True}).status_code == 409


def test_export_requires_warning_acknowledgement_for_open_review(client):
    run_id = seed_run(client)
    prefix = f"/api/v1/mdm/runs/{run_id}"
    assert client.post(f"{prefix}/exports", json={}).status_code == 409
    response = client.post(f"{prefix}/exports", json={"acknowledge_unreviewed": True})
    assert response.status_code == 200, response.text
    rows = read_csv(response)
    assert sorted(row["source_record_ids"] for row in rows) == ["A|B", "C"]
    assert next(row for row in rows if row["source_record_ids"] == "C")["review_pending"] == "true"


def test_conflicting_review_group_stages_then_splits_without_overriding_non_match(client):
    run_id = seed_run(client)
    with client.session_factory() as db:
        db.add(ResolutionPair(id="nonmatch-ac", run_id=run_id, left_record_id="a", right_record_id="c",
                              blocking_reasons=[], field_evidence=[], score=0.1, pair_decision="NON_MATCH",
                              decision_reason="CONFLICT", triage_status="NON_MATCH_PROPOSAL", triage_reason="CONFLICT"))
        db.commit()
    prefix = f"/api/v1/mdm/runs/{run_id}"
    detail = client.get(f"{prefix}/items/review/case-bc").json()
    assert detail["merge_blocked_reason"]
    moved = client.post(f"{prefix}/review-items/transition", json={"item_ids": ["case-bc"], "decision": "merge"})
    assert moved.status_code == 200, moved.text
    staged_id = moved.json()["changed_ids"][0]
    assert client.get(f"{prefix}/items/merge/{staged_id}").json()["status"] == "REVIEW_STAGED_CONFLICT"
    assert client.get(f"{prefix}/items/review").json()["total"] == 0
    assert client.post(f"{prefix}/export-readiness", json={}).json()["can_export"] is False
    assert client.post(f"{prefix}/exports", json={"acknowledge_unreviewed": True}).status_code == 409
    resolved = client.post(f"{prefix}/merge-items/{staged_id}/resolve-conflict")
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["rejected_pair_count"] == 1
    assert client.post(f"{prefix}/merge-items/{staged_id}/resolve-conflict").status_code == 404
    assert client.get(f"{prefix}/items/merge").json()["total"] == 1
    rows = read_csv(client.post(f"{prefix}/exports", json={}))
    assert sorted(row["source_record_ids"] for row in rows) == ["A|B", "C"]


def test_splitting_deferred_conflict_does_not_duplicate_an_existing_safe_group(client):
    run_id = seed_run(client)
    with client.session_factory() as db:
        records = db.query(ResolutionRecord).filter_by(run_id=run_id).all()
        payload = {row.id: {"id": row.id, "external_id": row.external_id,
                            "source_system": row.source_system, "raw_fields": row.raw_fields} for row in records}
        db.add(ResolutionProposal(id="deferred-abc", run_id=run_id, member_record_ids=["a", "b", "c"],
                                  golden_fields=build_golden(["a", "b", "c"], payload),
                                  status="REVIEW_REQUIRED", origin="AUTO"))
        db.add(ResolutionPair(id="nonmatch-ac", run_id=run_id, left_record_id="a", right_record_id="c",
                              blocking_reasons=[], field_evidence=[], score=0.1, pair_decision="NON_MATCH",
                              decision_reason="CONFLICT", triage_status="NON_MATCH_PROPOSAL", triage_reason="CONFLICT"))
        db.add(ResolutionPair(id="safe-pair-ab", run_id=run_id, left_record_id="a", right_record_id="b",
                              blocking_reasons=[], field_evidence=[], score=0.99, pair_decision="MATCH_CANDIDATE",
                              decision_reason="EXACT", triage_status="HIGH_CONFIDENCE_PROPOSAL", triage_reason="EXACT"))
        db.commit()
    prefix = f"/api/v1/mdm/runs/{run_id}"
    moved = client.post(f"{prefix}/review-items/transition", json={"item_ids": ["deferred-abc"], "decision": "merge"})
    assert moved.status_code == 200, moved.text
    assert client.post(f"{prefix}/merge-items/deferred-abc/resolve-conflict").status_code == 200
    proposals = client.get(f"{prefix}/items/merge").json()["items"]
    assert [item["member_record_ids"] for item in proposals] == [["a", "b"]]


def test_rejecting_link_inside_proposal_retains_safe_component(client):
    run_id = seed_run(client)
    with client.session_factory() as db:
        proposal = db.get(ResolutionProposal, "safe-ab")
        proposal.member_record_ids = ["a", "b", "c"]
        db.add(ResolutionPair(id="safe-pair-ab", run_id=run_id, left_record_id="a", right_record_id="b",
                              blocking_reasons=[], field_evidence=[], score=0.99, pair_decision="MATCH_CANDIDATE",
                              decision_reason="EXACT", triage_status="HIGH_CONFIDENCE_PROPOSAL", triage_reason="EXACT"))
        db.commit()
    prefix = f"/api/v1/mdm/runs/{run_id}"
    response = client.post(f"{prefix}/review-items/transition", json={"item_ids": ["case-bc"], "decision": "no_merge"})
    assert response.status_code == 200, response.text
    remaining = client.get(f"{prefix}/items/merge").json()["items"]
    assert len(remaining) == 1
    assert remaining[0]["member_record_ids"] == ["a", "b"]
    rows = read_csv(client.post(f"{prefix}/exports", json={}))
    assert sorted(row["source_record_ids"] for row in rows) == ["A|B", "C"]
