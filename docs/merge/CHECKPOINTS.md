# Safe pipeline checkpoints (2026-10-09)

`PIPELINE_RESUME_ENABLED=false` is an independent opt-in. Saved engine profiles
pin the flag; a missing flag means OFF. The API/frontend and reviewer DTOs are
unchanged. Resume selects the existing private generation publication gate even
when `GENERATION_STAGING_ENABLED` is OFF. No message broker or dependency added.

## Identity and commit protocol

`mdm/pipeline/checkpoints.py` defines strict `p119_compute_checkpoint_v1`
manifests. The DB is the authoritative commit index, not a scratch directory,
remote object existence or a local upload receipt alone.

| Pin | Meaning |
| --- | --- |
| snapshot ID | Run ID, immutable record fingerprint and canonical captured payload hash |
| config hash | Policy/config, code/resource hashes, saved feature flags, resource limits, reviewer fingerprint, negative/veto edges, normalization mode and object backend |
| model hash | Rule scorer/normalization/constraints/evidence/policy/decision implementation hashes |
| reference version | `p119_onemaster_boundaries_v1`; explicit boundary contract version |
| stage version | Stage name and checkpoint/coordinator source hashes |
| input/output artifact hashes | Content identity, canonical partition ID order, schema, SHA-256, rows and byte counts |
| run/attempt | Origin ownership plus immutable native artifact/partition identities |
| dependencies | Parent checkpoint IDs and their committed manifest hashes |

States: `LOCAL_COMPLETE` → `DURABLE_COMPLETE`; invalid entries become `INVALID`.
GC retires entries to `GC_PENDING` before deleting bytes, then `DELETED`.
Local means closed, validated immutable Parquet. Durable means verified uploaded
partition/receipt plus a committed DB checkpoint. A stage commits only after all
its partitions, the complete uploaded native manifest and upstream barriers.
Empty stages have a validated zero-row partition bound to its snapshot or current
completed upstream input hash. Manifest size is capped at
8 MiB; existing native limits and queue/disk backpressure remain in force.

Commits acquire the run lock and validate captured job ownership before and
after the transaction. Duplicate acknowledgment of identical content is a no-op;
conflicting acknowledgment is rejected and a durable entry cannot downgrade.
Uploader callbacks retain the worker claim explicitly across thread boundaries.

Before reuse, validate manifest version/hash/index fields, all pins, dependency
edges and parent manifest hashes recursively, complete stage coverage and actual
upstream input hashes. Verify remote file size/SHA-256, exact receipt, Parquet
footer/schema/row count and complete artifact manifest. CLUSTER also binds its
SCORE reference to the dependency graph. Corruption invalidates the checkpoint;
stale/local/partial entries recompute. Transient remote/DB errors fail the attempt
and remain retryable, rather than silently accepting incomplete data.

## Resume boundaries

PREPARE and NORMALIZE reuse validated partitions. Full normalized snapshot and
corpus census still precede BUILD. A complete BUILD checkpoint avoids rebuilding
candidate indexes; SCORE can reuse individual durable partitions even if the
worker died before stage completion. Pair-local DECIDE prep can resume, but
guarded union still waits for complete score coverage and all global barriers.
A complete CLUSTER checkpoint restores the computed result and reconstructs a
fresh canonical score artifact without rescoring. SEALED materialization validates
existing private receipts and promotes directly; partial materialization reloads
through the existing idempotent partition receipts.

Reused partition bytes are copied into a fresh current-attempt manifest/key, not
appended to an earlier attempt. Origin checkpoints remain immutable history.
Canonical order controls output; completion order never controls decisions.
Promotion independently rechecks source/reviewer fingerprints, generation fence,
durable receipts and job ownership. Cancel or stale ownership cannot move the
active pointer. Existing persistence keeps reviewer decisions, golden overrides,
negative edges and audit/history, with unique logical serving rows.

Rule execution without global semantic enrichment supports compute resume in
`_compute` and the DB-backed StageRunner worker path. Splink/semantic execution
keeps explicit serial eligibility fallback. Generation receipt recovery is
independent. With resume OFF, the existing serial/streaming behavior remains.

Configured R2 uses the existing object adapter. Without R2, bounded immutable
partition objects are stored in private `mdm_checkpoint_objects` BLOB rows, never
in API memory. One object is bounded by the partition/manifest limit; this is
durable artifact storage, not a second job queue. Runtime remote errors do not
switch backends. Operational retention/capacity needs deployment tuning.

## Retention, logging and rollout

Checkpoint directories are pinned before production. Generic cleanup refuses
the pin. Explicit `collect_checkpoint(..., local_root=...)` checks the recorded
root, native file leases, DB dependencies, active jobs and serving generations.
It retains lock files and tombstones; shared referenced remote keys are retained.
Deletion failure or crash after retirement can safely retry `GC_PENDING`, which
resume never consumes. GC is deliberately conservative: active generation blocks
collection for its run. There is no automatic GC scheduler or orphan-directory
cleanup; interrupted files without a DB index require an operator retention pass.

Logger `masterone.mdm.pipeline.checkpoints` emits `checkpoint_resolution` with
run, attempt, stage, partition, origin attempt and safe reason codes, including
`RESUME`, `RECOMPUTE`, `RECOMPUTE_STALE_OR_INCOMPLETE`, corruption/input reasons.
Source payloads and credentials are excluded.

Migration `0025_pipeline_checkpoints` follows `0024_job_ownership`; adds private
objects, checkpoint index and dependency tables. Stop workers before migration
or rollback. Downgrade retains durable private history; re-upgrade is idempotent.
Older code keeps resume OFF. Deploy migrations explicitly; workers never migrate
production databases themselves.

Tests: `test_checkpoint_resume.py`, isolated runner `--suite resume`. Real spawned
workers are killed at six compute boundaries and the materialization seal, then
lease recovery claims a new attempt. Tests include partial SCORE recovery, R2
fault simulation, SQL transaction failure, manifest/partition corruption, stale
pins, duplicate ack, cancel/reviewer preservation, GC leases/tombstone recovery,
empty snapshots and all-boundary overlap. See STATUS for executed reports.
No live R2/PostgreSQL/LLM was used. PostgreSQL concurrency, POSIX filesystem,
representative scale/shadow and real outage rehearsal remain rollout gates.

This extends P-119's sessions 11–13 modules and preserves their boundary contracts;
it does not replace the complete coordinator or remove global worker memory state.
