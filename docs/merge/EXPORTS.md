# Streaming export artifacts

P-119, 2026-10-09. `resolution_items.create_export` delegates to
`mdm/export_stream.py`; CSV remains the public preview/execute/download contract.

Records/proposals/pairs are read in deterministic ID order with keyset batches.
Temporary SQLite indexes keep ownership, review connected components and exact
coverage on disk. No complete record/pair/CSV list is constructed to write the file.
One proposal's source records and field options are loaded at a time, bounded by
`EXPORT_MAX_GROUP_RECORDS`; a larger group fails explicitly rather than truncating.
Review counts and pending flags retain the legacy connected-component definition.

CSV is written to a temporary artifact with UTF-8 BOM, CRLF, legacy quoting and
the exact field order:

`master_record_id,full_name,phone_number,email_address,raw_address,source_record_ids,source_systems,record_count,resolution_status,review_pending`

Merged rows precede unmerged rows, each section sorted by ID. Source systems and
external IDs retain legacy sorting/joining. Saved overrides win over submitted
source choices, then survivorship, then first non-empty raw member value. Original
values and leading zeroes are retained. `EXPORT_PARQUET_ENABLED=true` adds a
batched companion file: the same ten fields in the same order, all Arrow strings,
including textual count and lowercase boolean. Empty artifacts have valid schemas.
No new public format selector or frontend changes are required.

Before publication: reject missing/foreign/overlapping sources, enforce exact-once
coverage against the source table count, validate CSV header and the output-row
equation, close/fsync the CSV and close/validate Parquet footer/schema/row count.
SHA-256 and size are calculated with bounded chunks. R2 receives closed files with
one transfer worker, then the complete object is read back in chunks and verified.
Only after every artifact and the completion manifest succeed is the export/audit
row committed. Failure rolls back DB work and propagates a structured error type;
there is no best-effort upload or fallback after an R2 interruption.

Without configured R2, complete local objects live under `EXPORT_ARTIFACT_DIR` and
use `local:` storage references in the existing nullable `r2_key` column. This
directory must be durable/shared across API processes in deployments using local
storage. New exports keep `csv_bytes=NULL`; old DB-byte exports still download.
Create/execute responses stream the artifact with the same attachment filename and
`X-Export-Id`; R2 downloads retain their presigned redirect, local downloads stream.
Compare continues to sample the immutable historical artifact.

Run locks serialize export against review/golden mutations. Preview execution uses
the preview UUID as export ID and retains stale/expiry/replay checks. Direct export
accepts optional `Idempotency-Key` header (1–80 characters), namespaced by run and actor;
replay returns the same historical snapshot, changed choices/acknowledgement fail
409. Legacy direct calls without a request ID deliberately create new snapshots.
Content-addressed artifact keys let interrupted retries safely upload again before
DB publication. Split retains its existing request-ID transaction and ancestry.
Pair fingerprinting and merge-event collection stream pairs; event edge indexes
spill to SQLite rather than retaining all pair IDs across the run.

`CSV_EXPORTED.details.resources`, the completion manifest and structured logs expose
process-lifetime `peak_rss_bytes`, temporary `disk_peak_bytes`/`spill_peak_bytes`,
`queue_depth`, `backpressure_wait_seconds`, `upload_duration_seconds`, batch rows,
and readiness/write-validation/upload-verification/total stage durations. Export
is synchronous, so queue depth and backpressure wait are accurately zero. Upload
duration includes remote readback. Preview/event metadata still scales with the
number of proposals and one merge event's membership/pair IDs; this task does not
make the entire preview protocol constant-memory.

Budgets in Settings / `.env.example`:

| Variable | Default | Meaning |
| --- | --- | --- |
| `EXPORT_BATCH_ROWS` | 512 | Keyset batch and Parquet row-group buffer, 1–2048 |
| `EXPORT_MAX_GROUP_RECORDS` | 2048 | Maximum source group loaded for options/golden, 1–10000 |
| `EXPORT_DISK_BYTES` | 2147483648 | Combined temporary artifacts/index ceiling |
| `EXPORT_MAX_RSS_BYTES` | 0 | Process-lifetime peak RSS ceiling; zero disables enforcement |
| `EXPORT_TIMEOUT_SECONDS` | 3600 | Cooperative build/verification/upload callback deadline |
| `EXPORT_ARTIFACT_DIR` | `./data/export-artifacts` | Temporary builds and durable local objects |
| `EXPORT_PARQUET_ENABLED` | false | Additional validated, uploaded Parquet companion |

Disk/RSS violations fail 507 and elapsed-budget violations fail 504. Checks run at
batch boundaries, file chunks and transfer callbacks; these are cooperative guards,
not an OS hard limit, and one source field/group can transiently exceed a budget.
SQLite cache is 2 MiB and scratch is file-backed. Individual stalled network calls
remain subject to SDK timeouts/retries, rather than forced interruption by this deadline.

`scripts/run_merge_baseline.py --suite exports` isolates SQLite/R2/LLM settings and
artifacts. Fault injection covers upload, corrupt readback and completion-manifest
failure; tests also cover replay, field order/coverage, source choices/overrides,
empty/multi-batch Parquet, budgets and UI response/download compatibility. Fresh
child processes compare 128 versus 4096 rows of 4 KiB strings at batch sizes 16/128,
measuring tracemalloc peak and OS peak RSS growth. This is a local regression test,
not a million-row throughput or PostgreSQL/R2 production benchmark.

Outstanding deployment checks: isolated PostgreSQL concurrency rehearsal, real R2
multipart failure/abort behavior and throughput, durable local volume provisioning.
Unpublished remote/local content objects can remain after upload/DB failure; they
are never listed/downloaded by export APIs. Export-aware orphan GC is separate work;
do not delete historical artifacts using the pipeline checkpoint GC.
