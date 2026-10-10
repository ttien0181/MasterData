"""Closed, immutable attempt-local Parquet partitions and versioned manifests.

Selective adaptation of OneMaster's artifact protocol; no runtime dependency.
Local completion and verified remote upload receipts are separate checkpoints.
"""
import io
import json
import os
import stat
import threading
from contextlib import contextmanager, nullcontext
from pathlib import Path
from time import monotonic, sleep
from typing import Literal
from uuid import uuid4

import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import BaseModel, ConfigDict, Field, model_validator

from masterone.storage.manifest import fingerprint_file

FORMAT = "p119_partition_manifest_v1"
MANIFEST = "_manifest.json"
CHECKPOINT = "_uploads.json"
MAX_MANIFEST_BYTES = 8 * 1024**2
_mutex = threading.RLock()
_leases = {}


class ArtifactError(ValueError):
    """Stable non-sensitive error codes."""


@contextmanager
def file_lock(path, timeout=10):
    """OS lock, released on process death; lock files must never be unlinked."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()
    deadline = monotonic() + timeout
    acquired = False
    try:
        while not acquired:
            try:
                if os.name == "nt":
                    import msvcrt
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError:
                if monotonic() >= deadline:
                    raise TimeoutError("PARTITION_LOCK_BUSY") from None
                sleep(0.01)
        yield
    finally:
        if acquired:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


@contextmanager
def partition_lease(path):
    path = Path(path).resolve()
    key = str(path)
    with _mutex:
        if key not in _leases:
            lock = file_lock(path.with_suffix(".lease"))
            lock.__enter__()
            if not path.is_file():
                lock.__exit__(None, None, None)
                raise ArtifactError("PARTITION_MISSING")
            _leases[key] = [0, lock]
        _leases[key][0] += 1
    try:
        yield path
    finally:
        with _mutex:
            _leases[key][0] -= 1
            if _leases[key][0] == 0:
                _, lock = _leases.pop(key)
                lock.__exit__(None, None, None)


def schema_version(schema):
    from hashlib import sha256
    return "arrow_v1:" + sha256(schema.serialize().to_pybytes()).hexdigest()


def atomic_json(path, data, disk_guard=None):
    content = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    if len(content) > MAX_MANIFEST_BYTES:
        raise ArtifactError("MANIFEST_TOO_LARGE")
    temporary = path.with_name(".json-" + uuid4().hex + ".tmp")
    with disk_guard(len(content)) if disk_guard else nullcontext():
        try:
            with temporary.open("xb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            sync_directory(path.parent)
        finally:
            temporary.unlink(missing_ok=True)


def sync_directory(path):
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


Identity = str  # UUID attempts remain strings, unlike the reference integer IDs.


class PartitionArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    run_id: Identity = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    attempt_id: Identity = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    artifact_id: Identity = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    partition_id: Identity = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    path: str = Field(pattern=r"^part-[A-Za-z0-9_-]{1,128}\.parquet$")
    row_count: int = Field(ge=0)
    byte_size: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    schema_version: str = Field(pattern=r"^arrow_v1:[a-f0-9]{64}$")

    @property
    def key(self):
        return (f"partitions/v1/{self.run_id}/{self.attempt_id}/{self.artifact_id}/"
                f"{self.partition_id}-{self.sha256}.parquet")


class ArtifactManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    format: Literal["p119_partition_manifest_v1"] = FORMAT
    run_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    attempt_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    artifact_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    schema_version: str = Field(pattern=r"^arrow_v1:[a-f0-9]{64}$")
    local_complete: bool = False
    partitions: tuple[PartitionArtifact, ...] = Field(default=(), max_length=8192)

    @model_validator(mode="after")
    def identities(self):
        ids, paths = set(), set()
        for item in self.partitions:
            if (item.run_id, item.attempt_id, item.artifact_id, item.schema_version) != (
                    self.run_id, self.attempt_id, self.artifact_id, self.schema_version):
                raise ArtifactError("PARTITION_IDENTITY_MISMATCH")
            if item.partition_id in ids or item.path in paths:
                raise ArtifactError("PARTITION_DUPLICATE")
            ids.add(item.partition_id)
            paths.add(item.path)
        if self.local_complete and not self.partitions:
            raise ArtifactError("MANIFEST_EMPTY")
        return self


class _LimitedSink(io.RawIOBase):
    def __init__(self, path, capacity, disk_guard):
        super().__init__()
        self.handle = path.open("xb", buffering=0)
        self.capacity, self.disk_guard = capacity, disk_guard

    def writable(self):
        return True

    def tell(self):
        return self.handle.tell()

    def write(self, data):
        if self.tell() + len(data) > self.capacity:
            raise ArtifactError("PARTITION_BYTE_LIMIT")
        with self.disk_guard(len(data)) if self.disk_guard else nullcontext():
            return self.handle.write(data)

    def close(self):
        if not self.closed:
            self.handle.flush()
            os.fsync(self.handle.fileno())
            self.handle.close()
        super().close()


class DatasetManifest:
    def __init__(self, directory, identity):
        self.directory = Path(directory).resolve()
        self.identity = identity

    @classmethod
    def create(cls, directory, *, run_id, attempt_id, artifact_id, schema, disk_guard=None):
        directory = Path(directory)
        if directory.is_symlink():
            raise ArtifactError("PARTITION_UNSAFE_PATH")
        directory.mkdir(parents=True, exist_ok=True)
        data = ArtifactManifest(run_id=run_id, attempt_id=attempt_id, artifact_id=artifact_id,
                                schema_version=schema_version(schema))
        result = cls(directory, data)
        with file_lock(result.directory / ".manifest.lock"):
            if (result.directory / MANIFEST).exists():
                result.read()
            else:
                atomic_json(result.directory / MANIFEST, data.model_dump(mode="json"), disk_guard)
        return result

    @classmethod
    def load(cls, directory, **expected):
        directory = Path(directory)
        if directory.is_symlink():
            raise ArtifactError("PARTITION_UNSAFE_PATH")
        data = cls._read(directory / MANIFEST)
        for key, value in expected.items():
            if getattr(data, key) != value:
                raise ArtifactError("PARTITION_IDENTITY_MISMATCH")
        return cls(directory, data)

    @staticmethod
    def _read(path):
        if path.is_symlink() or path.stat().st_size > MAX_MANIFEST_BYTES:
            raise ArtifactError("MANIFEST_UNSAFE")
        return ArtifactManifest.model_validate_json(path.read_bytes())

    def read(self):
        data = self._read(self.directory / MANIFEST)
        if (data.run_id, data.attempt_id, data.artifact_id, data.schema_version) != (
                self.identity.run_id, self.identity.attempt_id,
                self.identity.artifact_id, self.identity.schema_version):
            raise ArtifactError("PARTITION_IDENTITY_MISMATCH")
        return data

    def path(self, item):
        path = self.directory / item.path
        if path.is_symlink() or path.resolve().parent != self.directory:
            raise ArtifactError("PARTITION_UNSAFE_PATH")
        return path

    def verify(self, item):
        if item not in self.read().partitions:
            raise ArtifactError("PARTITION_REFERENCE_MISMATCH")
        self.verify_file(self.path(item), item)

    @staticmethod
    def verify_file(path, item):
        if path.stat().st_size != item.byte_size or fingerprint_file(path) != item.sha256:
            raise ArtifactError("PARTITION_CHECKSUM_MISMATCH")
        try:
            with pq.ParquetFile(path) as reader:
                if reader.metadata.num_rows != item.row_count:
                    raise ArtifactError("PARTITION_ROW_COUNT_MISMATCH")
                if schema_version(reader.schema_arrow) != item.schema_version:
                    raise ArtifactError("PARTITION_SCHEMA_MISMATCH")
        except pa.ArrowException:
            raise ArtifactError("PARTITION_INVALID_FOOTER") from None

    def write_partition(self, partition_id, tables, schema, *, max_bytes,
                        disk_guard=None, metadata_guard=None):
        # The manifest lock serializes writers across processes. No committed
        # partition can be replaced, including an orphan after acknowledgment loss.
        with file_lock(self.directory / ".manifest.lock"):
            data = self.read()
            if data.local_complete:
                raise ArtifactError("ARTIFACT_ALREADY_COMPLETE")
            if schema_version(schema) != data.schema_version:
                raise ArtifactError("PARTITION_SCHEMA_MISMATCH")
            if not partition_id or len(partition_id) > 128 or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in partition_id):
                raise ArtifactError("PARTITION_INVALID_ID")
            target = self.directory / f"part-{partition_id}.parquet"
            if target.exists() or any(p.partition_id == partition_id for p in data.partitions):
                raise ArtifactError("PARTITION_IMMUTABLE")
            temporary = self.directory / (".parquet-" + uuid4().hex + ".tmp")
            rows = 0
            try:
                with _LimitedSink(temporary, max_bytes, disk_guard) as sink:
                    # Preserve Arrow list child names (default 'item') so the
                    # closed file has exactly the schema whose hash was pinned.
                    with pq.ParquetWriter(sink, schema, compression="zstd", use_compliant_nested_type=False) as writer:
                        for table in tables:
                            if not table.schema.equals(schema, check_metadata=True):
                                raise ArtifactError("PARTITION_SCHEMA_MISMATCH")
                            writer.write_table(table)
                            rows += table.num_rows
                item = PartitionArtifact(run_id=data.run_id, attempt_id=data.attempt_id,
                    artifact_id=data.artifact_id, partition_id=partition_id, path=target.name,
                    row_count=rows, byte_size=temporary.stat().st_size,
                    sha256=fingerprint_file(temporary), schema_version=data.schema_version)
                self.verify_file(temporary, item)
                os.rename(temporary, target)
                os.chmod(target, stat.S_IREAD)
                sync_directory(self.directory)
                updated = ArtifactManifest.model_validate({**data.model_dump(),
                    "partitions": (*data.partitions, item)})
                atomic_json(self.directory / MANIFEST, updated.model_dump(mode="json"), metadata_guard)
                return item
            finally:
                temporary.unlink(missing_ok=True)

    def finish(self, disk_guard=None):
        with file_lock(self.directory / ".manifest.lock"):
            data = self.read()
            if not data.partitions:
                raise ArtifactError("MANIFEST_EMPTY")
            for item in data.partitions:
                with partition_lease(self.path(item)):
                    self.verify(item)
            atomic_json(self.directory / MANIFEST,
                        data.model_copy(update={"local_complete": True}).model_dump(mode="json"), disk_guard)

    def uploaded(self):
        path = self.directory / CHECKPOINT
        if not path.exists():
            return {}
        if path.is_symlink() or path.stat().st_size > MAX_MANIFEST_BYTES:
            raise ArtifactError("UPLOAD_CHECKPOINT_UNSAFE")
        try:
            data = json.loads(path.read_bytes())
        except ValueError:
            raise ArtifactError("UPLOAD_CHECKPOINT_INVALID") from None
        identity = self.read()
        if not isinstance(data, dict) or data.get("format") != "p119_partition_uploads_v1" or data.get("identity") != [
                identity.run_id, identity.attempt_id, identity.artifact_id]:
            raise ArtifactError("UPLOAD_CHECKPOINT_IDENTITY")
        receipts = data.get("receipts")
        if not isinstance(receipts, dict):
            raise ArtifactError("UPLOAD_CHECKPOINT_INVALID")
        partitions = {p.partition_id: p for p in identity.partitions}
        for partition_id, receipt in receipts.items():
            item = partitions.get(partition_id)
            if item is None or receipt != {"key": item.key, "sha256": item.sha256,
                                          "receipt_key": item.key + ".receipt.json"}:
                raise ArtifactError("UPLOAD_CHECKPOINT_CONFLICT")
        return receipts

    def record_upload(self, item, disk_guard=None):
        with file_lock(self.directory / ".manifest.lock"):
            self.verify(item)
            receipts = self.uploaded()
            value = {"key": item.key, "sha256": item.sha256, "receipt_key": item.key + ".receipt.json"}
            if item.partition_id in receipts and receipts[item.partition_id] != value:
                raise ArtifactError("UPLOAD_CHECKPOINT_CONFLICT")
            receipts[item.partition_id] = value
            atomic_json(self.directory / CHECKPOINT, {"format": "p119_partition_uploads_v1",
                "identity": [item.run_id, item.attempt_id, item.artifact_id], "receipts": receipts}, disk_guard)

    @property
    def durable(self):
        data, receipts = self.read(), self.uploaded()
        return data.local_complete and all(receipts.get(p.partition_id) == {
            "key": p.key, "sha256": p.sha256, "receipt_key": p.key + ".receipt.json"} for p in data.partitions)

    def cleanup(self, item):
        # Explicit retention release only; never unlink lock files (ABA race).
        with file_lock(self.directory / ".manifest.lock"), _mutex:
            # Durable compute checkpoints pin the directory until explicit GC
            # has retired dependency references. Corrupt pin files fail closed.
            if (self.directory / ".checkpoint-pin").exists():
                return False
            path = self.path(item)
            receipt = self.uploaded().get(item.partition_id)
            expected = {"key": item.key, "sha256": item.sha256, "receipt_key": item.key + ".receipt.json"}
            if str(path.resolve()) in _leases or receipt != expected:
                return False
            try:
                with file_lock(path.with_suffix(".lease"), timeout=0):
                    self.verify(item)
                    os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
                    path.unlink()
                    return True
            except TimeoutError:
                return False

    def iter_batches(self, item, batch_size=65536):
        with partition_lease(self.path(item)):
            self.verify(item)
            with pq.ParquetFile(self.path(item)) as reader:
                yield from reader.iter_batches(batch_size=batch_size)


class ArtifactWriter:
    """Native producer adapter: upload admission precedes partition computation."""

    def __init__(self, manifest, uploader, schema, max_partition_bytes):
        self.manifest, self.uploader, self.schema = manifest, uploader, schema
        self.max_partition_bytes = max_partition_bytes

    def write(self, partition_id, tables):
        return self.uploader.publish(self.manifest, partition_id, tables, self.schema,
                                     max_partition_bytes=self.max_partition_bytes)

    def close(self):
        # Empty stages still have a verifiable schema/footer and an explicit
        # durable checkpoint; they must not be confused with an unfinished stage.
        if not self.manifest.read().partitions:
            self.write("p00000000", [pa.Table.from_pylist([], schema=self.schema)])
        self.manifest.finish(self.uploader.disk_guard)
