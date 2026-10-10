# Partition artifact and background upload protocol

2026-10-08. Additive storage layer, explicitly used by producers; the current
stage coordinator, stage checkpoints, generation JSON receipts and API/frontend
are unchanged. No broker, dependency or DB migration. The protocol is P-119
native, informed by OneMaster artifact/background-upload designs, not imported
from the other repository at runtime.

Subsequent coordinator adoption: [STREAMING](STREAMING.md). Separate default-OFF
compute retry/resume adds DB commit/dependency indexes, checkpoint directory pins
and conservative explicit GC: [CHECKPOINTS](CHECKPOINTS.md). Native upload receipts
alone do not authorize cross-attempt reuse.

## Producer contract

`storage.artifact.PartitionArtifact` is frozen metadata: run/attempt/artifact/
partition IDs, relative Parquet filename, row count, byte count, SHA-256 and
Arrow schema fingerprint. P-119 attempt IDs are strings (job claim UUIDs), not
the reference implementation's integer attempts. Identity segments are bounded
and cannot contain path separators. Schema serialization includes metadata.

`DatasetManifest` stores `p119_partition_manifest_v1` in `_manifest.json`.
Manifest partition identity and schema must match the dataset; duplicate IDs/
paths are rejected. Atomic JSON replacement and file fsync precede acknowledgment
(directory fsync also runs on POSIX). Manifest mutations hold an OS file lock
and reload the current data, preventing concurrent upload/producer lost updates.

`write_partition` consumes bounded Arrow tables and writes through a capped
sink to a unique `.tmp` file. The Parquet writer and sink are closed/fsynced
before footer/schema/row-count/hash validation. Only then is the file renamed,
made read-only and added to the manifest. It never overwrites a committed file
or reuses a committed partition ID, even after cleanup. Validation failure
removes temporary output without publishing a reference. A crash between rename
and manifest commit can leave a private orphan; it is counted in disk usage and
must not be adopted as a durable partition automatically.

The file permission is an accidental-write guard; content checks remain the
integrity authority. `verify` runs before reads, enqueue, upload and checkpoint.
Remote read-back uses the expected hash and size: byte-identical content has
the same locally validated Parquet footer/schema/count. Readers use bounded
`iter_batches` under a partition lease. No glob-based discovery of stale parts.

## Use with a worker producer

Pass IDs from the current worker claim; use a fresh workspace per attempt and
one manifest directory per artifact. Example (schema/tables/store supplied by
the producer; `store` may be the existing R2 adapter from `get_store(settings)`):

```python
manifest = DatasetManifest.create(workspace / "scores", run_id=claim.run_id,
    attempt_id=claim.attempt_id, artifact_id="scores", schema=schema)
with BackgroundUploader(store, workspace, run_id=claim.run_id,
        attempt_id=claim.attempt_id) as uploads:
    for partition_id, tables in bounded_partitions:
        uploads.publish(manifest, partition_id, tables, schema,
                        max_partition_bytes=64 * 1024**2)
    manifest.finish(uploads.disk_guard)
# Successful exit drains uploads, not just computation.
remote_manifest_key = uploads.upload_manifest(manifest)
```

Producer tables/partition row bounds remain the caller's responsibility;
`max_partition_bytes` caps actual encoded output including the footer. A table
too large for that cap fails rather than silently splitting or changing stage
semantics. `publish` reserves capacity before consuming the table iterator.
Standalone `write_partition` enforces its byte cap; configured workspace-wide
admission/disk limits require the uploader's `publish` and `disk_guard` APIs.
Manifests created before the uploader also count against its disk budget.

## Budgets and backpressure

One `BackgroundUploader` holds the workspace OS lock; a second owner is rejected.
Default limits: eight pending partitions, 512 MiB pending bytes, two upload
threads, 2 GiB local workspace bytes, 64 MiB minimum free disk, 30-second
admission timeout, three transfer attempts. All are constructor parameters.

Pending limits include producers holding reservations, queued items, active
transfers and retry sleep. A producer reserves its maximum encoded size;
enqueue reduces that reservation to the verified actual size. Oversized
partitions fail immediately. Admission waits on the condition until capacity
returns, or raises a bounded timeout/error. Queue entries contain only a
directory reference and frozen metadata; no Arrow tables or result rows.

Disk usage includes every retained partition, manifest/checkpoint, lock file,
temporary file and orphan in the workspace, plus unconsumed producer byte
reservations. Every encoded write rechecks quota and physical free space.
Atomic metadata writes account for their temporary copy too. Admission keeps
16 KiB metadata headroom; larger manifests may still hit the exact disk guard
and require a larger budget. An external disk consumer can exhaust free space;
the producer stops before its next write. The budget is an application limit,
not an OS/filesystem quota for unrelated processes.

Uploaded local files remain retained for future consumers. Upload completion
does not automatically free local disk: explicit retention release/cleanup is
required. Uploader read-back streams chunks without another local full-file
copy. R2's dedicated partition upload disables nested boto3 transfer threads;
configured upload concurrency therefore bounds concurrent file transfers.
Existing general-purpose ObjectStore methods retain their behavior. Custom
stores used here must implement streaming `iter_chunks` and respect the bounded
partition transfer contract.

## Lease and shutdown

`partition_lease` holds a separate OS lock, with process-local reference counts
for overlapping readers/uploads. Queued work acquires its lease before it is
visible to an upload thread. Cleanup requires an exact valid upload checkpoint,
no local references and a nonblocking OS lock; it never unlinks lock files.
Across processes leases serialize use; process death releases the OS lease.
The workspace ownership lock prevents a restarted uploader from overlapping a
still-running instance and its budget reservations.

Cancellation/failure wakes admission waiters and drains queued leases. A drain
timeout cannot kill an uncooperative SDK thread: the active partition and
workspace leases remain held until that thread actually returns. Do not remove
the workspace externally while a producer/consumer/uploader uses it.

## Durability, recovery and final manifest

`manifest.finish()` records local computation completion only. Each upload:

1. Revalidates the committed local metadata/file.
2. Uploads to `partitions/v1/<run>/<attempt>/<artifact>/<partition>-<sha>.parquet`
   if absent, then streams the stored bytes to validate count and SHA-256.
3. Writes and verifies `p119_partition_receipt_v1` at the same key plus
   `.receipt.json`. This remote receipt is the durable partition checkpoint.
4. Atomically records the matching acknowledgment in the separate
   `_uploads.json` (`p119_partition_uploads_v1`).

Local completion can therefore remain true with no durable upload. Upload
failure does not rewrite the computation manifest. Retry handles transient
network/5xx/throttling/OSError failures, retaining its pending budget/lease;
checksum/schema/identity conflicts are not retried. Errors expose safe codes.
Existing objects/receipts are verified rather than blindly overwritten.
An upload acknowledged remotely but interrupted before the local checkpoint
is recovered by `enqueue(manifest, partition)`; content is reverified, not
trusted solely because `_uploads.json` or the object exists. After restart,
load the manifest with expected run/attempt IDs and enqueue its retained parts.
No scan/adoption of artifacts from another attempt is permitted.

`upload_manifest` is an explicit barrier requiring local completion and all
valid upload acknowledgments. It rechecks remote partitions and publishes the
full versioned manifest to a content-addressed JSON key. A consumer's serving
publication still uses the existing generation validation/ownership gate;
this layer does not promote serving data or mark a resolution job completed.

## Provenance and verification limits

Read-only OneMaster working-tree SHA-256:

- `src/onemaster/io/artifact.py`:
  `EC23DADBDBA8D764F3E75C891D070FB7C0F9698FC218B0A75E082B6E73DA2740`.
- `backend/app/worker/background_upload.py`:
  `C83504C38AE315457AED9EF9E3DD9E080D55A8058520467AF5FBCE9E9794D9E8`.

P-119 implementation was written for its string attempt identity, ObjectStore
adapters, hard byte admission, OS leases and remote receipts. No source file was
copied wholesale and no external runtime import/new dependency was introduced.
No license file was found in the reference tree; any external distribution of
reference-derived material still needs the project owner's licensing review.

Tests use disposable local storage, real Parquet and spawned lease processes;
R2 SDK concurrency/stream closure is mocked. No live R2, production DB or scale
benchmark. POSIX locking/fsync code requires Linux deployment rehearsal; this
session runs Windows. Reports and unchanged baseline failures: [STATUS](STATUS.md).
