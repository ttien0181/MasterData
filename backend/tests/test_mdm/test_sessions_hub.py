from masterone.auth.models import User
from masterone.mdm.models import ResolutionRun


def _create_run(client, filename):
    payload = (f"record_id,source,name,phone,email,address\n"
               f"{filename},CRM,An,0901234567,an@example.test,Ha Noi\n").encode()
    response = client.post("/api/v1/mdm/runs", files=[("files", (filename, payload, "text/csv"))])
    assert response.status_code == 201, response.text
    return response.json()["run_id"]


def test_sessions_hub_cursor_search_rename_archive_and_restore(client):
    first_id = _create_run(client, "one.csv")
    second_id = _create_run(client, "two.csv")
    third_id = _create_run(client, "three.csv")

    renamed = client.patch(f"/api/v1/mdm/runs/{first_id}", json={"name": "October contacts"})
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "October contacts"
    matches = client.get("/api/v1/mdm/runs?q=October%20contacts").json()
    assert [item["run_id"] for item in matches["items"]] == [first_id]

    first_page = client.get("/api/v1/mdm/runs?limit=1&archived=all").json()
    assert first_page["total"] == 3
    second_page = client.get(f"/api/v1/mdm/runs?limit=1&archived=all&cursor={first_page['next_cursor']}").json()
    assert second_page["items"][0]["run_id"] != first_page["items"][0]["run_id"]

    archived = client.post("/api/v1/mdm/runs/bulk", json={"operation": "archive", "run_ids": [first_id]})
    assert archived.status_code == 200
    assert client.get("/api/v1/mdm/runs?q=October%20contacts&archived=active").json()["items"] == []
    archived_item = client.get("/api/v1/mdm/runs?q=October%20contacts&archived=archived").json()["items"][0]
    assert archived_item["archived_at"]

    restored = client.post("/api/v1/mdm/runs/bulk", json={"operation": "restore", "run_ids": [first_id]})
    assert restored.status_code == 200
    assert client.get("/api/v1/mdm/runs?q=October%20contacts&archived=active").json()["items"][0]["run_id"] == first_id
    actions = {event["action"] for event in client.get(
        f"/api/v1/mdm/runs/{first_id}/audit?limit=100").json()["items"]}
    assert {"SESSION_CREATED", "SESSION_RENAMED", "SESSION_ARCHIVED", "SESSION_RESTORED"} <= actions
    assert second_id != third_id


def test_viewer_can_read_hub_but_cannot_rename_or_archive(client):
    run_id = _create_run(client, "viewer-visible.csv")
    with client.session_factory.begin() as db:
        user = db.query(User).filter_by(email_normalized="steward@example.com").one()
        user.role = "VIEWER"

    listing = client.get("/api/v1/mdm/runs")
    assert listing.status_code == 200
    assert [item["run_id"] for item in listing.json()["items"]] == [run_id]
    assert client.patch(f"/api/v1/mdm/runs/{run_id}", json={"name": "blocked"}).status_code == 403
    assert client.post("/api/v1/mdm/runs/bulk", json={"operation": "archive", "run_ids": [run_id]}).status_code == 403


def test_run_list_can_supply_owner_scoped_active_status_and_phase(client):
    run_id = _create_run(client, "active.csv")
    with client.session_factory.begin() as db:
        run = db.get(ResolutionRun, run_id)
        run.status = "RUNNING"
        run.phase = "blocking"

    active = client.get("/api/v1/mdm/runs?run_status=QUEUED,RUNNING&limit=5").json()
    assert active["total"] == 1
    assert active["items"][0]["run_id"] == run_id
    assert active["items"][0]["status"] == "RUNNING"
    assert active["items"][0]["phase"] == "blocking"
    assert client.get("/api/v1/mdm/runs?run_status=UPLOADED").json()["total"] == 0
