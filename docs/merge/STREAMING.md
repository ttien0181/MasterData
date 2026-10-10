# Controlled stage streaming (2026-10-08)

Opt-in worker execution; API/frontend DTOs, policies, candidate coverage, corpus
frequencies, guarded union and publication fences retain the P-119 contracts.
No broker or new dependency. This adapts boundaries, not OneMaster algorithms.

## Boundaries

| Boundary | Permitted overlap | Global gate |
| --- | --- | --- |
| PREPARE → NORMALIZE | Closed prepared references feed private pass-one partitions | Entire input is captured and every pass-one consumer finishes before normalized completion and full snapshot census |
| NORMALIZE → BUILD | Upload normalized references while the BUILD consumer indexes them | Admission begins only after normalized manifest is complete; one BUILD consumer, then global finalize |
| BUILD → SCORE | Emit canonical candidate references to parallel rule scorers | CandidateEngine finalizes global union/deduplication before the first score partition |
| SCORE → DECIDE | Prepare pair-local evidence per score reference | All score coverage/identity/census checks, consumers and verified uploads finish before DecisionStore sealing/global guarded union |
| DECIDE → MATERIALIZE | Private staged partition loads overlap native uploads | Global clustering finishes first; all native uploads and DB partition receipts finish before generation seal; promote independently revalidates all receipts/ownership/input/reviewer state |

Pass one retains imported normalized fields. The StageRunner path standardizes
only records lacking them, with errors propagated. P-119 has no additional global
normalization transform: its scoring census computes contact frequency over the
whole normalized snapshot. Census never runs on a partial snapshot.

Publication retains the existing transactional generation gate. Materialization
finishes each result kind before starting the next; this preserves dependencies
between pair/proposal/membership/golden/review. Consumer threads inherit frozen
engine and worker ownership ContextVars. No API execution or memory queue added.

## Configuration / fallback

All five flags default `false`:

```
STREAM_PREPARE_NORMALIZE_ENABLED
STREAM_NORMALIZE_BUILD_ENABLED
STREAM_BUILD_SCORE_ENABLED
STREAM_SCORE_DECIDE_ENABLED
STREAM_DECIDE_MATERIALIZE_ENABLED
```

They are independent. Disabled PREPARE/BUILD/SCORE boundaries wait for complete
upstream artifacts before consuming. NORMALIZE/BUILD remains a complete snapshot
boundary even when enabled. Engine-v2 never automatically enables streaming.
New selections save explicit enabled flags; missing flags in old saved profiles
pin OFF. Deployment flag changes do not silently change an existing run profile.
Use a new run for shadow/rollback testing of another profile.

Limits: `STREAM_PARTITION_ROWS=512` (max 2048),
`STREAM_PARTITION_BYTES=16777216`, `STREAM_QUEUE_PARTITIONS=4`,
`STREAM_QUEUE_BYTES=67108864`, `STREAM_CONSUMER_WORKERS=2`,
`STREAM_UPLOAD_WORKERS=2`, `STREAM_DISK_BYTES=2147483648`,
`STREAM_TIMEOUT_SECONDS=60`. Upload and consumer queues each bound queued +
active work, and reserve before producing a file. The consumer queue passes only
PartitionReference metadata and retains a lease from admission through completion.
Disk budget counts retained partitions and metadata; canonical score assembly
uses a capped sink plus conservative coverage-ledger admission. Materialization
reuses the computation workspace/budget when streaming computed it.

With resume OFF, no configured R2 store → `STREAMING_STORE_UNAVAILABLE`, Splink →
`STREAMING_SPLINK_SERIAL`, global semantic enrichment →
`STREAMING_SEMANTIC_SERIAL`, no flags → `STREAMING_DISABLED`. These eligibility
fallbacks occur before streaming side effects and use the original serial path.
Runtime upload/consumer/coverage errors fail the attempt; they never silently
rerun partially completed work or publish partial results. DB-backed generation
staging still works serially without R2. MATERIALIZE opt-in selects generation
staging even when its general flag is off. Separate opt-in compute resume can use
bounded immutable DB objects without R2; see [CHECKPOINTS](CHECKPOINTS.md).

Canonical candidate order and zero-padded partition IDs define traversal, not
thread completion or manifest append order. Global ScoringEngine validations
remain shared by both paths. Empty artifacts get a zero-row Parquet partition,
so completion still has a schema/footer/checksum and durable receipt.

Job supervisor retains cancellation/deadline/heartbeat/recovery responsibility.
A timed-out thread retains its lease until actual return; the worker child
process is terminated on cancellation/deadline/ownership loss. Private artifacts
are attempt-isolated. New job attempts use fresh scratch paths and IDs, while
generation checkpoints remain independently validated by the publisher.

## Scope / rollout

This bounds transport and disk writes, not all computation. Normalized records,
global candidate indexes and clustering still use worker memory. Serial fallback
has its existing resource behavior. Streaming alone retries in a fresh attempt.
Separate default-OFF compute checkpoint recovery adds verified stage/partition
reuse and conservative explicit GC; [CHECKPOINTS](CHECKPOINTS.md) describes its
scope and retention limitations. Generation checkpoints retain existing idempotent recovery. Metadata manifests
retain the artifact module's 8192 partition/8 MiB limits and fail closed on excess.

Tests use isolated SQLite and LocalObjectStore, no live R2/PostgreSQL/LLM. Before
enabling a production profile: representative shadow equivalence, R2 outage and
resource rehearsal, PostgreSQL concurrency, POSIX leases/fsync and capacity tuning.
Passing local equivalence does not automatically change flag defaults.

## Provenance

Read-only `onemaster-workspace/backend/app/worker/` references; SHA-256:

| File | SHA-256 |
| --- | --- |
| prepare_normalize_stream.py | `448754B1328B8D66954D9E83AFB2244E5F9C40EEEE9EBDB2B777F7DFBC9663F0` |
| build_score_stream.py | `95692558C27300C241A1D3C5293B2758D30AF0984D0D6EF0902BC3A6CDC268A4` |
| score_decide_prep.py | `BE4A4191B89CAA90A9CF48CCB6A67C98F5FC3FBA720A6AB4E970AB1158C15AFB` |
| decide_materialize_stream.py | `59B97D87381CCDFC968E09EA08E13A6FA2C8EF8CBCF27470C0A877B22D4D7BEE` |

Selective implementation in P-119, no wholesale source copy or external runtime
imports. Reference tree has no discovered license file; external distribution
requires owner license review. See STATUS.md for executed tests and remaining gates.
