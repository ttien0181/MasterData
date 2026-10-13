import multiprocessing
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta

import pytest
from alembic import command
from masterone.db.base import now_utc
from masterone.mdm.generation_staging import GenerationPublisher
from masterone.mdm.job_queue import (
    OwnershipLostError,
    claim_next,
    failed,
    heartbeat,
    ownership,
    progress,
    recover,
    request_cancel,
)
from masterone.mdm.migrate import alembic_config
from masterone.mdm.models import ResolutionJobAttempt as Attempt
from masterone.mdm.models import ResolutionPair, ResolutionTask
from masterone.mdm.resolution_service import create_task, run_pipeline
from masterone.mdm.resolution_worker import ResolutionWorker, execute_attempt
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .test_generation_staging import snapshot as snapshot_fixture


@pytest.fixture
def snapshot(tmp_path):
    yield from snapshot_fixture.__wrapped__(tmp_path)


def expire(factory, claim, field="lease_expires_at"):
    with factory.begin() as db:
        setattr(db.get(Attempt, claim.attempt_id), field, now_utc() - timedelta(seconds=1))


def state(factory):
    with factory() as db:
        task = db.get(ResolutionTask, "task")
        return task.status, task.attempt_id, task.error_code


def test_two_workers_claim_same_job(snapshot):
    factory, _, _ = snapshot
    barrier = threading.Barrier(2)

    def claim(worker):
        barrier.wait(timeout=5)
        return claim_next(factory, worker)

    with ThreadPoolExecutor(2) as pool:
        claims = list(pool.map(claim, ("worker-a", "worker-b")))
    assert sum(c is not None for c in claims) == 1
    with factory() as db:
        assert db.query(Attempt).count() == 1
        assert db.get(ResolutionTask, "task").attempt_count == 1


def test_live_owner_survives_other_worker_startup(snapshot):
    factory, _, _ = snapshot
    claim = claim_next(factory, "live")
    recover(factory)
    assert claim_next(factory, "other") is None
    heartbeat(factory, claim)
    assert state(factory)[:2] == ("RUNNING", claim.attempt_id)


def test_queue_schema_defaults_match_migration(snapshot):
    from sqlalchemy import inspect, text

    factory, _, _ = snapshot
    with factory.begin() as db:
        db.execute(text("DELETE FROM mdm_resolution_tasks"))
        db.execute(text("INSERT INTO mdm_resolution_tasks "
            "(id,run_id,status,phase,processed_count,total_count,created_at,updated_at) "
            "VALUES ('task','run','QUEUED','queued',0,0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"))
        task = db.get(ResolutionTask, "task")
        assert task.cancel_requested is False
        assert task.attempt_count == 0 and task.max_attempts == 3
        assert "ix_mdm_resolution_tasks_queue" in {
            i["name"] for i in inspect(db.get_bind()).get_indexes("mdm_resolution_tasks")}
    assert claim_next(factory, "worker") is not None


@pytest.mark.parametrize("field,code", [("lease_expires_at", "LEASE_EXPIRED"), ("deadline_at", "JOB_TIMEOUT")])
def test_expiry_retry_fences_every_old_owner_write(snapshot, field, code):
    factory, _, _ = snapshot
    old = claim_next(factory, "old")
    expire(factory, old, field)
    for mutation in (lambda: heartbeat(factory, old), lambda: progress(factory, old, "late", 1, 1),
                     lambda: failed(factory, old)):
        with pytest.raises(OwnershipLostError):
            mutation()
    recover(factory)
    assert state(factory)[::2] == ("QUEUED", code)
    new = claim_next(factory, "new")
    assert new.attempt_id != old.attempt_id
    with ownership(old), pytest.raises(OwnershipLostError):
        run_pipeline("run", factory, lambda *_: None)
    with ownership(new):
        run_pipeline("run", factory, lambda p, d, t: progress(factory, new, p, d, t))
    assert state(factory)[0] == "COMPLETED"
    with factory() as db:
        assert db.get(Attempt, old.attempt_id).status == "FAILED"
        assert db.get(Attempt, new.attempt_id).status == "SUCCEEDED"


def test_wrong_worker_and_unowned_execution_are_blocked(snapshot):
    factory, _, _ = snapshot
    claim = claim_next(factory, "owner")
    with pytest.raises(OwnershipLostError):
        heartbeat(factory, replace(claim, worker_id="impostor"))
    with pytest.raises(OwnershipLostError):
        run_pipeline("run", factory, lambda *_: None)
    assert state(factory)[0] == "RUNNING"


def test_heartbeat_renews_attempt_without_taking_publication_lock(snapshot, monkeypatch):
    from masterone.mdm import job_queue

    factory, _, _ = snapshot
    claim = claim_next(factory, "owner", lease_seconds=3, timeout_seconds=6)

    def unexpected_lock(*_):
        pytest.fail("Heartbeat must not wait for the publication run lock")

    original_lock = job_queue.lock_run
    monkeypatch.setattr(job_queue, "lock_run", unexpected_lock)
    heartbeat(factory, claim, lease_seconds=60)
    with factory() as db:
        attempt = db.get(Attempt, claim.attempt_id)
        assert attempt.lease_expires_at == attempt.deadline_at
        assert attempt.status == "RUNNING"
    with pytest.raises(OwnershipLostError):
        heartbeat(factory, replace(claim, task_id="another-task"))
    monkeypatch.setattr(job_queue, "lock_run", original_lock)
    request_cancel(factory, "run")
    with pytest.raises(OwnershipLostError):
        heartbeat(factory, claim)


def test_valid_worker_cannot_mutate_another_generation_attempt(snapshot):
    factory, _, _ = snapshot
    old = claim_next(factory, "old")
    publisher = GenerationPublisher(factory, store=None)
    with ownership(old):
        identity = publisher.begin("run", config={}, attempt_id=old.attempt_id)
    expire(factory, old)
    recover(factory)
    new = claim_next(factory, "new")
    with ownership(new):
        with pytest.raises(OwnershipLostError, match="JOB_ATTEMPT_MISMATCH"):
            publisher.begin("run", config={}, attempt_id=old.attempt_id)
        with pytest.raises(OwnershipLostError, match="JOB_ATTEMPT_MISMATCH"):
            publisher.fail(identity)
        with pytest.raises(OwnershipLostError, match="JOB_ATTEMPT_MISMATCH"):
            publisher.promote(identity)
    assert state(factory)[:2] == ("RUNNING", new.attempt_id)


def test_lost_commit_acknowledgment_does_not_retry_completed_attempt(snapshot):
    factory, computed, _ = snapshot
    claim = claim_next(factory, "owner")

    def lost_ack(name):
        if name == "after_promote_commit":
            raise RuntimeError("lost acknowledgment")

    publisher = GenerationPublisher(factory, store=None, failpoint=lost_ack)
    with ownership(claim):
        identity = publisher.begin("run", config={}, attempt_id=claim.attempt_id)
        publisher.stage(identity, computed)
        with pytest.raises(RuntimeError, match="lost acknowledgment"):
            publisher.promote(identity)
    with pytest.raises(OwnershipLostError):
        failed(factory, claim, "WORKER_CRASHED")
    recover(factory)
    assert claim_next(factory, "replacement") is None
    assert state(factory)[0] == "COMPLETED"
    with factory() as db:
        assert db.get(Attempt, claim.attempt_id).status == "SUCCEEDED"


def test_cancel_queued_and_manual_retry(snapshot):
    factory, _, _ = snapshot
    assert request_cancel(factory, "run").status == "CANCELLED"
    assert claim_next(factory, "worker") is None
    task = create_task("run", factory)
    assert task.id == "task" and not task.cancel_requested
    assert claim_next(factory, "worker") is not None


def test_cancel_running_fences_publication(snapshot):
    factory, computed, _ = snapshot
    claim = claim_next(factory, "worker")
    publisher = GenerationPublisher(factory, store=None)
    with ownership(claim):
        identity = publisher.begin("run", config={}, attempt_id=claim.attempt_id)
        publisher.stage(identity, computed)
    assert request_cancel(factory, "run").status == "RUNNING"
    with ownership(claim), pytest.raises(OwnershipLostError):
        publisher.promote(identity)
    recover(factory)
    assert state(factory)[0] == "CANCELLED"
    with factory() as db:
        assert db.query(ResolutionPair).count() == 0
        assert db.get(Attempt, claim.attempt_id).status == "CANCELLED"


def test_generation_cannot_be_committed_by_two_attempts(snapshot):
    factory, computed, _ = snapshot
    old = claim_next(factory, "old")
    publisher = GenerationPublisher(factory, store=None)
    with ownership(old):
        first = publisher.begin("run", config={}, attempt_id=old.attempt_id)
        publisher.stage(first, computed)
    expire(factory, old)
    recover(factory)
    new = claim_next(factory, "new")
    with ownership(new):
        second = publisher.begin("run", config={}, attempt_id=new.attempt_id)
        assert first.generation_id == second.generation_id
        publisher.stage(second, computed)
    with ownership(old), pytest.raises(OwnershipLostError):
        publisher.promote(first)
    with ownership(new):
        publisher.promote(second)
    with ownership(old), pytest.raises(OwnershipLostError):
        publisher.fail(first)
    assert state(factory)[0] == "COMPLETED"
    with factory() as db:
        assert db.get(Attempt, new.attempt_id).status == "SUCCEEDED"
        assert db.query(ResolutionPair).count() == len(computed["pairs"])


def test_retry_budget_and_explicit_retry(snapshot):
    factory, _, _ = snapshot
    ids = []
    for _ in range(3):
        claim = claim_next(factory, "worker")
        ids.append(claim.attempt_id)
        expire(factory, claim, "deadline_at")
        recover(factory)
    assert len(set(ids)) == 3
    assert state(factory)[0] == "RETRYABLE_FAILED"
    assert claim_next(factory, "worker") is None
    create_task("run", factory)
    assert claim_next(factory, "worker") is not None
    with factory() as db:
        assert db.query(Attempt).count() == 4


def test_worker_crash_process_and_recovery(snapshot):
    factory, _, _ = snapshot
    claim = claim_next(factory, "crashed")
    # Abruptly kill an actual spawned process without its cleanup code.
    url = factory.kw["bind"].url.render_as_string(hide_password=False)
    child = multiprocessing.get_context("spawn").Process(target=execute_attempt, args=(url, claim))
    child.start()
    child.terminate()
    child.join(timeout=5)
    assert not child.is_alive()
    expire(factory, claim)
    recover(factory)
    retry = claim_next(factory, "replacement")
    assert retry.attempt_id != claim.attempt_id
    url = factory.kw["bind"].url.render_as_string(hide_password=False)
    process = multiprocessing.get_context("spawn").Process(target=execute_attempt, args=(url, retry))
    process.start()
    process.join(timeout=30)
    if process.is_alive():
        process.terminate()
        process.join()
        pytest.fail("Worker did not complete within test deadline")
    assert process.exitcode == 0
    assert state(factory)[0] == "COMPLETED"


def test_actual_independent_worker_completes(snapshot):
    factory, _, _ = snapshot
    worker = ResolutionWorker(factory)
    assert worker.run_once()
    assert state(factory)[0] == "COMPLETED"
    assert not worker.run_once()


def test_api_restart_preserves_queue_and_active_owner(snapshot, monkeypatch):
    import asyncio

    from fastapi import FastAPI
    from masterone import main
    factory, _, _ = snapshot
    engine = factory.kw["bind"]
    monkeypatch.setattr(main.get_settings(), "app_env", "development")
    monkeypatch.setattr(main.get_settings(), "mdm_embedded_worker_enabled", False)
    monkeypatch.setattr(main, "create_database", lambda *_: (engine, factory))
    monkeypatch.setattr(main, "require_current", lambda *_: None)

    async def restart():
        app = FastAPI()
        async with main.lifespan(app):
            assert not hasattr(app.state, "resolution_worker")

    asyncio.run(restart())
    assert state(factory)[0] == "QUEUED"
    claim = claim_next(factory, "external")
    asyncio.run(restart())
    heartbeat(factory, claim)
    assert state(factory)[:2] == ("RUNNING", claim.attempt_id)


def test_api_embedded_worker_starts_and_stops_with_lifespan(snapshot, monkeypatch):
    import asyncio

    from fastapi import FastAPI
    from masterone import main

    factory, _, _ = snapshot
    engine = factory.kw["bind"]
    events = []

    class FakeWorker:
        def __init__(self, received_factory):
            assert received_factory is factory

        def start(self):
            events.append("start")

        def close(self):
            events.append("close")

    monkeypatch.setattr(main.get_settings(), "app_env", "development")
    monkeypatch.setattr(main.get_settings(), "mdm_embedded_worker_enabled", True)
    monkeypatch.setattr(main, "create_database", lambda *_: (engine, factory))
    monkeypatch.setattr(main, "require_current", lambda *_: None)
    monkeypatch.setattr(main, "ResolutionWorker", FakeWorker)

    async def run():
        app = FastAPI()
        async with main.lifespan(app):
            assert app.state.resolution_worker is not None
            assert events == ["start"]

    asyncio.run(run())
    assert events == ["start", "close"]


def test_migration_preserves_jobs_and_attempt_history(tmp_path, monkeypatch):
    monkeypatch.delenv("MDM_DATABASE_URL", raising=False)
    url = "sqlite:///" + (tmp_path / "migration.db").as_posix()
    cfg = alembic_config(url)
    command.upgrade(cfg, "0023_engine_selection")
    engine = create_engine(url)
    from sqlalchemy import text
    with engine.begin() as db:
        db.execute(text("INSERT INTO users (id,email,email_normalized,password_hash,role,is_active,created_at) "
            "VALUES ('owner','o@example.test','o@example.test','unused','STEWARD',1,CURRENT_TIMESTAMP)"))
        db.execute(text("INSERT INTO mdm_resolution_runs (id,owner_user_id,status,phase,policy_version,record_count,file_count,candidate_count,insufficient_count,created_at) "
            "VALUES ('run','owner','RUNNING','blocking','v',0,0,0,0,CURRENT_TIMESTAMP)"))
        db.execute(text("INSERT INTO mdm_resolution_tasks (id,run_id,status,phase,processed_count,total_count,created_at,updated_at) "
            "VALUES ('task','run','RUNNING','blocking',0,0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"))
    command.upgrade(cfg, "head")
    factory = sessionmaker(engine, expire_on_commit=False)
    recover(factory)
    claim = claim_next(factory, "worker")
    command.downgrade(cfg, "0023_engine_selection")
    command.upgrade(cfg, "head")
    assert state(factory)[:2] == ("RUNNING", claim.attempt_id)
    heartbeat(factory, claim)
    engine.dispose()


def test_cancel_endpoint_preserves_task_dto_and_owner_check(client):
    response = client.post("/api/v1/mdm/runs", files=[("files", ("a.csv",
        b"id,source_system,full_name\na,CRM,Alice\n", "text/csv"))])
    assert response.status_code == 201
    run_id = response.json()["run_id"]
    start = client.post(f"/api/v1/mdm/runs/{run_id}/start")
    assert start.status_code == 202
    assert client.post(f"/api/v1/mdm/runs/{run_id}/cancel",
                       headers={"X-CSRF-Token": "wrong"}).status_code == 403
    result = client.post(f"/api/v1/mdm/runs/{run_id}/cancel")
    assert result.status_code == 200
    assert result.json()["task_id"] == start.json()["task_id"]
    assert result.json()["status"] == "CANCELLED"
    assert set(result.json()) == {"task_id", "run_id", "status", "phase", "processed_count",
                                  "total_count", "error_code", "updated_at"}
    assert client.post("/api/v1/mdm/runs/unknown/cancel").status_code == 404
    from masterone.auth.models import User
    credentials = {"email": "other-worker-test@example.com", "password": "a-long-test-password"}
    assert client.post("/api/v1/auth/register", json=credentials).status_code == 201
    with client.session_factory.begin() as db:
        db.query(User).filter_by(email_normalized=credentials["email"]).one().role = "STEWARD"
    login = client.post("/api/v1/auth/login", json=credentials).json()
    client.headers.update({"X-CSRF-Token": login["csrf_token"]})
    assert client.post(f"/api/v1/mdm/runs/{run_id}/cancel").status_code == 404



def slow_attempt(_url, _claim):
    import time
    time.sleep(30)


@pytest.mark.parametrize("operation", ["timeout", "cancel"])
def test_supervisor_terminates_actual_child(snapshot, monkeypatch, operation):
    import time

    from masterone.mdm import resolution_worker
    factory, _, _ = snapshot
    monkeypatch.setattr(resolution_worker, "execute_attempt", slow_attempt)
    worker = ResolutionWorker(factory, lease_seconds=3,
        timeout_seconds=0.2 if operation == "timeout" else 30)
    result = []
    thread = threading.Thread(target=lambda: result.append(worker.run_once()))
    thread.start()
    deadline = time.monotonic() + 5
    while (worker.child is None or worker.child.pid is None) and time.monotonic() < deadline:
        time.sleep(0.01)
    child = worker.child
    assert child is not None and child.pid is not None
    if operation == "cancel":
        request_cancel(factory, "run")
    thread.join(timeout=8)
    if thread.is_alive():
        worker.close()
        thread.join(timeout=5)
        pytest.fail("Supervisor failed to stop child")
    assert result == [True]
    assert not child.is_alive()
    assert state(factory)[::2] == (("QUEUED", "JOB_TIMEOUT") if operation == "timeout"
                                  else ("CANCELLED", "JOB_CANCELLED"))



def claim_in_process(url, worker_id, barrier, output):
    from masterone.db.session import create_database
    engine, factory = create_database(url)
    try:
        barrier.wait(timeout=10)
        output.put(claim_next(factory, worker_id))
    finally:
        engine.dispose()


def test_two_processes_claim_same_job(snapshot):
    factory, _, _ = snapshot
    url = factory.kw["bind"].url.render_as_string(hide_password=False)
    ctx = multiprocessing.get_context("spawn")
    barrier, output = ctx.Barrier(2), ctx.Queue()
    children = [ctx.Process(target=claim_in_process, args=(url, name, barrier, output))
                for name in ("process-a", "process-b")]
    try:
        for child in children:
            child.start()
        claims = [output.get(timeout=20) for _ in children]
        assert sum(c is not None for c in claims) == 1
        for child in children:
            child.join(timeout=5)
            assert child.exitcode == 0
        with factory() as db:
            assert db.query(Attempt).count() == 1
    finally:
        for child in children:
            if child.is_alive():
                child.terminate()
                child.join()
        output.close()
        output.join_thread()



def test_lease_expires_during_promotion_rolls_back_everything(snapshot, monkeypatch):
    from masterone.mdm import job_queue
    factory, computed, _ = snapshot
    claim = claim_next(factory, "worker")

    def failpoint(name):
        if name == "during_promote":
            monkeypatch.setattr(job_queue, "database_now", lambda _db: now_utc() + timedelta(seconds=60))

    publisher = GenerationPublisher(factory, store=None, failpoint=failpoint)
    with ownership(claim):
        identity = publisher.begin("run", config={}, attempt_id=claim.attempt_id)
        publisher.stage(identity, computed)
        with pytest.raises(OwnershipLostError):
            publisher.promote(identity)
    with factory() as db:
        assert db.query(ResolutionPair).count() == 0
        assert db.get(ResolutionTask, "task").status == "RUNNING"
        assert db.get(Attempt, claim.attempt_id).status == "RUNNING"


def test_cli_entrypoint_runs_without_fastapi(snapshot):
    import os
    import subprocess
    import sys

    from masterone.mdm.migrate import HEAD
    from sqlalchemy import text
    factory, _, _ = snapshot
    engine = factory.kw["bind"]
    with engine.begin() as db:
        db.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"))
        db.execute(text("INSERT INTO alembic_version VALUES (:head)"), {"head": HEAD})
    url = engine.url.render_as_string(hide_password=False)
    env = {**os.environ, "MDM_DATABASE_URL": url, "MDM_DATABASE_URL_LOCAL": url, "DATABASE_URL": url}
    result = subprocess.run([sys.executable, "-X", "utf8", "-m", "masterone.mdm.resolution_worker", "--once"],
                            env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert state(factory)[0] == "COMPLETED"
