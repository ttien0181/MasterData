"""Bounded local real transactions and read-only QA Neon pool probes."""
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

from dotenv import dotenv_values
from qa_live_system import ROOT, Probe
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

sys.path.insert(0, str(ROOT / "backend/src"))
from masterone.db.session import create_database

out = Path("artifacts/full-system-20261009/database-transactions")
probe = Probe(out)
table = "qa_transactions_" + uuid4().hex


def local():
    assert probe.engine.url.host == "127.0.0.1" and probe.engine.url.port == 15432
    assert probe.engine.url.database == "masterone_qa"
    with probe.engine.begin() as db:
        db.execute(text(f"CREATE TABLE {table}(id INTEGER PRIMARY KEY, value INTEGER NOT NULL)"))
        db.execute(text(f"INSERT INTO {table} VALUES (1,0),(2,0)"))
    try:
        def upsert(i):
            with probe.engine.begin() as db:
                db.execute(text(f"INSERT INTO {table} VALUES (:id,1) ON CONFLICT(id) DO NOTHING"), {"id": 3 + i % 10})
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(upsert, range(20)))
        barrier = threading.Barrier(2)
        def conflict(first):
            try:
                with probe.engine.begin() as db:
                    db.execute(text("SET LOCAL statement_timeout='10s'"))
                    db.execute(text(f"UPDATE {table} SET value=value+1 WHERE id=:id"), {"id": first})
                    barrier.wait(timeout=5)
                    db.execute(text(f"UPDATE {table} SET value=value+1 WHERE id=:id"), {"id": 3 - first})
                return "COMMITTED"
            except OperationalError as error:
                if getattr(error.orig, "sqlstate", None) != "40P01":
                    raise
                return "DEADLOCK_ROLLED_BACK"
        with ThreadPoolExecutor(max_workers=2) as pool:
            states = sorted(pool.map(conflict, (1, 2)))
        assert states == ["COMMITTED", "DEADLOCK_ROLLED_BACK"]
        with probe.engine.connect() as db:
            assert db.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 12
            assert db.execute(text(f"SELECT sum(value) FROM {table} WHERE id IN (1,2)")).scalar_one() == 2
        return {"concurrent_upsert_requests": 20, "unique_inserted_keys": 10,
                "duplicate_retry_idempotent": True, "deadlock_detected_sqlstate": "40P01",
                "deadlock_transaction_rollback": True, "successful_transaction_atomic": True}
    finally:
        with probe.engine.begin() as db:
            db.execute(text(f"DROP TABLE {table}"))


def neon():
    values = dotenv_values(ROOT / ".env.qa")
    assert values.get("MDM_DATABASE_PROFILE") == "neon" and values.get("MDM_DATABASE_URL")
    engine, _ = create_database(values["MDM_DATABASE_URL"])
    try:
        started = time.monotonic()
        with engine.connect() as db:
            assert db.execute(text("SELECT 1")).scalar_one() == 1
        first = time.monotonic() - started
        def query(_):
            started = time.monotonic()
            with engine.connect() as db:
                assert db.execute(text("SELECT 1")).scalar_one() == 1
            return (time.monotonic() - started) * 1000
        with ThreadPoolExecutor(max_workers=4) as pool:
            samples = list(pool.map(query, range(8)))
        assert engine.pool.checkedout() == 0
        with engine.connect() as db:
            head = db.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            isolation = db.execute(text("SHOW transaction_isolation")).scalar_one()
        return {"read_only": True, "concurrency": 4, "queries": 9, "first_connect_seconds": first,
                "query_ms_min_median_max": [min(samples), statistics.median(samples), max(samples)],
                "pool_checkedout_after_queries": 0, "schema_head": head, "transaction_isolation": isolation,
                "cold_serverless_wakeup_claimed": False}
    finally:
        engine.dispose()


try:
    probe.check("local_concurrent_upsert_atomic_deadlock_rollback", local)
    probe.check("real_neon_bounded_pool_concurrency", neon)
finally:
    probe.save()
    probe.client.close()
    probe.engine.dispose()
raise SystemExit(int(any(case["status"] != "PASS" for case in probe.results)))
