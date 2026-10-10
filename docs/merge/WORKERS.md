# Independent resolution worker

2026-10-08. ResolutionTask is the durable DB queue; no message broker or new
dependency. FastAPI creates jobs, reads persisted progress, and requests cancel.
Its lifespan neither starts execution nor resets job ownership. The existing
start/task DTOs and frontend remain unchanged. An additive authenticated/CSRF
protected `POST /api/v1/mdm/runs/{run_id}/cancel` returns the existing task DTO.

## Start and operate

Use the same database configuration and installed backend package for API and
worker. From the repository root, with `backend/src` on PYTHONPATH when using
the source checkout:

```powershell
.\.venv\Scripts\python.exe -m masterone.mdm.migrate
.\.venv\Scripts\python.exe -m masterone.mdm.resolution_worker
```

The worker requires current schema `0025_pipeline_checkpoints` and does not
migrate it itself. Ownership was introduced in `0024_job_ownership`; checkpoint
recovery is additive and defaults OFF; see [CHECKPOINTS](CHECKPOINTS.md).
Options: `--worker-id`, `--lease-seconds` (default 30, minimum 3),
`--timeout-seconds` (default 3600), `--once` (at most one job).
Default worker ID is hostname plus UUID. Multiple worker processes may run
against one DB. Each supervisor starts one spawned execution child with its own
DB engine; pooled API connections and pipeline results are never inherited.
SIGINT/SIGTERM stops the supervisor and terminates its child.

Compose includes a separate `worker` service, after the API health check (the
existing API startup performs migration). Scale with
`docker compose up -d --scale worker=2`. Worker has no HTTP port/probe.
Production schema changes should be performed as a coordinated deployment.

## Ownership and publication

- Claim uses a conditional QUEUED-to-RUNNING UPDATE and inserts a fresh UUID
  attempt in the same transaction. Job retains its task/run IDs across retries.
  PostgreSQL selects run locks with SKIP LOCKED; SQLite uses a writer reservation
  and conditional update. A competing claimant cannot also win the same job.
- Job attempt stores run/task/worker IDs, start, heartbeat, lease, deadline,
  terminal status, finish time and safe error code. DB time is authoritative.
  Heartbeat runs independently of compute and updates only the attempt row,
  conditional on matching current ownership, RUNNING state, no cancel, and an
  unexpired lease/deadline. It cannot renew past the deadline.
- Progress, engine selection, staging, failure and publication validate the
  current owner under the shared run lock. Generation attempt ID must equal
  the job attempt ID. Ownership is checked again after partition writes and
  before successful publication. An expired/superseded owner cannot commit.
- Serving reconciliation, active generation pointer, run/task completion and
  successful job/generation attempts commit together. Lost acknowledgment after
  commit leaves COMPLETED; it does not enqueue another publication. Legacy
  synchronous tests/callers are allowed only for jobs without a claimed attempt.

## Failure, cancellation and recovery

Supervisor detects a crashed execution child and retires its owned attempt.
If the supervisor itself dies, heartbeat stops; another polling worker reaps
the expired lease. Recovery never resets a live owner on API/worker startup.
PostgreSQL recovery skips locked run rows and revisits them on the next poll.
Publication checks its deadline before committing; heartbeat can continue
while that run lock is held. SQLite serializes DB writers and does not provide
the same concurrent publication/heartbeat behavior.

Automatic retries are limited to three attempts by default (`max_attempts` on
the task). Failure, lease expiry or timeout retires the attempt with an error
code and queues another attempt until the budget is exhausted; then status is
RETRYABLE_FAILED. Existing `/start` permits an explicit new retry budget while
retaining attempt history and saved reviewer decisions/golden/audit data.
Retries reuse durable generation receipts; compute may repeat.

Queued cancellation is immediately CANCELLED. Running cancellation persists
the request; heartbeat/publication rejects that owner, the supervisor kills
the child, and recovery records CANCELLED. Cancel and publication serialize on
the run lock: a completed publication wins if it commits first. Cancel does
not undo previously committed serving data. The job deadline is per attempt;
the supervisor terminates compute and recovery records JOB_TIMEOUT. Killing a
child cannot undo committed DB transactions; ownership fencing prevents later
serving writes from an orphaned execution.

A DB outage causes polling to retry; a child without a renewable lease is
terminated, and durable expiry recovery resumes when the DB returns. No API
memory stores stage results. Worker input/preservation/proposal memory remains
as described in GENERATIONS.md; this is not a fully bounded compute engine.

## Migration and verification limits

Migration 0024 adds queue ownership columns/index and job attempt history.
Existing interrupted RUNNING jobs without attempts are requeued once by the
worker. Downgrade retains ownership columns/history; re-upgrade preserves them.
Stop external workers before runtime rollback. The older API embeds execution
and cannot honor these fences: do not run it concurrently with new workers.

Tests use disposable SQLite, real spawned worker processes and mocked object
storage. They cover concurrent claims, crash/lease recovery, timeout retries,
cancel, API restart, attempt mismatch, expired publication rollback, lost commit
acknowledgment, CLI and migration history. PostgreSQL locking/heartbeat under a
long publication and real R2 durability require isolated rehearsal; Docker
daemon was unavailable in this session. Exact reports are in [STATUS](STATUS.md).
