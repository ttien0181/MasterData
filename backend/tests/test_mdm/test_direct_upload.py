"""Direct browser → R2 part upload: presign, PUT, commit, and the orphan sweep."""

import base64
import hashlib
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from masterone.config import get_settings
from masterone.db.base import now_utc
from masterone.mdm.upload_sessions import sweep_orphan_parts
from masterone.storage.object_store import R2ObjectStore

PAYLOAD = (b"record_id,source,name,phone,email,address\n"
           b"ONE,CRM,Ada,0901234567,ada@example.test,Ha Noi\n")


def _b64(data: bytes) -> str:
    return base64.b64encode(hashlib.sha256(data).digest()).decode("ascii")


class FakeR2:
    """Mimics the parts R2 enforces: a PUT is stored only when its body matches the signed checksum."""

    def __init__(self):
        self.objects: dict[str, tuple[bytes, object]] = {}
        self.targets: dict[str, str] = {}

    def presigned_put_url(self, key, content_type, sha256_base64, expires_in):
        url = f"https://r2.test/{key}?expires={expires_in}"
        self.targets[url] = key
        return url

    def receive_put(self, target, body: bytes) -> int:
        key = self.targets[target["upload_url"]]
        if target["headers"]["x-amz-checksum-sha256"] != _b64(body):
            return 400
        self.objects[key] = (body, now_utc())
        return 200

    def head_part(self, key):
        if key not in self.objects:
            return None
        data, _ = self.objects[key]
        return {"byte_size": len(data), "sha256_base64": _b64(data)}

    def list_objects(self, prefix):
        return [(key, modified) for key, (_, modified) in self.objects.items() if key.startswith(prefix)]

    def upload_stream(self, stream, key, content_type):
        self.objects[key] = (stream.read(), now_utc())

    def download_bytes(self, key):
        return self.objects[key][0]

    def delete(self, key):
        self.objects.pop(key, None)

    def exists(self, key):
        return key in self.objects


@pytest.fixture
def r2(client, monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), "upload_staging_dir", str(tmp_path / "staging"))
    fake = FakeR2()
    monkeypatch.setattr("masterone.mdm.upload_sessions.get_store", lambda _settings: fake)
    monkeypatch.setattr("masterone.mdm.resolution_import.get_store", lambda _settings: fake)
    return fake


def _draft(client, payload=PAYLOAD, filename="people.csv"):
    response = client.post("/api/v1/mdm/upload-sessions", json={
        "name": "", "files": [{"filename": filename, "size": len(payload)}]})
    assert response.status_code == 201, response.text
    return response.json()


def _presign(client, draft, payload=PAYLOAD, part_number=0):
    file_id = draft["files"][0]["file_id"]
    return client.post(
        f"/api/v1/mdm/upload-sessions/{draft['session_id']}/files/{file_id}/presign",
        json={"parts": [{"part_number": part_number, "byte_size": len(payload),
                         "sha256": hashlib.sha256(payload).hexdigest()}]})


def _commit(client, draft, payload=PAYLOAD, part_number=0):
    file_id = draft["files"][0]["file_id"]
    return client.post(
        f"/api/v1/mdm/upload-sessions/{draft['session_id']}/files/{file_id}/parts/{part_number}/commit",
        json={"byte_size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()})


def test_direct_upload_presign_put_commit_validates_and_commits(client, r2):
    draft = _draft(client)
    assert draft["upload_mode"] == "direct"

    presigned = _presign(client, draft)
    assert presigned.status_code == 200, presigned.text
    target = presigned.json()["parts"][0]
    assert target["headers"]["x-amz-checksum-sha256"] == _b64(PAYLOAD)
    assert r2.receive_put(target, PAYLOAD) == 200

    committed = _commit(client, draft)
    assert committed.status_code == 200, committed.text
    assert committed.json()["received_size"] == len(PAYLOAD)

    prefix = f"/api/v1/mdm/upload-sessions/{draft['session_id']}"
    checked = client.post(f"{prefix}/validate")
    assert checked.json()["status"] == "READY", checked.text
    run = client.post(f"{prefix}/commit")
    assert run.status_code == 201, run.text
    assert run.json()["profile"]["row_count"] == 1


def test_commit_refused_when_part_never_reached_storage(client, r2):
    draft = _draft(client)
    assert _presign(client, draft).status_code == 200
    response = _commit(client, draft)
    assert response.status_code == 409, response.text
    with client.session_factory() as db:
        from masterone.mdm.models import ResolutionUploadPart
        assert db.query(ResolutionUploadPart).count() == 0


def test_commit_refused_when_stored_bytes_differ_from_claimed_checksum(client, r2):
    draft = _draft(client)
    target = _presign(client, draft).json()["parts"][0]
    # Bypass the signed-checksum check to simulate an object that does not match what the client claims.
    r2.objects[target["upload_url"].split("?")[0].removeprefix("https://r2.test/")] = (
        PAYLOAD + b"tampered", now_utc())
    response = _commit(client, draft)
    assert response.status_code == 422, response.text


def test_presign_rejects_part_with_wrong_size(client, r2):
    draft = _draft(client)
    file_id = draft["files"][0]["file_id"]
    response = client.post(
        f"/api/v1/mdm/upload-sessions/{draft['session_id']}/files/{file_id}/presign",
        json={"parts": [{"part_number": 0, "byte_size": len(PAYLOAD) - 1,
                         "sha256": hashlib.sha256(PAYLOAD).hexdigest()}]})
    assert response.status_code == 422, response.text


def test_presign_rejects_malformed_checksum(client, r2):
    draft = _draft(client)
    file_id = draft["files"][0]["file_id"]
    response = client.post(
        f"/api/v1/mdm/upload-sessions/{draft['session_id']}/files/{file_id}/presign",
        json={"parts": [{"part_number": 0, "byte_size": len(PAYLOAD), "sha256": "not-a-digest"}]})
    assert response.status_code == 422, response.text


def test_presign_refused_for_local_staging(client, monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), "upload_staging_dir", str(tmp_path / "staging"))
    monkeypatch.setattr("masterone.mdm.upload_sessions.get_store", lambda _settings: None)
    draft = _draft(client)
    assert draft["upload_mode"] == "proxy"
    response = _presign(client, draft)
    assert response.status_code == 409, response.text


def test_commit_is_idempotent(client, r2):
    draft = _draft(client)
    target = _presign(client, draft).json()["parts"][0]
    r2.receive_put(target, PAYLOAD)
    first = _commit(client, draft)
    second = _commit(client, draft)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert second.json()["received_size"] == len(PAYLOAD)


def test_sweep_deletes_only_old_unreferenced_objects(client, r2):
    draft = _draft(client)
    target = _presign(client, draft).json()["parts"][0]
    r2.receive_put(target, PAYLOAD)
    assert _commit(client, draft).status_code == 200

    old = now_utc() - timedelta(days=2)
    r2.objects["upload-staging/gone/gone/0.part"] = (b"abandoned", old)
    r2.objects["upload-staging/fresh/fresh/0.part"] = (b"in flight", now_utc())
    with client.session_factory() as db:
        assert sweep_orphan_parts(db, r2, dry_run=True) == 1
        assert "upload-staging/gone/gone/0.part" in r2.objects
        deleted = sweep_orphan_parts(db, r2)

    assert deleted == 1
    assert "upload-staging/gone/gone/0.part" not in r2.objects
    assert "upload-staging/fresh/fresh/0.part" in r2.objects
    assert target["upload_url"].split("?")[0].removeprefix("https://r2.test/") in r2.objects


def test_presigned_put_signs_checksum_header():
    store = R2ObjectStore("https://account.r2.example.test", "access", "secret", "bucket")
    url = store.presigned_put_url("upload-staging/s/f/0.part", "application/octet-stream",
                                  _b64(PAYLOAD), 900)
    query = parse_qs(urlparse(url).query)
    assert set(query["X-Amz-SignedHeaders"][0].split(";")) >= {"content-type", "host", "x-amz-checksum-sha256"}
    assert query["X-Amz-Expires"] == ["900"]


def test_head_part_reports_size_and_checksum_or_none():
    from botocore.stub import Stubber

    store = R2ObjectStore("https://r2.example.test", "access", "secret", "test-bucket")
    with Stubber(store.client) as stubber:
        stubber.add_response("head_object", {"ContentLength": len(PAYLOAD), "ChecksumSHA256": _b64(PAYLOAD)},
                             {"Bucket": "test-bucket", "Key": "k", "ChecksumMode": "ENABLED"})
        assert store.head_part("k") == {"byte_size": len(PAYLOAD), "sha256_base64": _b64(PAYLOAD)}
        stubber.add_client_error("head_object", service_error_code="NotFound", http_status_code=404)
        assert store.head_part("k") is None
        stubber.assert_no_pending_responses()


def test_received_size_totals_stored_parts_whatever_the_commit_order(client, r2):
    from masterone.mdm.upload_sessions import UPLOAD_CHUNK_BYTES

    payload = b"a" * (UPLOAD_CHUNK_BYTES + 5)
    draft = _draft(client, payload=payload, filename="big.csv")
    file_id = draft["files"][0]["file_id"]
    prefix = f"/api/v1/mdm/upload-sessions/{draft['session_id']}"
    specs = [
        {"part_number": 0, "byte_size": UPLOAD_CHUNK_BYTES, "sha256": hashlib.sha256(payload[:UPLOAD_CHUNK_BYTES]).hexdigest()},
        {"part_number": 1, "byte_size": 5, "sha256": hashlib.sha256(payload[UPLOAD_CHUNK_BYTES:]).hexdigest()},
    ]
    presigned = client.post(f"{prefix}/files/{file_id}/presign", json={"parts": specs}).json()["parts"]
    for target, body in zip(presigned, [payload[:UPLOAD_CHUNK_BYTES], payload[UPLOAD_CHUNK_BYTES:]], strict=True):
        assert r2.receive_put(target, body) == 200

    last = client.post(f"{prefix}/files/{file_id}/parts/1/commit",
                       json={"byte_size": 5, "sha256": specs[1]["sha256"]})
    assert last.status_code == 200, last.text
    first = client.post(f"{prefix}/files/{file_id}/parts/0/commit",
                        json={"byte_size": UPLOAD_CHUNK_BYTES, "sha256": specs[0]["sha256"]})
    assert first.status_code == 200, first.text
    assert first.json()["received_size"] == len(payload)
    assert client.get(prefix).json()["files"][0]["received_size"] == len(payload)
