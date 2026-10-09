"""Opt-in timing for the upload commit API: set MDM_BENCH_ROWS=10000 to run."""

import csv
import hashlib
import os
import time
from io import StringIO

import pytest
from masterone.config import get_settings

from tests.test_mdm.test_upload_sessions import _create_draft, _upload_payload

pytestmark = pytest.mark.skipif(not os.environ.get("MDM_BENCH_ROWS"), reason="opt-in benchmark")


def _csv_payload(rows):
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["record_id", "source", "name", "phone", "email", "address"])
    for i in range(rows):
        writer.writerow([f"R{i:06d}", ["CRM", "ERP", "BILL"][i % 3], f"Nguyen Van {i % 500}",
                         f"09{i // 3:08d}", f"user{i % 700}@example.test", f"Ha Noi {i % 90}"])
    return buffer.getvalue().encode("utf-8")


def test_commit_timing(client, monkeypatch, tmp_path):
    rows = int(os.environ["MDM_BENCH_ROWS"])
    monkeypatch.setattr(get_settings(), "upload_staging_dir", str(tmp_path / "staging"))
    payload = _csv_payload(rows)
    timings = {}

    t = time.perf_counter()
    draft = _create_draft(client, "bench.csv", payload)
    _upload_payload(client, draft, payload)
    timings["upload"] = time.perf_counter() - t

    prefix = f"/api/v1/mdm/upload-sessions/{draft['session_id']}"
    t = time.perf_counter()
    validated = client.post(f"{prefix}/validate")
    timings["validate"] = time.perf_counter() - t
    assert validated.json()["status"] == "READY", validated.text

    profiler = None
    if os.environ.get("MDM_BENCH_PROFILE"):
        import cProfile
        profiler = cProfile.Profile()
        profiler.enable()
    t = time.perf_counter()
    committed = client.post(f"{prefix}/commit")
    timings["commit"] = time.perf_counter() - t
    if profiler is not None:
        profiler.disable()
        import pstats
        pstats.Stats(profiler).sort_stats("cumtime").print_stats("masterone", 30)
    assert committed.status_code == 201, committed.text
    assert committed.json()["profile"]["row_count"] == rows

    print("\nBENCH rows=%d " % rows + " ".join(f"{k}={v:.2f}s" for k, v in timings.items()))
    assert hashlib.sha256(payload).hexdigest()
