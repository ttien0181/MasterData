"""DB queue with run locks and attempt fences shared by all serving writes."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, or_, select, text, update

from masterone.db.base import now_utc
from masterone.mdm.generation_staging import lock_run
from masterone.mdm.models import ResolutionJobAttempt as Attempt
from masterone.mdm.models import ResolutionRun, ResolutionTask

_owner = ContextVar("resolution_job_owner", default=None)


class OwnershipLostError(RuntimeError):
    pass


@dataclass(frozen=True)
class Claim:
    run_id: str
    task_id: str
    attempt_id: str
    worker_id: str


@contextmanager
def ownership(claim):
    token = _owner.set(claim)
    try:
        yield
    finally:
        _owner.reset(token)


def current_claim():
    return _owner.get()


def utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def database_now(db):
    # Lease authority is the database clock, independent of worker clock skew.
    if db.get_bind().dialect.name == "postgresql":
        return utc(db.scalar(select(func.clock_timestamp())))
    value = db.scalar(select(func.strftime("%Y-%m-%d %H:%M:%f", "now")))
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


def validate(db, claim):
    lock_run(db, claim.run_id)
    task = db.query(ResolutionTask).filter_by(id=claim.task_id).populate_existing().one()
    attempt = db.get(Attempt, claim.attempt_id, populate_existing=True)
    now = database_now(db)
    if (task.run_id != claim.run_id or task.attempt_id != claim.attempt_id or task.status != "RUNNING"
            or attempt is None or attempt.task_id != claim.task_id or attempt.run_id != claim.run_id
            or attempt.worker_id != claim.worker_id or attempt.status != "RUNNING"
            or utc(attempt.lease_expires_at) <= now or utc(attempt.deadline_at) <= now
            or task.cancel_requested):
        raise OwnershipLostError("JOB_OWNER_FENCED")
    return task, attempt


def guard(db, run_id, *, attempt_id=None):
    claim = current_claim()
    if claim is not None:
        if claim.run_id != run_id:
            raise OwnershipLostError("JOB_RUN_MISMATCH")
        if attempt_id is not None and claim.attempt_id != attempt_id:
            raise OwnershipLostError("JOB_ATTEMPT_MISMATCH")
        return validate(db, claim)
    # Legacy synchronous callers remain supported only for unclaimed tasks.
    lock_run(db, run_id)
    task = db.query(ResolutionTask).filter_by(run_id=run_id).one_or_none()
    if task is not None and task.attempt_id is not None:
        raise OwnershipLostError("JOB_OWNER_REQUIRED")
    return None


def completed(db, run_id):
    claim = current_claim()
    if claim is not None:
        _, attempt = validate(db, claim)
        attempt.status = "SUCCEEDED"
        attempt.finished_at = database_now(db)


def claim_next(factory, worker_id, *, lease_seconds=30, timeout_seconds=3600):
    if lease_seconds <= 0 or timeout_seconds <= 0:
        raise ValueError("Lease and timeout must be positive")
    with factory.begin() as db:
        query = (select(ResolutionTask.id, ResolutionTask.run_id)
            .join(ResolutionRun, ResolutionRun.id == ResolutionTask.run_id)
            .where(ResolutionTask.status == "QUEUED", ResolutionTask.cancel_requested.is_(False))
            .order_by(ResolutionTask.created_at, ResolutionTask.id).limit(1))
        if db.get_bind().dialect.name == "postgresql":
            query = query.with_for_update(of=ResolutionRun, skip_locked=True)
        candidate = db.execute(query).first()
        if candidate is None:
            return None
        lock_run(db, candidate.run_id)
        # CAS is the claim, not the preceding candidate SELECT.
        result = db.execute(update(ResolutionTask).where(ResolutionTask.id == candidate.id,
            ResolutionTask.status == "QUEUED", ResolutionTask.cancel_requested.is_(False))
            .values(status="RUNNING", phase="loading", updated_at=now_utc()))
        if result.rowcount != 1:
            return None
        task = db.get(ResolutionTask, candidate.id, populate_existing=True)
        now = database_now(db)
        attempt_id = str(uuid4())
        task.attempt_id = attempt_id
        task.error_code = None
        task.processed_count = 0
        task.attempt_count += 1
        db.add(Attempt(id=attempt_id, task_id=task.id, run_id=task.run_id, worker_id=worker_id,
            started_at=now, heartbeat_at=now, lease_expires_at=now + timedelta(seconds=lease_seconds),
            deadline_at=now + timedelta(seconds=timeout_seconds)))
        run = db.get(ResolutionRun, task.run_id)
        run.status, run.phase = "RUNNING", "loading"
        return Claim(task.run_id, task.id, attempt_id, worker_id)


def heartbeat(factory, claim, lease_seconds=30):
    if lease_seconds <= 0:
        raise ValueError("Lease must be positive")
    with factory.begin() as db:
        if db.get_bind().dialect.name == "postgresql":
            db.execute(text("SET LOCAL lock_timeout = '1s'"))
            db.execute(text("SET LOCAL statement_timeout = '2s'"))
        # Renew only the attempt row. Publication holds the run row, and must
        # still be able to receive heartbeats until its final ownership check.
        now = database_now(db)
        active_task = select(ResolutionTask.id).where(
            ResolutionTask.id == claim.task_id, ResolutionTask.run_id == claim.run_id,
            ResolutionTask.attempt_id == claim.attempt_id, ResolutionTask.status == "RUNNING",
            ResolutionTask.cancel_requested.is_(False)).exists()
        deadline = db.scalar(select(Attempt.deadline_at).where(Attempt.id == claim.attempt_id))
        if deadline is None:
            raise OwnershipLostError("JOB_OWNER_FENCED")
        result = db.execute(update(Attempt).where(
            Attempt.id == claim.attempt_id, Attempt.task_id == claim.task_id,
            Attempt.run_id == claim.run_id, Attempt.worker_id == claim.worker_id,
            Attempt.status == "RUNNING", Attempt.lease_expires_at > now,
            Attempt.deadline_at > now, active_task).values(
                heartbeat_at=now,
                lease_expires_at=min(now + timedelta(seconds=lease_seconds), utc(deadline))))
        if result.rowcount != 1:
            raise OwnershipLostError("JOB_OWNER_FENCED")


def progress(factory, claim, phase, processed, total):
    with factory.begin() as db:
        task, _ = validate(db, claim)
        task.phase, task.processed_count, task.total_count = phase, processed, total
        task.updated_at = now_utc()
        db.get(ResolutionRun, claim.run_id).phase = phase


def _retire(db, task, attempt, code):
    now = database_now(db)
    attempt.status = "CANCELLED" if task.cancel_requested else "FAILED"
    attempt.finished_at, attempt.error_code = now, code
    task.status = ("CANCELLED" if task.cancel_requested else
                   "QUEUED" if task.attempt_count < task.max_attempts else "RETRYABLE_FAILED")
    task.phase, task.error_code, task.updated_at = task.status.lower(), code, now
    run = db.get(ResolutionRun, task.run_id)
    run.status, run.phase = task.status, task.phase
    # Generation ownership also ends, retaining all receipts for deterministic replay.
    from masterone.mdm.models import ResolutionGenerationAttempt
    generation_attempt = db.get(ResolutionGenerationAttempt, attempt.id)
    if generation_attempt is not None and generation_attempt.status == "RUNNING":
        generation_attempt.status, generation_attempt.error_code = "FAILED", code
        generation_attempt.finished_at = now


def failed(factory, claim, code="PIPELINE_FAILED"):
    with factory.begin() as db:
        task, attempt = validate(db, claim)
        _retire(db, task, attempt, code)


def recover(factory):
    # Startup never resets live owners. Only expired/cancelled attempts are retired.
    with factory() as db:
        now = database_now(db)
        candidates = db.execute(select(ResolutionTask.id, ResolutionTask.run_id)
            .outerjoin(Attempt, Attempt.id == ResolutionTask.attempt_id)
            .where(ResolutionTask.status == "RUNNING", or_(
                ResolutionTask.cancel_requested.is_(True), Attempt.id.is_(None),
                Attempt.lease_expires_at <= now, Attempt.deadline_at <= now))).all()
    for task_id, run_id in candidates:
        with factory.begin() as db:
            if db.get_bind().dialect.name == "postgresql":
                # A live publication is responsible for its final deadline
                # check. Reap other runs without waiting behind that writer.
                run = db.scalar(select(ResolutionRun).where(ResolutionRun.id == run_id)
                    .with_for_update(skip_locked=True))
                if run is None:
                    continue
            lock_run(db, run_id)
            task = db.get(ResolutionTask, task_id, populate_existing=True)
            if task.status != "RUNNING":
                continue
            attempt = db.get(Attempt, task.attempt_id) if task.attempt_id else None
            if attempt is None:
                # Pre-migration interrupted jobs have no owner; one durable recovery.
                task.status, task.phase, task.error_code = "QUEUED", "queued", "LEGACY_INTERRUPTED"
                run = db.get(ResolutionRun, run_id)
                run.status, run.phase = task.status, task.phase
                continue
            now = database_now(db)
            code = ("JOB_CANCELLED" if task.cancel_requested else
                    "JOB_TIMEOUT" if utc(attempt.deadline_at) <= now else
                    "LEASE_EXPIRED" if utc(attempt.lease_expires_at) <= now else None)
            if code:
                _retire(db, task, attempt, code)


def request_cancel(factory, run_id):
    with factory.begin() as db:
        run = lock_run(db, run_id)
        task = db.query(ResolutionTask).filter_by(run_id=run_id).one_or_none()
        if task is None:
            return None
        if task.status in {"QUEUED", "RUNNING"}:
            task.cancel_requested = True
            if task.status == "QUEUED":
                task.status = run.status = "CANCELLED"
                task.phase = run.phase = "cancelled"
                task.error_code = "JOB_CANCELLED"
            task.updated_at = now_utc()
        return task
