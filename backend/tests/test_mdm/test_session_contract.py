"""CP01 keeps run identifiers, persisted data and existing response keys intact."""

from masterone.mdm.models import ResolutionRun, ResolutionTask
from masterone.mdm.resolution_service import run_pipeline


def test_upload_start_overview_preview_and_download_keep_the_existing_run(client):
    csv = b"record_id,source,name,phone,email,address\nONE,CRM,An,0901234567,an@example.test,Ha Noi\n"
    created = client.post("/api/v1/mdm/runs", files=[("files", ("one.csv", csv, "text/csv"))])
    assert created.status_code == 201, created.text
    run_id = created.json()["run_id"]
    prefix = f"/api/v1/mdm/runs/{run_id}"
    overview = client.get(f"{prefix}/overview").json()
    assert overview["status"] == "UPLOADED"
    assert overview["task"] is None
    assert overview["record_count"] == 1
    first = client.post(f"{prefix}/start")
    assert first.status_code == 202
    assert client.post(f"{prefix}/start").json()["task_id"] == first.json()["task_id"]
    with client.session_factory() as db:
        assert db.query(ResolutionRun).count() == 1
        assert db.query(ResolutionTask).count() == 1
    run_pipeline(run_id, client.session_factory, lambda *args: None)
    overview = client.get(f"{prefix}/overview").json()
    assert overview["status"] == overview["task"]["status"] == "COMPLETED"
    assert overview["task"]["run_id"] == run_id
    assert overview["task"]["task_id"] == first.json()["task_id"]
    assert overview["counts"]["separate"] == 1
    assert overview["source_counts"] == {"CRM": 1}
    assert client.post(f"{prefix}/start").status_code == 409
    prepared = client.post(f"{prefix}/previews", json={})
    assert prepared.status_code == 200, prepared.text
    exported = client.post(f"{prefix}/previews/{prepared.json()['preview_id']}/execute",
                           json={"execute_request_id": "cp01-export"})
    assert exported.status_code == 200, exported.text
    export_id = exported.headers["x-export-id"]
    assert client.get(f"{prefix}/exports/{export_id}/download").content == exported.content
    assert client.get(f"{prefix}/overview").json()["history_count"] == 1
    actions = {event["action"] for event in client.get(f"{prefix}/audit?limit=100").json()["items"]}
    assert {"SESSION_CREATED", "RUN_STARTED", "PREVIEW_EXECUTED", "CSV_EXPORTED"} <= actions


def test_old_run_without_task_is_readable_and_failure_metadata_is_restorable(client):
    owner_id = client.get("/api/v1/auth/me").json()["id"]
    with client.session_factory.begin() as db:
        db.add(ResolutionRun(id="historical", owner_user_id=owner_id, status="COMPLETED"))
        db.add(ResolutionRun(id="interrupted", owner_user_id=owner_id, status="RETRYABLE_FAILED"))
        db.add(ResolutionTask(id="task", run_id="interrupted", status="RETRYABLE_FAILED",
                             phase="interrupted", error_code="WORKER_RESTARTED"))
    historical = client.get("/api/v1/mdm/runs/historical/overview")
    assert historical.status_code == 200
    assert historical.json()["task"] is None
    assert historical.json()["status"] == "COMPLETED"
    failed = client.get("/api/v1/mdm/runs/interrupted/overview").json()
    assert failed["task"]["error_code"] == "WORKER_RESTARTED"
    assert client.get("/api/v1/mdm/runs/tasks/task").json() == failed["task"]
    first_page = client.get("/api/v1/mdm/runs?limit=1&offset=0").json()
    second_page = client.get("/api/v1/mdm/runs?limit=1&offset=1").json()
    assert first_page["total"] == second_page["total"] == 2
    assert first_page["items"][0]["run_id"] != second_page["items"][0]["run_id"]


def test_decision_contract_rejects_unrecognized_actions(client):
    response = client.post("/api/v1/mdm/runs/anything/review-items/transition",
                           json={"item_ids": ["item"], "decision": "approve_and_export"})
    assert response.status_code == 422
