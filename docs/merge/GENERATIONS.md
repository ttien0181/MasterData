# Generation staging and atomic publication

Contract `p119_generation_v1`; migration `0022_generation_staging` after
`0021_input_contract`. Read with [rules](AGENT_RULES.md), [decision contract](DECISIONS.md)
and [clustering contract](CLUSTERING.md). No frontend/API DTO changes.

## Identity and ownership

`GENERATION_STAGING_ENABLED=false` by default. Enable after isolated PostgreSQL
rehearsal and shadow validation. Small pipeline, large StageRunner orchestration
and direct StageRunner publication use the same publisher. Once a run has a
publication row it keeps this gate even if the global flag is disabled; disabling
the flag only selects the legacy path for runs without publication state.

Generation ID is UUIDv5 over run ID, sorted imported-snapshot SHA-256, saved human
state SHA-256 and pinned config/code/storage identity. The same input/config/state
has the same generation across retries. Config pins policy YAML, pipeline source,
scoring/candidate config, semantics/cluster flags, semantic settings, partition size
and backend/bucket; no credentials. Attempt ID is a separate UUIDv4 for each
pipeline invocation, not the reusable task ID. Direct StageRunner uses its explicit
attempt ID and must not substitute other input for imported record identities.

A run-row lock fences attempts: a new attempt supersedes its previous RUNNING
attempt. All loads/seal/promote check the current attempt; stale failure cannot
mark the newer attempt failed. PostgreSQL uses FOR UPDATE; SQLite uses BEGIN
IMMEDIATE before reading the publication fence. This is publication fencing,
not a distributed compute lease or a complete multi-process worker claim protocol.

## Additive schema and lifecycle

| Table | Purpose / constraints |
| --- | --- |
| `mdm_resolution_generations` | STAGING -> SEALED -> PROMOTED; unique run/identity, snapshot/config/review hashes, expected counts, manifest hash, base active generation and timestamps |
| `mdm_resolution_generation_attempts` | Separate RUNNING/SUPERSEDED/FAILED/SUCCEEDED attempts, generation FK, safe failure code |
| `mdm_resolution_publications` | One row per run; active-generation/current-attempt FKs, changed under run lock. Separate from legacy ResolutionRun columns |
| `mdm_resolution_staged_results` | Private typed projections for pair/proposal/membership/golden/review; PK generation/kind/key, pair endpoint uniqueness/order, membership uniqueness, record FKs, payload/hash/partition index |
| `mdm_resolution_partition_receipts` | PK generation/kind/partition; attempt FK, row/byte counts, SHA-256, DB checkpoint bytes or object key; immutable content on retry |

No API reads staging. Default batches have 512 rows (configured 1..2048). Each
batch commits private rows, checkpoint and receipt together. Existing receipts
must match content/count exactly; duplicate logical rows across partitions fail.
Retry resumes the same generation and validates already loaded partitions.
Seal records the declared counts and a durable final manifest, including empty
output kinds. A crash leaves STAGING/SEALED data private, with previous serving
rows and active pointer unchanged. A stale/changed input creates a different
identity; a SEALED conflicting manifest is never silently repaired.

With R2 configured, partition JSON and manifest use generation/kind/index/hash
keys. Upload and read-back checksum acknowledgment precede the DB receipt.
Promotion checks the remote bytes again; upload/missing/corrupt object failure
blocks publication. With object storage disabled, receipt bytes are a durable DB
checkpoint. Local scoring Parquet/scratch is never accepted as durable evidence.
Imported source/normalized snapshot is already durable in DB and is rehashed
before publication. These result checkpoints do not implement PREPARE/SCORE
resume or archive every scored insufficient-evidence row.

## Validation and promote

Before any serving mutation, under the run lock:

- Require SEALED state, expected row counts for every kind, and candidate count
  equal to persisted pair count plus declared insufficient count.
- Check contiguous partitions, receipt counts/hashes, artifact bytes/checksums,
  attempt/generation references and final manifest identity/counts/metrics.
- Rehash every staged payload; validate typed keys, canonical pair orientation,
  unique identities and source records belonging to this run.
- Require disjoint proposal membership, declared membership equality, every
  membership referencing a proposal and every proposal having exactly one golden.
  Golden member coverage/provenance and proposal edge/review pair references
  must be internally consistent; review projections include the full schema.
- Recheck imported input and reviewer fingerprints and the base active pointer.
  Changed input/review, stale base/attempt, absent receipts or artifacts fail closed.

Promote uses one DB transaction for serving reconciliation, generation state,
active pointer, attempt acknowledgment, task/run completion and counts. A flushed
but uncommitted transaction remains invisible to another reader. Failure rolls
back these writes. After an uncertain commit acknowledgment, a fresh DB read of
PROMOTED/SUCCEEDED is authoritative; failure handling cannot demote that result.
Replay is idempotent and does not recompute a committed pipeline generation.

Serving reconciliation deliberately retains the existing insert-only P-119
contract: canonical saved pairs, all saved proposals/cases, reviewer revisions,
confirmed decisions, golden overrides, audit, entities and export history remain
unchanged. New proposals must be disjoint from reserved saved/reviewer members.
Active generation identifies the last complete, reconciled publication, not an
instruction to replace all older algorithm rows. Full destructive recomputation
requires a separate reconciliation/domain contract. Existing legacy serving data
has no fabricated generation/backfill; first publication retains it.

Validation/promotion read staged rows by bounded/keyset batches. Manifest metadata
uses O(partitions) memory. Existing preservation reconciliation still loads serving
identity maps; input/candidates/proposals and optional semantic enrichment remain
in memory. No claim of a fully bounded worker or production throughput.

## Migration / rollback strategy

1. Back up an isolated copy first. Upgrade with the existing Alembic runner to
   `0022_generation_staging`; this adds five tables, leaves run/serving columns
   and all existing IDs/JSON/reviewer/history unchanged. No application lifespan
   or auto-upgrade was run against enterprise DB in this task.
2. Keep the flag OFF while rehearsing PostgreSQL constraints/locking/rollback and
   shadow comparisons. The tests use disposable SQLite with foreign keys ON,
   mocked object storage and real DuckDB/Parquet; they do not prove Neon/R2 behavior.
3. To roll back runtime, drain/stop workers and disable the flag. Existing protected
   runs in this version retain the publication gate. Older runtime may read/review
   existing serving data; do not rerun protected runs with an older publisher.
4. Alembic downgrade to 0021 intentionally retains the five additive tables,
   checkpoints and pointers as archives. Re-upgrade reuses them without loss.
   No destructive drop/backfill or automatic cleanup. TTL/garbage collection,
   object-store orphan cleanup and storage budgets require separate retention
   policy, backups and concurrency checks.

## Reference provenance and executed tests

Read-only OneMaster working-tree references:

- `backend/app/worker/generation_staging.py`: SHA-256
  `13C3FF1A320BEB91A143688A65DA861AE42CE29DA30D0A7300B1E865FFFC1245`.
- `backend/app/worker/materialize.py`: SHA-256
  `3D4AA22E41E6BC32D79FD9A466DB108FEAC6FF2F71A42BCC69E859C3A402962A`.

Adapted private staging, checksums, references and atomic publication to P-119
models and preservation contract. No external runtime import or new dependency.
Source license/distribution review remains as recorded in PROJECT_MAP.md.
See [STATUS](STATUS.md) for exact commands/reports, 33 new failure/preservation/
reader-isolation/migration cases and unchanged baseline failures.
