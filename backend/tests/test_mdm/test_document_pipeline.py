"""Critical contract checks for Linh CSV ingestion and preview execution."""

import io

import pytest
from masterone.mdm.pipeline.blocking import generate_candidates
from masterone.mdm.pipeline.matching import score_pair
from masterone.mdm.pipeline.standardization import standardize
from masterone.mdm.resolution_import import parse_upload
from starlette.datastructures import UploadFile

from tests.test_mdm.test_resolution_items import read_csv, seed_run


@pytest.mark.asyncio
async def test_linh_schema_accepts_seven_thousand_rows_without_label_leakage():
    header = "record_id,entity_id,source,name,phone,email,address,difficulty\n"
    body = "".join(f"CRM_{index},E{index},CRM,Nguyễn Văn An,0912345678,,Hà Nội,hard\n"
                   for index in range(7000))
    parsed = await parse_upload(UploadFile(filename="crm.csv", file=io.BytesIO((header + body).encode())))
    assert len(parsed.rows) == 7000
    assert parsed.rows[0]["raw_fields"] == {
        "name": "Nguyễn Văn An", "phone": "0912345678", "email": "", "address": "Hà Nội"
    }
    assert "entity_id" not in parsed.rows[0]["raw_fields"]


def _record(name: str, phone: str, email: str, address: str) -> dict:
    raw = {"name": name, "phone": phone, "email": email, "address": address}
    normalized, flags = standardize(raw)
    return {"raw_fields": raw, "normalized_fields": normalized, "quality_flags": flags}


def test_different_contact_is_df_while_name_conflict_goes_to_d():
    left = _record("Nguyễn Văn An", "0912345678", "an@example.com", "Hà Nội")
    right = _record("Nguyen Van An", "0987654321", "an@example.com", "Hà Nội")
    result = score_pair(left, right, {"exact_email"})
    states = {item["field"]: item["state"] for item in result["field_evidence"]}
    print(f"DEBUG_STATES: {states}")
    print(f"DEBUG_RESULT: {result}")
    assert states["phone"] == "DF"
    assert result["primary_group"] == "A"
    assert result["decision"] == "REVIEW"

    conflict = _record("Trần Thị Bình", "0912345678", "binh@example.com", "Hà Nội")
    result = score_pair(left, conflict, {"exact_phone"})
    assert result["primary_group"] == "D"
    assert result["decision"] == "REVIEW"


def test_steward_can_approve_group_d_without_a_hard_negative_edge(client):
    run_id = seed_run(client)
    with client.session_factory() as db:
        from masterone.mdm.models import ResolutionPair, ResolutionReviewCase

        pair = db.get(ResolutionPair, "uncertain-bc")
        pair.primary_group = "D"
        pair.subgroup = "D2_NAME_CONFLICT"
        case = db.get(ResolutionReviewCase, "case-bc")
        case.primary_group = "D"
        case.subgroup = "D2_NAME_CONFLICT"
        db.commit()
    prefix = f"/api/v1/mdm/runs/{run_id}"
    moved = client.post(f"{prefix}/review-items/transition",
                        json={"item_ids": ["case-bc"], "decision": "merge"})
    assert moved.status_code == 200, moved.text
    proposal_id = moved.json()["changed_ids"][0]
    choices = {proposal_id: {"phone": "c"}}
    readiness = client.post(f"{prefix}/export-readiness", json={"choices": choices}).json()
    assert readiness["can_export"] is True
    preview = client.post(f"{prefix}/previews", json={"choices": choices})
    assert preview.status_code == 200, preview.text
    assert preview.json()["separate_record_count"] == 0


def test_blocking_is_deterministic_when_input_order_changes():
    records = [{"id": str(index), **_record("Nguyễn Văn An", f"09000000{index:02}", "", "Hà Nội")}
               for index in range(8)]
    assert generate_candidates(records, top_k=2) == generate_candidates(list(reversed(records)), top_k=2)


def test_mixed_review_cluster_is_visible_in_each_matching_category(client, monkeypatch):
    run_id = seed_run(client)
    with client.session_factory() as db:
        from masterone.mdm.models import ResolutionPair, ResolutionReviewCase
        db.add(ResolutionPair(id="review-ab-d", run_id=run_id, left_record_id="a", right_record_id="b",
                              blocking_reasons=[], field_evidence=[], score=0.2,
                              pair_decision="REVIEW", decision_reason="NAME_CONFLICT",
                              triage_status="REVIEW", triage_reason="NAME_CONFLICT",
                              primary_group="D", subgroup="D2_NAME_CONFLICT", signature="N:CF"))
        db.add(ResolutionReviewCase(id="case-ab-d", run_id=run_id, pair_ids=["review-ab-d"],
                                    member_record_ids=["a", "b"], primary_group="D",
                                    subgroup="D2_NAME_CONFLICT", signature_hash="d-signature",
                                    triage_reason="NAME_CONFLICT"))
        db.add(ResolutionPair(id="review-ac-d", run_id=run_id, left_record_id="a", right_record_id="c",
                              blocking_reasons=[], field_evidence=[], score=0.3,
                              pair_decision="REVIEW", decision_reason="NAME_CONFLICT",
                              triage_status="REVIEW", triage_reason="NAME_CONFLICT",
                              primary_group="D", subgroup="D2_NAME_CONFLICT", signature="N:CF:2"))
        db.add(ResolutionReviewCase(id="case-ac-d", run_id=run_id, pair_ids=["review-ac-d"],
                                    member_record_ids=["a", "c"], primary_group="D",
                                    subgroup="D2_NAME_CONFLICT", signature_hash="other-d-signature",
                                    triage_reason="NAME_CONFLICT"))
        db.commit()
    prefix = f"/api/v1/mdm/runs/{run_id}"
    categories = client.get(f"{prefix}/categories").json()
    assert {item["primary_group"] for item in categories} == {"D", "SHARED_VALUE"}
    assert len(categories) == 2
    category = next(item for item in categories if item["primary_group"] == "D")
    assert category["pair_count"] == 2
    assert category["unique_cluster_count"] == 1
    filtered = client.get(f"{prefix}/items/review", params={"primary_group": "D",
                         "subgroup": "D2_NAME_CONFLICT"}).json()
    assert filtered["total"] == 1
    assert filtered["items"][0]["primary_group"] == "D"
    item_id = filtered["items"][0]["id"]
    detail = client.get(f"{prefix}/items/review/{item_id}", params={"primary_group": "D",
                      "subgroup": "D2_NAME_CONFLICT"})
    assert detail.status_code == 200
    assert detail.json()["primary_group"] == "D"
    assert item_id == client.get(f"{prefix}/items/review").json()["items"][0]["id"]

    aggregates = []

    def explain(aggregate, _settings):
        aggregates.append(aggregate)
        return {"explanation": {"summary_vi": "Cần kiểm tra tên", "attention_fields": ["name"],
                                "limitations": []}, "model_version": "test", "usage": {}}

    monkeypatch.setattr("masterone.api.mdm_resolution.explain_cohort", explain)
    response = client.post(f"{prefix}/categories/{category['signature_hash']}/explanation")
    assert response.status_code == 200, response.text
    assert aggregates[0]["pair_count"] == 2
    assert aggregates[0]["unique_record_count"] == 3


def test_preview_detects_changed_pair_and_execute_is_idempotent(client):
    run_id = seed_run(client)
    prefix = f"/api/v1/mdm/runs/{run_id}"
    preview = client.post(f"{prefix}/previews", json={"acknowledge_unreviewed": True})
    assert preview.status_code == 200, preview.text
    preview_id = preview.json()["preview_id"]
    with client.session_factory() as db:
        from masterone.mdm.models import ResolutionPair
        pair = db.get(ResolutionPair, "uncertain-bc")
        pair.revision += 1
        db.commit()
    stale = client.post(f"{prefix}/previews/{preview_id}/execute", json={"execute_request_id": "request-1"})
    assert stale.status_code == 409

    preview = client.post(f"{prefix}/previews", json={"acknowledge_unreviewed": True}).json()
    endpoint = f"{prefix}/previews/{preview['preview_id']}/execute"
    first = client.post(endpoint, json={"execute_request_id": "request-2"})
    assert first.status_code == 200, first.text
    repeated = client.post(endpoint, json={"execute_request_id": "request-2"})
    assert repeated.content == first.content
    assert client.post(endpoint, json={"execute_request_id": "request-3"}).status_code == 409


def test_new_policy_requires_preview_before_export(client):
    run_id = seed_run(client)
    with client.session_factory() as db:
        from masterone.mdm.models import ResolutionRun
        db.get(ResolutionRun, run_id).policy_version = "person-linh-v1"
        db.commit()
    prefix = f"/api/v1/mdm/runs/{run_id}"
    assert client.post(f"{prefix}/exports", json={"acknowledge_unreviewed": True}).status_code == 409
    preview = client.post(f"{prefix}/previews", json={"acknowledge_unreviewed": True})
    assert preview.status_code == 200, preview.text


def test_selective_unmerge_creates_new_snapshot_and_negative_edge(senior_client):
    client = senior_client
    run_id = seed_run(client)
    prefix = f"/api/v1/mdm/runs/{run_id}"
    prepared = client.post(f"{prefix}/previews", json={"acknowledge_unreviewed": True}).json()
    merged = client.post(f"{prefix}/previews/{prepared['preview_id']}/execute",
                         json={"execute_request_id": "initial-export"})
    assert merged.status_code == 200, merged.text
    export_id = merged.headers["x-export-id"]
    events = client.get(f"{prefix}/exports/{export_id}/merges").json()
    assert events["total"] == 1
    assert client.get(f"{prefix}/exports/{export_id}/merges", params={"q": "an"}).json()["total"] == 1
    assert client.get(f"{prefix}/exports/{export_id}/merges", params={"q": "không có"}).json()["total"] == 0
    event_id = events["items"][0]["event_id"]
    split_url = f"{prefix}/exports/{export_id}/merges/{event_id}"
    preview = client.get(f"{split_url}/split-preview", params={"member_id": "a"})
    assert preview.status_code == 200, preview.text
    assert preview.json()["estimated_output_rows"] == 3
    payload = {"member_id": "a", "version": preview.json()["version"],
               "execute_request_id": "split-a"}
    result = client.post(f"{split_url}/split", json=payload)
    assert result.status_code == 200, result.text
    assert result.json()["parent_export_id"] == export_id
    assert client.post(f"{split_url}/split", json=payload).json() == result.json()
    assert client.get(f"{split_url}/split-preview", params={"member_id": "b"}).status_code == 409
    with client.session_factory() as db:
        from masterone.mdm.models import ResolutionPair, ResolutionSplitEvent
        assert db.query(ResolutionSplitEvent).count() == 1
        assert db.query(ResolutionPair).filter_by(run_id=run_id, pair_decision="NOT_MERGEABLE").count() == 1
        negative_id = db.query(ResolutionPair).filter_by(run_id=run_id, pair_decision="NOT_MERGEABLE").one().id
    detail = client.get(f"{prefix}/items/no_merge/{negative_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["field_evidence"]


def test_selective_unmerge_preserves_other_members_and_selected_value(senior_client):
    client = senior_client
    run_id = seed_run(client)
    with client.session_factory() as db:
        from masterone.mdm.models import ResolutionProposal, ResolutionRecord, ResolutionRun
        from masterone.mdm.pipeline.golden import build_golden
        db.add(ResolutionRecord(id="d", run_id=run_id, file_id="file-items", external_id="D",
                                source_system="ERP", raw_fields={"name": "An", "phone": "111",
                                "email": "", "address": ""}, normalized_fields={}))
        proposal = db.get(ResolutionProposal, "safe-ab")
        proposal.member_record_ids = ["a", "b", "c", "d"]
        db.flush()
        records = db.query(ResolutionRecord).filter_by(run_id=run_id).all()
        proposal.golden_fields = build_golden(["a", "b", "c", "d"], {
            row.id: {"id": row.id, "source_system": row.source_system, "external_id": row.external_id,
                     "raw_fields": row.raw_fields} for row in records})
        db.get(ResolutionRun, run_id).record_count = 4
        db.commit()
    prefix = f"/api/v1/mdm/runs/{run_id}"
    preview = client.post(f"{prefix}/previews", json={"acknowledge_unreviewed": True}).json()
    response = client.post(f"{prefix}/previews/{preview['preview_id']}/execute",
                           json={"execute_request_id": "four-records"})
    assert response.status_code == 200, response.text
    export_id = response.headers["x-export-id"]
    event_id = client.get(f"{prefix}/exports/{export_id}/merges").json()["items"][0]["event_id"]
    split_url = f"{prefix}/exports/{export_id}/merges/{event_id}"
    prepared = client.get(f"{split_url}/split-preview", params={"member_id": "c"}).json()
    result = client.post(f"{split_url}/split", json={"member_id": "c", "version": prepared["version"],
                                                    "execute_request_id": "remove-c"})
    assert result.status_code == 200, result.text
    csv_response = client.get(f"{prefix}/exports/{result.json()['export_id']}/download")
    rows = read_csv(csv_response)
    assert sorted(row["source_record_ids"] for row in rows) == ["A|B|D", "C"]
    assert next(row for row in rows if row["source_record_ids"] == "A|B|D")["phone_number"] == "111"
