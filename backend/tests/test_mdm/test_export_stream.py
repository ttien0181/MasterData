"""Streaming export contracts and fault injection, using isolated local objects."""

import csv
import gc
import hashlib
import json
import os
import subprocess
import sys
import tracemalloc
from io import BytesIO, StringIO
from pathlib import Path

import pytest
from fastapi import HTTPException
from masterone.auth.models import User
from masterone.config import get_settings
from masterone.db.base import Base
from masterone.mdm.export_stream import COLUMNS, create_export, rss_peak
from masterone.mdm.models import (
    ResolutionAuditEvent,
    ResolutionExport,
    ResolutionFile,
    ResolutionPreview,
    ResolutionProposal,
    ResolutionRecord,
    ResolutionRun,
)
from masterone.storage.object_store import LocalObjectStore
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from tests.test_mdm.test_resolution_items import seed_run


@pytest.fixture
def prepared(client, monkeypatch, tmp_path):
    settings = get_settings()
    monkeypatch.setattr(settings, "export_artifact_dir", str(tmp_path / "objects"))
    monkeypatch.setattr(settings, "export_batch_rows", 1)
    monkeypatch.setattr(settings, "export_parquet_enabled", False)
    run_id = seed_run(client)
    with client.session_factory() as db:
        actor = db.query(User).one().id
    return client, run_id, actor


def artifact_bytes(snapshot):
    return LocalObjectStore(get_settings().export_artifact_dir).download_bytes(snapshot.r2_key.removeprefix("local:"))


@pytest.mark.parametrize("batch", [1, 2, 17])
def test_batches_schema_coverage_and_resources(prepared, monkeypatch, batch):
    client, run_id, actor = prepared
    monkeypatch.setattr(get_settings(), "export_batch_rows", batch)
    with client.session_factory() as db:
        snapshot = create_export(db, run_id, actor, True)
        content = artifact_bytes(snapshot)
        assert content.startswith(b"\xef\xbb\xbf")
        reader = csv.DictReader(StringIO(content.decode("utf-8-sig")))
        rows = list(reader)
        assert tuple(reader.fieldnames) == COLUMNS
        assert [row["source_record_ids"] for row in rows] == ["A|B", "C"]
        assert rows[1]["review_pending"] == "true"
        assert snapshot.row_count == 2 and snapshot.csv_bytes is None
        receipt = db.query(ResolutionAuditEvent).filter_by(action="CSV_EXPORTED").one().details
        assert receipt["coverage"] == "PASS" and receipt["source_record_count"] == 3
        assert receipt["artifacts"][".csv"]["sha256"] == hashlib.sha256(content).hexdigest()
        metrics = receipt["resources"]
        assert metrics["peak_rss_bytes"] > 0 and metrics["spill_peak_bytes"] > 0
        assert metrics["disk_peak_bytes"] >= len(content)
        assert metrics["queue_depth"] == metrics["backpressure_wait_seconds"] == 0
        assert metrics["upload_duration_seconds"] >= 0
        assert set(metrics["stage_duration_seconds"]) == {"readiness", "write_validate", "upload_verify", "total"}
    assert not list(Path(get_settings().export_artifact_dir).glob("build-*"))


def test_overrides_win_over_selected_source(prepared):
    client, run_id, actor = prepared
    with client.session_factory() as db:
        proposal = db.get(ResolutionProposal, "safe-ab")
        record = db.get(ResolutionRecord, "b")
        record.raw_fields = {**record.raw_fields, "phone": "999"}
        proposal.golden_fields = {**proposal.golden_fields, "_golden_override": {"phone": "007"}}
        db.commit()
        snapshot = create_export(db, run_id, actor, True, {"safe-ab": {"phone": "b"}})
        assert list(csv.DictReader(StringIO(artifact_bytes(snapshot).decode("utf-8-sig"))))[0]["phone_number"] == "007"
        assert db.get(ResolutionProposal, "safe-ab").golden_fields["_golden_override"]["phone"] == "007"


def test_source_choice_and_survivorship(prepared):
    client, run_id, actor = prepared
    with client.session_factory() as db:
        record = db.get(ResolutionRecord, "b")
        record.raw_fields = {**record.raw_fields, "phone": "00999"}
        db.commit()
        snapshot = create_export(db, run_id, actor, True, {"safe-ab": {"phone": "b"}})
        row = next(csv.DictReader(StringIO(artifact_bytes(snapshot).decode("utf-8-sig"))))
        assert row["phone_number"] == "00999" and row["full_name"] == "An"


def test_unicode_quotes_multiline_and_large_raw_field(prepared):
    client, run_id, actor = prepared
    value = 'Nguyễn, "An"\n' + "x" * (256 * 1024)
    with client.session_factory() as db:
        record = db.get(ResolutionRecord, "c")
        record.external_id = "000C"
        record.raw_fields = {"name": value, "phone": "00123", "email": "a@example.com", "address": "Hà Nội"}
        db.commit()
        snapshot = create_export(db, run_id, actor, True)
        content = artifact_bytes(snapshot)
        assert ('"' + value.replace('"', '""') + '",00123,a@example.com,Hà Nội,000C,BILL,1,UNMERGED,true\r\n').encode() in content
        assert content.startswith(("\ufeff" + ",".join(COLUMNS) + "\r\n").encode())


@pytest.mark.parametrize("fault", ["upload", "corrupt", "manifest"])
def test_remote_interruption_never_publishes_and_retry_is_idempotent(prepared, monkeypatch, fault):
    client, run_id, actor = prepared

    class Store(LocalObjectStore):
        failing = True

        def upload_file(self, path, key, content_type):
            super().upload_file(path, key, content_type)
            if self.failing and fault == "upload":
                raise OSError("injected interruption")

        def iter_chunks(self, key, chunk_size=1024 * 1024):
            if self.failing and fault == "corrupt":
                yield b"corrupt"
            else:
                yield from super().iter_chunks(key, chunk_size)

        def write_manifest(self, key, payload):
            if self.failing and fault == "manifest":
                raise OSError("injected manifest interruption")
            super().write_manifest(key, payload)

    store = Store(Path(get_settings().export_artifact_dir) / "remote")
    monkeypatch.setattr("masterone.mdm.export_stream.get_store", lambda _: store)
    with client.session_factory() as db:
        with pytest.raises((OSError, HTTPException)):
            create_export(db, run_id, actor, True, export_id="stable")
        assert db.query(ResolutionExport).count() == 0
        assert db.query(ResolutionAuditEvent).filter_by(action="CSV_EXPORTED").count() == 0
        store.failing = False
        snapshot = create_export(db, run_id, actor, True, export_id="stable")
        repeated = create_export(db, run_id, actor, True, export_id="stable")
        assert snapshot.id == repeated.id == "stable"
        assert db.query(ResolutionExport).count() == 1
        assert db.query(ResolutionAuditEvent).filter_by(action="CSV_EXPORTED").count() == 1
        with pytest.raises(HTTPException) as exc:
            create_export(db, run_id, actor, False, export_id="stable")
        assert exc.value.status_code == 409


@pytest.mark.parametrize("invalid", ["missing", "overlap", "foreign"])
def test_coverage_rejects_invalid_members(prepared, invalid):
    client, run_id, actor = prepared
    with client.session_factory() as db:
        proposal = db.get(ResolutionProposal, "safe-ab")
        if invalid == "missing":
            proposal.member_record_ids = ["a", "missing"]
        elif invalid == "overlap":
            db.add(ResolutionProposal(id="overlap", run_id=run_id, member_record_ids=["b", "c"],
                   status="AUTO_READY", origin="AUTO", golden_fields={}))
        else:
            other = ResolutionRun(id="other", owner_user_id=actor, status="COMPLETED")
            db.add(other)
            db.get(ResolutionRecord, "b").run_id = "other"
        db.commit()
        with pytest.raises(HTTPException):
            create_export(db, run_id, actor, True)
        assert db.query(ResolutionExport).count() == 0


@pytest.mark.parametrize("budget,value", [("export_disk_bytes", 32768), ("export_max_rss_bytes", 1),
                                          ("export_timeout_seconds", 0.000001), ("export_max_group_records", 1)])
def test_resource_budget_fails_closed(prepared, monkeypatch, budget, value):
    client, run_id, actor = prepared
    monkeypatch.setattr(get_settings(), budget, value)
    with client.session_factory() as db:
        if budget == "export_disk_bytes":
            record = db.get(ResolutionRecord, "c")
            record.raw_fields = {"name": "x" * 65536}
            db.commit()
        with pytest.raises(HTTPException) as exc:
            create_export(db, run_id, actor, True)
        assert exc.value.status_code in (504, 507)
        assert db.query(ResolutionExport).count() == 0


def test_parquet_companion_matches_csv(prepared, monkeypatch):
    import pyarrow.parquet as pq
    client, run_id, actor = prepared
    monkeypatch.setattr(get_settings(), "export_parquet_enabled", True)
    with client.session_factory() as db:
        snapshot = create_export(db, run_id, actor, True)
        receipt = db.query(ResolutionAuditEvent).filter_by(action="CSV_EXPORTED").one().details
        store = LocalObjectStore(get_settings().export_artifact_dir)
        parquet = pq.read_table(BytesIO(store.download_bytes(receipt["artifacts"][".parquet"]["key"])))
        assert tuple(parquet.column_names) == COLUMNS
        rows = list(csv.DictReader(StringIO(artifact_bytes(snapshot).decode("utf-8-sig"))))
        assert parquet.to_pylist() == rows


def test_preview_retry_and_download_keep_snapshot(prepared):
    client, run_id, _ = prepared
    prefix = f"/api/v1/mdm/runs/{run_id}"
    preview = client.post(f"{prefix}/previews", json={"acknowledge_unreviewed": True}).json()
    url = f"{prefix}/previews/{preview['preview_id']}/execute"
    first = client.post(url, json={"execute_request_id": "retry"})
    assert first.status_code == 200, first.text
    repeated = client.post(url, json={"execute_request_id": "retry"})
    assert repeated.content == first.content and repeated.headers["x-export-id"] == first.headers["x-export-id"]
    download = client.get(f"{prefix}/exports/{first.headers['x-export-id']}/download")
    assert download.content == first.content
    with client.session_factory() as db:
        assert db.query(ResolutionExport).count() == 1
        assert db.get(ResolutionPreview, preview["preview_id"]).status == "EXECUTED"


def memory_probe(directory, count, batch):
    """Called in fresh child processes; seed memory is released before measurement."""
    settings = get_settings()
    settings.export_artifact_dir = str(Path(directory) / "artifacts")
    settings.export_batch_rows = batch
    engine = create_engine("sqlite:///" + str(Path(directory) / "probe.db"))
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine)
    with factory.begin() as db:
        user = User(email_normalized="probe@example.com", email="probe@example.com", password_hash="unused", role="STEWARD")
        db.add(user)
        db.flush()
        actor = user.id
        db.add(ResolutionRun(id="probe", owner_user_id=actor, status="COMPLETED", record_count=count))
        db.add(ResolutionFile(id="file", run_id="probe", filename="probe.csv", sha256="0" * 64, row_count=count))
        db.flush()
        for start in range(0, count, batch):
            db.bulk_insert_mappings(ResolutionRecord, [{"id": f"{n:08d}", "run_id": "probe", "file_id": "file",
                "external_id": str(n), "source_system": "CRM", "raw_fields": {"name": "x" * 4096},
                "normalized_fields": {}} for n in range(start, min(count, start + batch))])
    gc.collect()
    before = rss_peak()
    tracemalloc.start()
    with factory() as db:
        snapshot = create_export(db, "probe", actor, False)
        assert snapshot.row_count == count
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(json.dumps({"python_peak": peak, "rss_growth": max(0, rss_peak() - before)}))
    engine.dispose()


@pytest.mark.parametrize("batch", [16, 128])
def test_memory_stability_in_fresh_processes(tmp_path, batch, request):
    results = []
    for count in (128, 4096):
        directory = tmp_path / str(count)
        directory.mkdir()
        command = [sys.executable, "-X", "utf8", "-c",
                   "from tests.test_mdm.test_export_stream import memory_probe; import sys; "
                   "memory_probe(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]))",
                   str(directory), str(count), str(batch)]
        result = subprocess.run(command, capture_output=True, text=True, env=os.environ.copy(), timeout=120)
        assert result.returncode == 0, result.stderr
        results.append(json.loads(result.stdout.strip().splitlines()[-1]))
    request.node.user_properties.append(("memory_probe", json.dumps({"batch": batch, "results": results})))
    # 16 MiB additional CSV data must not become an additional in-memory file/list.
    assert results[1]["python_peak"] < results[0]["python_peak"] + 6 * 1024**2, results
    assert results[1]["rss_growth"] < results[0]["rss_growth"] + 32 * 1024**2, results


def test_direct_api_retry_key_and_conflicting_choices(prepared):
    client, run_id, _ = prepared
    url = f"/api/v1/mdm/runs/{run_id}/exports"
    body = {"acknowledge_unreviewed": True}
    headers = {"Idempotency-Key": "direct-retry"}
    first = client.post(url, json=body, headers=headers)
    assert first.status_code == 200, first.text
    repeated = client.post(url, json=body, headers=headers)
    assert repeated.content == first.content
    assert repeated.headers["x-export-id"] == first.headers["x-export-id"]
    assert client.post(url, json={**body, "choices": {"safe-ab": {"phone": "a"}}}, headers=headers).status_code == 409


def test_empty_export_has_valid_csv_and_parquet_schema(prepared, monkeypatch):
    import pyarrow.parquet as pq
    client, run_id, actor = prepared
    monkeypatch.setattr(get_settings(), "export_parquet_enabled", True)
    with client.session_factory() as db:
        from masterone.mdm.models import ResolutionPair, ResolutionReviewCase
        db.query(ResolutionReviewCase).delete()
        db.query(ResolutionPair).delete()
        db.query(ResolutionProposal).delete()
        db.query(ResolutionRecord).delete()
        db.commit()
        snapshot = create_export(db, run_id, actor, False)
        assert snapshot.row_count == 0
        assert not list(csv.DictReader(StringIO(artifact_bytes(snapshot).decode("utf-8-sig"))))
        receipt = db.query(ResolutionAuditEvent).filter_by(action="CSV_EXPORTED").one().details
        store = LocalObjectStore(get_settings().export_artifact_dir)
        with pq.ParquetFile(BytesIO(store.download_bytes(receipt["artifacts"][".parquet"]["key"]))) as file:
            assert tuple(file.schema_arrow.names) == COLUMNS
            assert file.metadata.num_rows == 0
