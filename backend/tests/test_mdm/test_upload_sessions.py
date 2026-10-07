import csv
import hashlib
from io import StringIO

import pytest
from masterone.config import get_settings
from masterone.mdm.models import ResolutionExport, ResolutionRun
from masterone.mdm.resolution_service import run_pipeline
from masterone.mdm.upload_sessions import UPLOAD_CHUNK_BYTES
from sqlalchemy import event
from sqlalchemy.exc import OperationalError


@pytest.mark.parametrize("failure_table", ["mdm_resolution_runs", "mdm_resolution_records"])
def test_database_full_keeps_upload_ready_and_can_retry(client, monkeypatch, tmp_path, failure_table):
    monkeypatch.setattr(get_settings(), "upload_staging_dir", str(tmp_path / "staging"))
    payload = (b"record_id,source,name,phone,email,address\n"
               b"ONE,CRM,Ada,0901234567,ada@example.test,Ha Noi\n")
    draft = _create_draft(client, "people.csv", payload)
    _upload_payload(client, draft, payload)
    prefix = f"/api/v1/mdm/upload-sessions/{draft['session_id']}"
    assert client.post(f"{prefix}/validate").json()["status"] == "READY"

    class DiskFullError(Exception):
        sqlstate = "53100"

    def fail_insert(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.startswith(f"INSERT INTO {failure_table} "):
            raise OperationalError(statement, None, DiskFullError("project size limit exceeded"))

    with client.session_factory() as db:
        engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", fail_insert)
    try:
        failed = client.post(f"{prefix}/commit")
    finally:
        event.remove(engine, "before_cursor_execute", fail_insert)
    assert failed.status_code == 507, failed.text
    assert "không cần upload lại" in failed.json()["detail"]
    restored = client.get(prefix).json()
    assert restored["status"] == "READY"
    assert restored["files"][0]["received_size"] == len(payload)
    with client.session_factory() as db:
        assert db.query(ResolutionRun).filter_by(upload_session_id=draft["session_id"]).count() == 0
    retried = client.post(f"{prefix}/commit")
    assert retried.status_code == 201, retried.text
    assert retried.json()["profile"]["row_count"] == 1
    assert client.post(f"{prefix}/commit").json()["run_id"] == retried.json()["run_id"]


def test_commit_with_r2_source_upload_keeps_stream_open(client, monkeypatch, tmp_path):
    """Use the real S3 transfer implementation: it closes its small-file body."""
    from botocore.stub import Stubber
    from masterone.mdm.models import ResolutionFile
    from masterone.storage.object_store import R2ObjectStore

    monkeypatch.setattr(get_settings(), "upload_staging_dir", str(tmp_path / "staging"))
    payload = (b"record_id,source,name,phone,email,address\n"
               b"ONE,CRM,Ada,0901234567,ada@example.test,Ha Noi\n")
    draft = _create_draft(client, "people.csv", payload)
    _upload_payload(client, draft, payload)
    prefix = f"/api/v1/mdm/upload-sessions/{draft['session_id']}"
    assert client.post(f"{prefix}/validate").json()["status"] == "READY"

    store = R2ObjectStore("https://r2.example.test", "test-access", "test-secret", "test-bucket")
    monkeypatch.setattr("masterone.mdm.resolution_import.get_store", lambda _: store)
    with Stubber(store.client) as stubber:
        stubber.add_response("put_object", {"ETag": '"test-etag"'})
        committed = client.post(f"{prefix}/commit")
        assert committed.status_code == 201, committed.text
        assert committed.json()["profile"]["row_count"] == 1
        run_id = committed.json()["run_id"]
        assert client.get(prefix).json()["status"] == "COMMITTED"
        assert client.post(f"{prefix}/commit").json()["run_id"] == run_id
        with client.session_factory() as db:
            saved_file = db.query(ResolutionFile).filter_by(run_id=run_id).one()
            assert saved_file.r2_key.endswith("/people.csv")
        stubber.assert_no_pending_responses()


def _create_draft(client, filename, payload, name=""):
    response = client.post("/api/v1/mdm/upload-sessions", json={
        "name": name,
        "files": [{"filename": filename, "size": len(payload)}],
    })
    assert response.status_code == 201, response.text
    return response.json()


def _upload_payload(client, draft, payload):
    file = draft["files"][0]
    target = file.get("replacement") or file
    response = client.put(
        f"/api/v1/mdm/upload-sessions/{draft['session_id']}/files/{target['file_id']}/parts/0",
        content=payload,
        headers={"Content-Type": "application/octet-stream",
                 "X-Chunk-SHA256": hashlib.sha256(payload).hexdigest()},
    )
    assert response.status_code == 200, response.text
    return file


def test_chunked_draft_resumes_validates_commits_and_is_idempotent(client, monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), "upload_staging_dir", str(tmp_path / "staging"))
    payload = (b"record_id,source,name,phone,email,address\n"
               b"ONE,CRM,Ada,0901234567,ada@example.test,Ha Noi\n")
    draft = _create_draft(client, "people.csv", payload, "Import tuần này")
    file = _upload_payload(client, draft, payload)

    # Re-sending a part replaces its bytes without inflating the received count.
    _upload_payload(client, draft, payload)
    current = client.get(f"/api/v1/mdm/upload-sessions/{draft['session_id']}").json()
    assert current["files"][0]["received_size"] == len(payload)
    assert current["files"][0]["parts"][0]["sha256"] == hashlib.sha256(payload).hexdigest()

    checked = client.post(f"/api/v1/mdm/upload-sessions/{draft['session_id']}/validate")
    assert checked.status_code == 200, checked.text
    assert checked.json()["status"] == "READY"
    assert checked.json()["files"][0]["row_count"] == 1

    committed = client.post(f"/api/v1/mdm/upload-sessions/{draft['session_id']}/commit")
    assert committed.status_code == 201, committed.text
    assert committed.json()["profile"]["row_count"] == 1
    assert client.post(f"/api/v1/mdm/upload-sessions/{draft['session_id']}/commit").json()["run_id"] == committed.json()["run_id"]
    with client.session_factory() as db:
        run = db.get(ResolutionRun, committed.json()["run_id"])
        assert run.name == "Import tuần này"
        assert run.record_count == 1
        assert run.upload_session_id == draft["session_id"]
    assert file["file_id"]


def test_three_csv_files_are_processed_and_exported_as_one_run(client, monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), "upload_staging_dir", str(tmp_path / "staging"))
    files = [
        ("crm.csv", b"record_id,source,name,phone,email,address\nCRM-1,CRM,Ada One,0901000001,ada@example.test,Hanoi\n"),
        ("erp.csv", b"record_id,source,name,phone,email,address\nERP-1,ERP,Binh Two,0901000002,binh@example.test,Danang\n"),
        ("billing.csv", b"record_id,source,name,phone,email,address\nBILL-1,BILL,Chi Three,0901000003,chi@example.test,Hue\n"),
    ]
    created = client.post("/api/v1/mdm/upload-sessions", json={
        "name": "Ba nguồn dữ liệu",
        "files": [{"filename": name, "size": len(payload)} for name, payload in files],
    })
    assert created.status_code == 201, created.text
    draft = created.json()
    for target, (_, payload) in zip(draft["files"], files, strict=True):
        response = client.put(
            f"/api/v1/mdm/upload-sessions/{draft['session_id']}/files/{target['file_id']}/parts/0",
            content=payload,
            headers={"Content-Type": "application/octet-stream",
                     "X-Chunk-SHA256": hashlib.sha256(payload).hexdigest()},
        )
        assert response.status_code == 200, response.text
    validated = client.post(f"/api/v1/mdm/upload-sessions/{draft['session_id']}/validate")
    assert validated.status_code == 200, validated.text
    assert validated.json()["status"] == "READY"
    committed = client.post(f"/api/v1/mdm/upload-sessions/{draft['session_id']}/commit")
    assert committed.status_code == 201, committed.text
    run_id = committed.json()["run_id"]
    assert committed.json()["profile"]["file_count"] == 3
    assert committed.json()["profile"]["row_count"] == 3
    assert committed.json()["profile"]["source_counts"] == {"BILL": 1, "CRM": 1, "ERP": 1}

    prefix = f"/api/v1/mdm/runs/{run_id}"
    assert client.post(f"{prefix}/start").status_code == 202
    run_pipeline(run_id, client.session_factory, lambda *args: None)
    overview = client.get(f"{prefix}/overview").json()
    assert overview["record_count"] == 3
    assert overview["file_count"] == 3
    assert overview["counts"]["separate"] == 3
    assert overview["source_counts"] == {"BILL": 1, "CRM": 1, "ERP": 1}

    preview = client.post(f"{prefix}/previews", json={})
    assert preview.status_code == 200, preview.text
    stored_objects = {}

    class FakeR2:
        def upload_bytes(self, data, key, _content_type):
            stored_objects[key] = data

        def download_bytes(self, key):
            return stored_objects[key]

        def download_file(self, key, path):
            with open(path, "wb") as stream:
                stream.write(stored_objects[key])

    monkeypatch.setattr("masterone.mdm.resolution_items.get_store", lambda _settings: FakeR2())
    monkeypatch.setattr("masterone.api.mdm_resolution.get_store", lambda _settings: FakeR2())
    exported = client.post(f"{prefix}/previews/{preview.json()['preview_id']}/execute",
                           json={"execute_request_id": "three-file-export"})
    assert exported.status_code == 200, exported.text
    export_id = exported.headers["x-export-id"]
    with client.session_factory() as db:
        snapshot = db.get(ResolutionExport, export_id)
        assert snapshot.csv_bytes is None
        assert snapshot.r2_key in stored_objects
        assert stored_objects[snapshot.r2_key] == exported.content
    rows = list(csv.DictReader(StringIO(exported.content.decode("utf-8-sig"))))
    assert len(rows) == 3
    assert {row["source_record_ids"] for row in rows} == {"BILL-1", "CRM-1", "ERP-1"}
    assert {row["source_systems"] for row in rows} == {"BILL", "CRM", "ERP"}
    comparison = client.get(f"{prefix}/exports/{export_id}/compare")
    assert comparison.status_code == 200, comparison.text
    assert comparison.json()["source_count"] == comparison.json()["output_count"] == 3


def test_upload_has_no_five_mib_file_cap(client, monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), "upload_staging_dir", str(tmp_path / "staging"))
    large_value = b"x" * (5 * 1024 * 1024 + 1)
    payload = (b"record_id,source,name,phone,email,address,notes\n"
               b"ONE,CRM,Ada,0901234567,ada@example.test,Ha Noi," + large_value + b"\n")
    assert len(payload) > 5 * 1024 * 1024
    draft = _create_draft(client, "large.csv", payload)
    assert len(payload) <= UPLOAD_CHUNK_BYTES
    _upload_payload(client, draft, payload)

    checked = client.post(f"/api/v1/mdm/upload-sessions/{draft['session_id']}/validate")
    assert checked.status_code == 200, checked.text
    assert checked.json()["status"] == "READY"
    assert checked.json()["files"][0]["row_count"] == 1

    deleted = client.delete(f"/api/v1/mdm/upload-sessions/{draft['session_id']}")
    assert deleted.status_code == 204, deleted.text


def test_replacement_keeps_original_until_new_file_validates(client, monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), "upload_staging_dir", str(tmp_path / "staging"))
    original = (b"record_id,source,name,phone,email,address\n"
                b"OLD,CRM,Old person,0901234567,old@example.test,Ha Noi\n")
    replacement = (b"record_id,source,name,phone,email,address\n"
                   b"NEW,CRM,New person,0901234567,new@example.test,Ha Noi\n")
    draft = _create_draft(client, "old.csv", original)
    _upload_payload(client, draft, original)
    assert client.post(f"/api/v1/mdm/upload-sessions/{draft['session_id']}/validate").json()["status"] == "READY"

    original_file = draft["files"][0]
    changed = client.put(
        f"/api/v1/mdm/upload-sessions/{draft['session_id']}/files/{original_file['file_id']}/replace",
        json={"filename": "new.csv", "size": len(replacement)},
    )
    assert changed.status_code == 200, changed.text
    draft = changed.json()
    assert draft["files"][0]["filename"] == "old.csv"
    assert draft["files"][0]["received_size"] == len(original)
    assert draft["files"][0]["replacement"]["filename"] == "new.csv"

    incomplete = client.post(f"/api/v1/mdm/upload-sessions/{draft['session_id']}/validate").json()
    assert incomplete["status"] == "ERROR"
    assert incomplete["files"][0]["filename"] == "old.csv"
    assert incomplete["files"][0]["replacement"]["filename"] == "new.csv"

    _upload_payload(client, draft, replacement)
    checked = client.post(f"/api/v1/mdm/upload-sessions/{draft['session_id']}/validate")
    assert checked.status_code == 200, checked.text
    assert checked.json()["status"] == "READY"
    assert checked.json()["files"][0]["filename"] == "new.csv"
    assert checked.json()["files"][0]["replacement"] is None
    assert client.delete(f"/api/v1/mdm/upload-sessions/{draft['session_id']}").status_code == 204


def test_direct_stream_import_has_no_ten_thousand_row_cap(client):
    header = b"record_id,source,name,phone,email,address\n"
    rows = b"".join(
        f"ROW-{index},CRM,Person {index},0901234567,p{index}@example.test,Ha Noi\n".encode()
        for index in range(10_001)
    )
    created = client.post("/api/v1/mdm/runs", files=[("files", ("many.csv", header + rows, "text/csv"))])
    assert created.status_code == 201, created.text
    assert created.json()["profile"]["row_count"] == 10_001
