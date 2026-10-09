"""Local-only PostgreSQL/worker recovery tests; targets masterone-qa exclusively."""
import json
import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import boto3
import httpx
import numpy as np
from qa_live_system import DB, Probe, synthetic
from run_merge_baseline import isolated_environment
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

OUT = Path("artifacts/full-system-20261009/operations")
os.environ.update(isolated_environment(OUT / "state"))
from masterone.auth import models as auth_models  # noqa: F401
from masterone.mdm import models as mdm_models  # noqa: F401
from masterone.mdm.job_queue import OwnershipLostError, claim_next, heartbeat, recover
from masterone.mdm.models import ResolutionRecord

COMPOSE = ["docker", "compose", "--env-file", ".env.qa.local", "-p", "masterone-qa", "-f", "docker-compose.qa.yml"]
probe = Probe(OUT)
factory = sessionmaker(bind=probe.engine, expire_on_commit=False)
owned = []


def compose(*args):
    with (OUT / "docker-operations.log").open("a", encoding="utf-8") as log:
        subprocess.run([*COMPOSE, *args], stdout=log, stderr=subprocess.STDOUT, check=True, timeout=90)


def ready(expected=200, timeout=45):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        try:
            response = httpx.get("http://127.0.0.1:18001/ready", timeout=5)
            if response.status_code == expected:
                return response.json()
        except httpx.TransportError:
            if expected != 200:
                raise
        time.sleep(0.5)
    raise TimeoutError("QA readiness did not reach expected state")


def upload(count):
    result = probe.upload(content=synthetic(count))
    owned.append(result["run_id"])
    return result


def wait(task, timeout=180):
    task_id = task.get("task_id", task.get("id"))
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        result = probe.request("GET", f"/api/v1/mdm/runs/tasks/{task_id}").json()
        if result["status"] == "COMPLETED":
            return result
        if result["status"] in {"FAILED", "RETRYABLE_FAILED", "CANCELLED"}:
            raise RuntimeError("QA task failed")
        time.sleep(0.3)
    raise TimeoutError("QA worker recovery timed out")


def concurrent_uploads():
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = list(pool.map(upload, (100, 100)))
    assert len({job["run_id"] for job in jobs}) == 2
    with probe.engine.connect() as db:
        for job in jobs:
            assert db.scalar(text("SELECT count(*) FROM mdm_resolution_records WHERE run_id=:run"), {"run": job["run_id"]}) == 100
    return {"concurrent_uploads": 2, "records_each": 100, "atomic_isolated_runs": True}


def claims_and_fencing():
    with probe.engine.connect() as db:
        assert db.scalar(text("SELECT count(*) FROM mdm_resolution_tasks WHERE status IN ('RUNNING','QUEUED')")) == 0
    compose("stop", "worker")
    job = upload(100)
    task = probe.request("POST", f"/api/v1/mdm/runs/{job['run_id']}/start", expected=202).json()
    barrier = threading.Barrier(12)
    def contender(number):
        barrier.wait(timeout=10)
        return claim_next(factory, f"qa-contender-{number}", lease_seconds=30)
    with ThreadPoolExecutor(max_workers=12) as pool:
        claims = [claim for claim in pool.map(contender, range(12)) if claim is not None]
    assert len(claims) == 1 and claims[0].run_id == job["run_id"]
    old = claims[0]
    # Simulate expiry of ONLY the claim created by this test, using DB clock.
    with factory.begin() as db:
        db.execute(text("UPDATE mdm_resolution_job_attempts SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=:id AND run_id=:run"),
                   {"id": old.attempt_id, "run": old.run_id})
    recover(factory)
    fenced = False
    try:
        heartbeat(factory, old)
    except OwnershipLostError:
        fenced = True
    assert fenced
    compose("up", "-d", "--no-deps", "--scale", "worker=2", "worker")
    wait(task)
    with probe.engine.connect() as db:
        row = db.execute(text("SELECT status,attempt_count FROM mdm_resolution_tasks WHERE run_id=:run"), {"run": old.run_id}).mappings().one()
        assert row["status"] == "COMPLETED" and row["attempt_count"] == 2
    return {"contenders": 12, "successful_claims": 1, "stale_owner_fenced": True, "recovered_attempts": 2, "two_real_workers": True}


def crash_recovery():
    job = upload(4000)
    task = probe.request("POST", f"/api/v1/mdm/runs/{job['run_id']}/start", expected=202).json()
    until = time.monotonic() + 45
    killed = False
    while time.monotonic() < until and not killed:
        with probe.engine.connect() as db:
            row = db.execute(text("SELECT a.worker_id,t.status FROM mdm_resolution_tasks t JOIN mdm_resolution_job_attempts a ON a.id=t.attempt_id WHERE t.run_id=:run"), {"run": job["run_id"]}).mappings().first()
        if row and row["status"] == "RUNNING":
            for container in ("masterone-qa-worker-1", "masterone-qa-worker-2"):
                hostname = subprocess.check_output(["docker", "inspect", "--format", "{{.Config.Hostname}}", container], text=True).strip()
                if row["worker_id"].startswith(hostname + ":"):
                    # Kill only the spawned attempt, identified by its actual
                    # multiprocessing command and supervisor PPID; no env dumps.
                    code = "import pathlib,os,signal; pids=[int(p.name) for p in pathlib.Path('/proc').iterdir() if p.name.isdigit() and int(p.name)!=os.getpid() and b'spawn_main' in (p/'cmdline').read_bytes() and (p/'stat').read_text().split()[3]=='1']; assert len(pids)<=1; [os.kill(p,signal.SIGKILL) for p in pids]; print(len(pids))"
                    answer = subprocess.check_output(["docker", "exec", container, "python", "-c", code], text=True).strip()
                    killed = answer == "1"
        if not killed:
            time.sleep(0.2)
    assert killed
    wait(task)
    with probe.engine.connect() as db:
        args = {"run": job["run_id"]}
        attempts = db.scalar(text("SELECT attempt_count FROM mdm_resolution_tasks WHERE run_id=:run"), args)
        failed = db.scalar(text("SELECT count(*) FROM mdm_resolution_job_attempts WHERE run_id=:run AND error_code='WORKER_CRASHED'"), args)
        records = db.scalar(text("SELECT count(*) FROM mdm_resolution_records WHERE run_id=:run"), args)
        pairs, unique = db.execute(text("SELECT count(*),count(DISTINCT (left_record_id,right_record_id)) FROM mdm_resolution_pairs WHERE run_id=:run"), args).one()
    assert attempts == 2 and failed == 1 and records == 4000 and pairs == unique > 0
    return {"run_id": job["run_id"], "killed_attempts": 1, "attempt_count": attempts, "records": records,
            "published_pairs": pairs, "no_duplicate_pairs": True}


def constraints_and_queries():
    with factory() as db:
        source = db.query(ResolutionRecord).filter_by(run_id=owned[0]).first()
        payload = {column.name: getattr(source, column.name) for column in ResolutionRecord.__table__.columns}
    for kind in ("unique", "foreign_key"):
        with factory() as db:
            changed = {**payload, "id": str(uuid4())}
            if kind == "foreign_key":
                changed["run_id"] = str(uuid4())
            try:
                db.add(ResolutionRecord(**changed))
                db.commit()
            except IntegrityError:
                db.rollback()
            else:
                raise AssertionError("Constraint did not reject invalid write")
    samples = []
    with probe.engine.connect() as db:
        for _ in range(100):
            start = time.perf_counter()
            db.execute(text("SELECT id FROM mdm_resolution_records WHERE run_id=:run ORDER BY id LIMIT 25"), {"run": owned[0]}).all()
            samples.append((time.perf_counter() - start) * 1000)
        plan = db.scalar(text("EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) SELECT id FROM mdm_resolution_records WHERE run_id=:run ORDER BY id LIMIT 25"), {"run": owned[0]})
    (OUT / "query-explain.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    assert probe.engine.pool.checkedout() == 0
    return {"unique_and_fk_rejection": True, "transaction_rollback": True, "query_samples": 100,
            "query_ms_p50_p95_p99": list(np.percentile(samples, [50, 95, 99])), "pool_checkedout_after_queries": 0}


def database_restart():
    with probe.engine.connect() as db:
        assert db.scalar(text("SELECT 1")) == 1
    compose("stop", "database")
    try:
        assert ready(503)["status"] == "not_ready"
        assert httpx.get("http://127.0.0.1:18001/health").status_code == 200
    finally:
        compose("start", "database")
    ready()
    with probe.engine.connect() as db:
        assert db.scalar(text("SELECT count(*) FROM mdm_resolution_records WHERE run_id=:run"), {"run": owned[0]}) == 100
    return {"readiness_during_outage": 503, "liveness_during_outage": 200, "stale_pool_recovered": True, "persisted_records_preserved": True}


def storage_outage():
    subprocess.run(["docker", "pause", "masterone-qa-storage-1"], check=True, capture_output=True)
    try:
        assert ready(503, timeout=15)["checks"]["storage"] == "unavailable"
    finally:
        subprocess.run(["docker", "unpause", "masterone-qa-storage-1"], check=True, capture_output=True)
    ready()
    return {"readiness_during_storage_outage": 503, "readiness_after_recovery": 200, "emulator_recreated": False}


def missing_export():
    browser = json.loads(Path("artifacts/full-system-20261009/browser-real-merge/results.json").read_text())
    run_id = browser["run_id"]
    with probe.engine.connect() as db:
        row = db.execute(text("SELECT e.id,e.r2_key FROM mdm_resolution_exports e JOIN mdm_resolution_runs r ON r.id=e.run_id JOIN users u ON u.id=r.owner_user_id WHERE e.run_id=:run AND u.email_normalized='qa-steward@example.com' ORDER BY e.created_at DESC LIMIT 1"), {"run": run_id}).mappings().one()
    assert run_id in row["r2_key"]
    store = boto3.client("s3", endpoint_url="http://127.0.0.1:19000", aws_access_key_id="qa-local-access", aws_secret_access_key="qa-local-secret-only", region_name="us-east-1")
    response = store.get_object(Bucket="masterone-qa", Key=row["r2_key"])
    try:
        content = response["Body"].read()
    finally:
        response["Body"].close()
    try:
        store.delete_object(Bucket="masterone-qa", Key=row["r2_key"])
        probe.request("GET", f"/api/v1/mdm/runs/{run_id}/exports/{row['id']}/download", expected=409)
    finally:
        store.put_object(Bucket="masterone-qa", Key=row["r2_key"], Body=content, ContentType="text/csv")
        store.close()
    assert probe.request("GET", f"/api/v1/mdm/runs/{run_id}/exports/{row['id']}/download").content == content
    return {"missing_object_http_status": 409, "owned_object_restored": True, "download_after_restore_checksum": True}


try:
    assert DB.endswith("/masterone_qa") and "127.0.0.1:15432" in DB
    probe.check("authenticated_steward", probe.login)
    for name, fn in (("concurrent_uploads", concurrent_uploads), ("postgres_claim_race_expiry_fencing", claims_and_fencing),
                     ("real_worker_child_crash_retry", crash_recovery), ("constraints_pool_query_latency", constraints_and_queries),
                     ("database_restart_recovery", database_restart), ("storage_network_pause_readiness", storage_outage),
                     ("missing_export_consistency", missing_export)):
        probe.check(name, fn)
finally:
    compose("start", "database")
    subprocess.run(["docker", "unpause", "masterone-qa-storage-1"], capture_output=True)
    compose("up", "-d", "--no-deps", "--scale", "worker=1", "worker")
    probe.client.close()
    probe.engine.dispose()
    probe.save()
raise SystemExit(int(any(case["status"] == "FAIL" for case in probe.results)))
