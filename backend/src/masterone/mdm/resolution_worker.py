"""Independent DB queue worker: python -m masterone.mdm.resolution_worker."""
import argparse
import logging
import multiprocessing
import re
import signal
import socket
import threading
import time
import traceback
from uuid import uuid4

from sqlalchemy import inspect, text

from masterone.auth import models as auth_models  # noqa: F401 -- register user FK in spawned workers
from masterone.db.session import create_database
from masterone.mdm.job_queue import (
    OwnershipLostError,
    claim_next,
    failed,
    heartbeat,
    ownership,
    progress,
    recover,
)

logger = logging.getLogger(__name__)


def operation_timeout(worker_seconds, config):
    """Provider run_seconds bounds encoding, not setup/scoring/publication.

    Hybrid profiles explicitly pin an end-to-end execution budget; legacy
    evidence profiles retain the worker deadline and the runtime's own API cap.
    """
    return min(worker_seconds, config['execution_budget']['run_seconds']) if config and config.get('execution_budget') else worker_seconds


def _safe_failure_code(error):
    # Expose fixed resource/service codes; arbitrary exception text can contain secrets.
    code = str(error)
    if isinstance(error, ValueError) and re.fullmatch(r'(?:EMBEDDING|ENCODER|RETRIEVAL|MATCHING|CANDIDATE)_[A-Z0-9_]{1,96}', code):
        return code
    return 'PIPELINE_FAILED'


def execute_attempt(database_url, claim):
    # Spawn, never inherit pooled connections or API memory across a fork.
    from masterone.logging_config import configure_logging
    from masterone.mdm.resolution_service import run_pipeline
    configure_logging()
    engine, factory = create_database(database_url)
    try:
        with ownership(claim):
            from masterone.mdm.models import ResolutionTask
            with factory() as db:
                operation_id = db.get(ResolutionTask, claim.task_id).operation_id
            def report(phase, done, total):
                return progress(factory, claim, phase, done, total)
            if operation_id:
                from masterone.mdm.workflow_jobs import execute_workflow
                execute_workflow(operation_id, factory, report)
            else:
                run_pipeline(claim.run_id, factory, report)
    except OwnershipLostError:
        pass
    except Exception as error:
        # Safe structured cause; SQL exception text can contain PII and credentials.
        frames = [f"{frame.name}:{frame.lineno}" for frame in traceback.extract_tb(error.__traceback__)]
        logger.error("ATTEMPT_FAILED attempt_id=%s error_type=%s frames=%s",
                     claim.attempt_id, type(error).__name__, ",".join(frames))
        try:
            failed(factory, claim, _safe_failure_code(error))
        except OwnershipLostError:
            pass
        except Exception as final_error:
            logger.error("ATTEMPT_FINALIZE_FAILED attempt_id=%s error_type=%s",
                         claim.attempt_id, type(final_error).__name__)
        raise SystemExit(1) from None
    finally:
        engine.dispose()


class ResolutionWorker:
    """One supervisor owns one child. Multiple supervisors share the DB queue."""

    def __init__(self, factory, *, worker_id=None, lease_seconds=30, timeout_seconds=3600,
                 poll_seconds=0.3):
        if lease_seconds < 3 or timeout_seconds <= 0 or poll_seconds <= 0:
            raise ValueError("Invalid worker timing")
        self.factory = factory
        self.worker_id = worker_id or f"{socket.gethostname()}:{uuid4()}"
        if len(self.worker_id) > 128:
            raise ValueError("worker_id is too long")
        self.lease_seconds, self.timeout_seconds = lease_seconds, timeout_seconds
        self.poll_seconds = poll_seconds
        self.stop_event = threading.Event()
        self.child = None
        self.thread = None

    def start(self):
        # Optional embedding by CLI/tests; FastAPI never instantiates this class.
        self.thread = threading.Thread(target=self.run_forever, name="resolution-supervisor", daemon=True)
        self.thread.start()

    def close(self):
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=self.lease_seconds + 5)

    def run_once(self):
        from masterone.mdm.heavy_jobs import heavy_job_slot
        # Acquire before claiming: a queued task must not lose its lease waiting for an import.
        with heavy_job_slot(blocking=False) as acquired:
            return self._run_once() if acquired else False

    def _run_once(self):
        recover(self.factory)
        claim = claim_next(self.factory, self.worker_id, lease_seconds=self.lease_seconds,
                           timeout_seconds=self.timeout_seconds)
        if claim is None:
            return False
        agents_budget = None
        config = None
        with self.factory() as db:
            from masterone.mdm.models import ResolutionRun, ResolutionTask
            from masterone.mdm.workflow_models import WorkflowOperation
            task = db.get(ResolutionTask, claim.task_id)
            op = db.get(WorkflowOperation, task.operation_id) if task.operation_id else None
            if op and op.payload.get('matching_binding_id'):
                from masterone.mdm.matching_agents.workflow_agents import config_for_operation
                config = config_for_operation(db, db.get(ResolutionRun, claim.run_id), op)
                if config:
                    agents_budget = config['resource_budget']
        url = self.factory.kw["bind"].url.render_as_string(hide_password=False)
        child = multiprocessing.get_context("spawn").Process(
            target=execute_attempt, args=(url, claim), name="resolution-attempt")
        self.child = child
        try:
            child.start()
            deadline = time.monotonic() + operation_timeout(self.timeout_seconds, config)
            budget_error = None
            while child.is_alive() and not self.stop_event.wait(min(self.lease_seconds / 3, 1)):
                if time.monotonic() >= deadline:
                    budget_error = 'MATCHING_WORKER_TIMEOUT' if agents_budget else None
                    break
                if agents_budget:
                    import psutil
                    try:
                        process = psutil.Process(child.pid)
                        rss = sum(p.memory_info().rss for p in [process] + process.children(recursive=True))
                        if rss > agents_budget['worker_rss_bytes']:
                            budget_error = 'MATCHING_WORKER_RSS_BUDGET'
                            break
                    except psutil.NoSuchProcess:
                        pass
                try:
                    heartbeat(self.factory, claim, self.lease_seconds)
                except OwnershipLostError:
                    break
            if child.is_alive():
                if agents_budget:
                    import psutil
                    try:
                        for process in psutil.Process(child.pid).children(recursive=True):
                            process.kill()
                    except psutil.NoSuchProcess:
                        pass
                child.terminate()
            child.join(timeout=5)
            if child.is_alive():
                child.kill()
                child.join()
            # Success is committed by the child, never inferred from exit code.
            recover(self.factory)
            try:
                failed(self.factory, claim, budget_error or ("WORKER_STOPPED" if self.stop_event.is_set() else "WORKER_CRASHED"))
            except OwnershipLostError:
                pass
        finally:
            if child.pid is not None and child.is_alive():
                child.terminate()
                child.join(timeout=5)
                if child.is_alive():
                    child.kill()
                    child.join()
            self.child = None
        return True

    def run_forever(self):
        while not self.stop_event.is_set():
            try:
                if not self.run_once():
                    self.stop_event.wait(self.poll_seconds)
            except Exception as error:
                logger.error("WORKER_POLL_FAILED worker_id=%s error_type=%s", self.worker_id, type(error).__name__)
                self.stop_event.wait(self.poll_seconds)


def main():
    from masterone.logging_config import configure_logging
    configure_logging()
    from masterone.config import get_settings
    from masterone.mdm.migrate import HEAD
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker-id")
    parser.add_argument("--lease-seconds", type=float, default=30)
    parser.add_argument("--timeout-seconds", type=float, default=3600)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    engine, factory = create_database(get_settings().active_mdm_database_url)
    try:
        # Migrate explicitly before deployment; worker never upgrades a live DB.
        if not inspect(engine).has_table("alembic_version"):
            raise RuntimeError("Run masterone.mdm.migrate before starting worker")
        with engine.connect() as db:
            if db.execute(text("SELECT version_num FROM alembic_version")).scalar() != HEAD:
                raise RuntimeError("Worker requires current database schema")
        worker = ResolutionWorker(factory, worker_id=args.worker_id,
            lease_seconds=args.lease_seconds, timeout_seconds=args.timeout_seconds)
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: worker.stop_event.set())
        if args.once:
            worker.run_once()
        else:
            worker.run_forever()
    finally:
        engine.dispose()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
