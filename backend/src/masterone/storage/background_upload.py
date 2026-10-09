"""Bounded producer admission and uploads of immutable partition references.

One uploader owns an attempt workspace. Queued/active/retrying partitions and
producer reservations share the budgets. No stage coordinator is replaced.
"""
import hashlib
import json
import shutil
from collections import deque
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import Condition, Event, Thread
from time import monotonic

from masterone.storage.artifact import ArtifactError, DatasetManifest, PartitionArtifact, file_lock, partition_lease


@dataclass(frozen=True)
class PartitionReference:
    directory: Path
    metadata: PartitionArtifact


class UploadError(RuntimeError):
    """Upload failures contain a stable code, never source/SDK exception text."""


class BackgroundUploader:
    def __init__(self, store, workspace, *, run_id, attempt_id, max_pending_partitions=8,
                 max_pending_bytes=512 * 1024**2, upload_concurrency=2,
                 local_disk_budget=2 * 1024**3, min_free_bytes=64 * 1024**2,
                 admission_timeout=30, max_attempts=3, retry_seconds=0.1,
                 disk_usage=shutil.disk_usage, retryable=None):
        if min(max_pending_partitions, max_pending_bytes, upload_concurrency,
               local_disk_budget, max_attempts) <= 0 or min(min_free_bytes, admission_timeout, retry_seconds) < 0:
            raise ValueError("Invalid upload budgets")
        self.store, self.workspace = store, Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.run_id, self.attempt_id = run_id, attempt_id
        self.max_files, self.max_bytes = max_pending_partitions, max_pending_bytes
        self.disk_budget, self.min_free = local_disk_budget, min_free_bytes
        self.admission_timeout, self.disk_usage = admission_timeout, disk_usage
        self.max_attempts, self.retry_seconds = max_attempts, retry_seconds
        self.retryable = retryable or self._transient
        self.on_uploaded = None
        self.condition, self.stop = Condition(), Event()
        self.queue, self.pending, self.reservations = deque(), {}, {}
        self.pending_bytes = self.active = 0
        self.closed, self.error = False, None
        self._owner = file_lock(self.workspace / ".uploader.lock", timeout=0)
        self._owner.__enter__()
        self.threads = [Thread(target=self._worker_entry, name=f"partition-upload-{i}", daemon=True)
                        for i in range(min(upload_concurrency, max_pending_partitions))]
        self._workers_left = len(self.threads)
        for thread in self.threads:
            thread.start()

    def _release_owner_if_idle(self):
        if self.closed and self._workers_left == 0 and not self.reservations and not self.pending and self._owner is not None:
            self._owner.__exit__(None, None, None)
            self._owner = None

    def _worker_entry(self):
        try:
            self._worker()
        finally:
            with self.condition:
                self._workers_left -= 1
                self._release_owner_if_idle()
                self.condition.notify_all()

    @staticmethod
    def _transient(error):
        from botocore.exceptions import ClientError, ConnectionClosedError, EndpointConnectionError, ReadTimeoutError
        if isinstance(error, ClientError):
            return error.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0) >= 500 or (
                error.response.get("Error", {}).get("Code") in {"SlowDown", "RequestTimeout", "Throttling"})
        return isinstance(error, (OSError, ConnectionClosedError, EndpointConnectionError, ReadTimeoutError))

    def _check(self):
        if self.error is not None:
            raise self.error
        if self.stop.is_set():
            raise UploadError("UPLOAD_CANCELLED")

    def _bind(self, manifest):
        data = manifest.read()
        if (data.run_id, data.attempt_id) != (self.run_id, self.attempt_id):
            raise ArtifactError("PARTITION_IDENTITY_MISMATCH")
        if not manifest.directory.is_relative_to(self.workspace):
            raise ArtifactError("PARTITION_WORKSPACE_MISMATCH")

    def _used_disk(self):
        # Include old artifacts, manifests, orphan temp files and lock files.
        used = 0
        for path in self.workspace.rglob("*"):
            try:
                if path.is_file():
                    used += path.stat().st_size
            except FileNotFoundError:
                continue  # Concurrent explicit cleanup relinquished those bytes.
        return used

    def upload_manifest(self, manifest):
        """Explicit artifact barrier after local completion and all upload receipts."""
        self._bind(manifest)
        if not manifest.durable:
            raise ArtifactError("ARTIFACT_NOT_DURABLE")
        data = manifest.read()
        for item in data.partitions:
            self._verify_remote(item.key, item.sha256, item.byte_size)
        payload = json.dumps(data.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
        checksum = hashlib.sha256(payload).hexdigest()
        key = f"partitions/v1/{self.run_id}/{self.attempt_id}/{data.artifact_id}/manifest-{checksum}.json"
        if not self.store.exists(key):
            self.store.upload_bytes(payload, key, "application/json")
        self._verify_remote(key, checksum, len(payload))
        return key

    @contextmanager
    def disk_guard(self, size, reservation=None):
        with self.condition:
            self._check()
            remaining = sum(self.reservations.values())
            extra = size if reservation is None else 0
            if (self._used_disk() + remaining + extra > self.disk_budget
                    or self.disk_usage(self.workspace).free < size + self.min_free):
                raise ArtifactError("LOCAL_DISK_LIMIT")
            if reservation is not None and size > self.reservations[reservation]:
                raise ArtifactError("PARTITION_BYTE_LIMIT")
            yield
            if reservation is not None:
                self.reservations[reservation] -= size

    def snapshot(self):
        with self.condition:
            return {"pending_partitions": len(self.pending) + len(self.reservations),
                    "pending_bytes": self.pending_bytes, "active_uploads": self.active,
                    "local_bytes": self._used_disk()}

    def flush(self, timeout=30):
        """Durability barrier without closing admission for later stages."""
        deadline = monotonic() + timeout
        with self.condition:
            while self.pending or self.reservations:
                self._check()
                if monotonic() >= deadline:
                    raise UploadError("UPLOAD_BARRIER_TIMEOUT")
                self.condition.wait(min(0.05, max(0, deadline - monotonic())))
            self._check()

    def _reserve(self, size, *, producing):
        if size <= 0 or size > self.max_bytes:
            raise ArtifactError("PARTITION_PENDING_BYTE_LIMIT")
        deadline = monotonic() + self.admission_timeout
        with self.condition:
            while True:
                self._check()
                if self.closed:
                    raise UploadError("UPLOAD_CLOSED")
                disk_required = size if producing else 0
                # Headroom for the atomic local manifest/checkpoint replacement.
                disk_ok = (self._used_disk() + sum(self.reservations.values()) + disk_required + 16384 <= self.disk_budget
                           and self.disk_usage(self.workspace).free >= disk_required + self.min_free + 16384)
                if (len(self.pending) + len(self.reservations) < self.max_files
                        and self.pending_bytes + size <= self.max_bytes and disk_ok):
                    token = object()
                    self.reservations[token] = size if producing else 0
                    self.pending_bytes += size
                    return token
                if monotonic() >= deadline:
                    raise ArtifactError("LOCAL_DISK_LIMIT" if not disk_ok else "UPLOAD_BACKPRESSURE_TIMEOUT")
                self.condition.wait(min(0.05, max(0, deadline - monotonic())))

    def publish(self, manifest, partition_id, tables, schema, *, max_partition_bytes):
        """Reserve before asking the producer iterator for any table or bytes."""
        self._bind(manifest)
        token = self._reserve(max_partition_bytes, producing=True)
        try:
            item = manifest.write_partition(partition_id, tables, schema, max_bytes=max_partition_bytes,
                disk_guard=lambda size: self.disk_guard(size, token), metadata_guard=self.disk_guard)
            self._queue(manifest, item, token, max_partition_bytes)
            return item
        except BaseException:
            with self.condition:
                if token in self.reservations:
                    self.reservations.pop(token)
                    self.pending_bytes -= max_partition_bytes
                    self._release_owner_if_idle()
                    self.condition.notify_all()
            raise

    def enqueue(self, manifest, item):
        """Recovery: reverify existing closed partitions, including old receipts."""
        self._bind(manifest)
        with partition_lease(manifest.path(item)):
            manifest.verify(item)
        token = self._reserve(item.byte_size, producing=False)
        try:
            self._queue(manifest, item, token, item.byte_size)
        except BaseException:
            with self.condition:
                if token in self.reservations:
                    self.reservations.pop(token)
                    self.pending_bytes -= item.byte_size
                    self._release_owner_if_idle()
                    self.condition.notify_all()
            raise

    def _queue(self, manifest, item, token, reserved_bytes):
        # Leases cover queue wait, transfer and retry sleep, not just SDK reads.
        lease = partition_lease(manifest.path(item))
        lease.__enter__()
        try:
            manifest.verify(item)
            with self.condition:
                self._check()
                if self.closed:
                    raise UploadError("UPLOAD_CLOSED")
                if item.key in self.pending:
                    raise ArtifactError("PARTITION_ALREADY_PENDING")
                self.reservations.pop(token)
                self.pending_bytes += item.byte_size - reserved_bytes
                self.pending[item.key] = (PartitionReference(manifest.directory, item), lease)
                self.queue.append(item.key)
                self.condition.notify_all()
        except BaseException:
            lease.__exit__(None, None, None)
            raise

    def _verify_remote(self, key, expected_hash, expected_size):
        checksum, size = hashlib.sha256(), 0
        with closing(self.store.iter_chunks(key)) as chunks:
            for chunk in chunks:
                self._check()
                size += len(chunk)
                if size > expected_size:
                    raise ArtifactError("UPLOAD_CHECKSUM_MISMATCH")
                checksum.update(chunk)
        if size != expected_size or checksum.hexdigest() != expected_hash:
            raise ArtifactError("UPLOAD_CHECKSUM_MISMATCH")

    def _upload(self, reference):
        item = reference.metadata
        manifest = DatasetManifest.load(reference.directory, run_id=self.run_id,
            attempt_id=self.attempt_id, artifact_id=item.artifact_id)
        manifest.verify(item)
        if not self.store.exists(item.key):
            self.store.upload_partition_file(manifest.path(item), item.key)
        self._verify_remote(item.key, item.sha256, item.byte_size)
        # Content-addressed remote receipt is the durable checkpoint. Local
        # computation completion never stands in for this acknowledgment.
        receipt = json.dumps({"format": "p119_partition_receipt_v1",
            "partition": item.model_dump(mode="json"), "key": item.key},
            sort_keys=True, separators=(",", ":")).encode()
        key = item.key + ".receipt.json"
        if not self.store.exists(key):
            self.store.upload_bytes(receipt, key, "application/json")
        self._verify_remote(key, hashlib.sha256(receipt).hexdigest(), len(receipt))
        self._check()
        manifest.record_upload(item, self.disk_guard)

    def _worker(self):
        while True:
            with self.condition:
                while not self.queue and not self.closed and not self.stop.is_set():
                    self.condition.wait()
                if self.stop.is_set() or not self.queue:
                    return
                key = self.queue.popleft()
                reference, lease = self.pending[key]
                self.active += 1
            try:
                for attempt in range(self.max_attempts):
                    try:
                        self._check()
                        self._upload(reference)
                        if self.on_uploaded is not None:
                            self.on_uploaded(reference)
                        break
                    except Exception as error:
                        if attempt + 1 == self.max_attempts or not self.retryable(error):
                            raise
                        self.stop.wait(self.retry_seconds * 2**attempt)
            except Exception as error:
                with self.condition:
                    self.error = error if isinstance(error, (ArtifactError, UploadError)) else UploadError(
                        "UPLOAD_FAILED_" + type(error).__name__)
                    self.stop.set()
            finally:
                lease.__exit__(None, None, None)
                with self.condition:
                    self.pending.pop(key)
                    self.pending_bytes -= reference.metadata.byte_size
                    self.active -= 1
                    self.condition.notify_all()

    def shutdown(self, *, cancel=False, timeout=30):
        deadline = monotonic() + timeout
        with self.condition:
            self.closed = True
            if cancel:
                self.stop.set()
            self.condition.notify_all()
        for thread in self.threads:
            thread.join(max(0, deadline - monotonic()))
        with self.condition:
            if any(t.is_alive() for t in self.threads):
                self.stop.set()
                self.error = self.error or UploadError("UPLOAD_DRAIN_TIMEOUT")
            while self.queue:
                key = self.queue.popleft()
                reference, lease = self.pending.pop(key)
                lease.__exit__(None, None, None)
                self.pending_bytes -= reference.metadata.byte_size
            self.condition.notify_all()
            self._release_owner_if_idle()
            self._check()

    def __enter__(self):
        return self

    def __exit__(self, kind, error, traceback):
        if kind is None:
            self.shutdown()
        else:
            try:
                self.shutdown(cancel=True)
            except (ArtifactError, UploadError):
                # Preserve the producer's original exception; active SDK calls
                # retain their leases until they actually return.
                pass
