import json
import multiprocessing
import os
import stat
import threading
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import Mock

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from masterone.storage.artifact import (
    CHECKPOINT,
    ArtifactError,
    ArtifactManifest,
    DatasetManifest,
    PartitionArtifact,
    partition_lease,
    schema_version,
)
from masterone.storage.background_upload import BackgroundUploader, PartitionReference, UploadError
from masterone.storage.object_store import LocalObjectStore, R2ObjectStore
from pydantic import ValidationError


@pytest.fixture
def dataset(tmp_path):
    schema = pa.schema([("id", pa.int64()), ("value", pa.string())], metadata={b"contract": b"test-v1"})
    table = pa.Table.from_pylist([{"id": 1, "value": "Ada"}, {"id": 2, "value": "Lin"}], schema=schema)
    workspace = tmp_path / "work"
    manifest = DatasetManifest.create(workspace / "scores", run_id="run", attempt_id="attempt-a",
                                      artifact_id="scores", schema=schema)
    store = LocalObjectStore(tmp_path / "remote")
    return workspace, manifest, schema, table, store


def uploader(dataset, **kwargs):
    workspace, _, _, _, store = dataset
    return BackgroundUploader(store, workspace, run_id="run", attempt_id="attempt-a",
        min_free_bytes=0, local_disk_budget=1024**2, retry_seconds=0, **kwargs)


def local_partition(dataset, partition_id="0"):
    _, manifest, schema, table, _ = dataset
    return manifest.write_partition(partition_id, [table], schema, max_bytes=8192)


def test_closed_valid_immutable_partition_and_versioned_manifest(dataset):
    _, manifest, schema, table, _ = dataset
    item = local_partition(dataset)
    assert item.row_count == 2 and item.schema_version == schema_version(schema)
    assert not list(manifest.directory.glob("*.tmp"))
    with pq.ParquetFile(manifest.path(item)) as reader:
        assert reader.schema_arrow.equals(schema, check_metadata=True)
        assert reader.metadata.num_rows == 2
    with pytest.raises(ValidationError):
        item.row_count = 3
    with pytest.raises(ArtifactError, match="PARTITION_IMMUTABLE"):
        manifest.write_partition("0", [table], schema, max_bytes=8192)
    manifest.finish()
    assert manifest.read().local_complete and not manifest.durable
    assert not (manifest.directory / CHECKPOINT).exists()
    assert sum(b.num_rows for b in manifest.iter_batches(item, batch_size=1)) == 2
    assert DatasetManifest.load(manifest.directory).read().format == "p119_partition_manifest_v1"


def test_empty_partition_has_valid_footer_and_schema(dataset):
    _, manifest, schema, _, _ = dataset
    item = manifest.write_partition("empty", [], schema, max_bytes=8192)
    manifest.finish()
    assert item.row_count == 0
    manifest.verify(item)


@pytest.mark.parametrize("change,code", [("truncated", "CHECKSUM"), ("checksum", "CHECKSUM"),
                                       ("footer", "FOOTER"), ("schema", "SCHEMA"), ("rows", "ROW_COUNT")])
def test_invalid_partition_is_rejected(dataset, change, code):
    _, manifest, _, _, _ = dataset
    item = local_partition(dataset)
    path = manifest.path(item)
    os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    if change == "truncated":
        path.write_bytes(path.read_bytes()[:-5])
    elif change in {"checksum", "footer"}:
        value = path.read_bytes()
        path.write_bytes(value[:-4] + b"BAD!")
        if change == "footer":
            item = item.model_copy(update={"sha256": sha256(path.read_bytes()).hexdigest()})
    elif change == "schema":
        item = item.model_copy(update={"schema_version": "arrow_v1:" + "a" * 64})
    else:
        item = item.model_copy(update={"row_count": 3})
    with pytest.raises(ArtifactError, match=code):
        manifest.verify_file(path, item)


def test_failed_producer_never_publishes_reference(dataset):
    _, manifest, schema, table, _ = dataset

    def broken():
        yield table
        raise RuntimeError("producer failed")

    with pytest.raises(RuntimeError):
        manifest.write_partition("0", broken(), schema, max_bytes=8192)
    assert not manifest.read().partitions
    assert not list(manifest.directory.glob("*.tmp"))
    assert not list(manifest.directory.glob("*.parquet"))


def test_exact_partition_byte_cap_includes_footer(dataset):
    _, manifest, schema, table, _ = dataset
    with pytest.raises(ArtifactError, match="PARTITION_BYTE_LIMIT"):
        manifest.write_partition("0", [table], schema, max_bytes=128)
    assert not manifest.read().partitions
    assert not list(manifest.directory.glob("*.tmp"))


def test_attempts_and_schemas_cannot_mix(dataset):
    workspace, manifest, schema, _, _ = dataset
    item = local_partition(dataset)
    data = manifest.read().model_dump()
    data["partitions"] = [item.model_copy(update={"attempt_id": "attempt-b"})]
    with pytest.raises(ValidationError):
        ArtifactManifest.model_validate(data)
    with pytest.raises(ArtifactError, match="IDENTITY"):
        DatasetManifest.load(manifest.directory, attempt_id="attempt-b")
    with pytest.raises(ArtifactError, match="IDENTITY"):
        DatasetManifest.create(manifest.directory, run_id="run", attempt_id="attempt-b", artifact_id="scores", schema=schema)
    with BackgroundUploader(dataset[-1], workspace, run_id="run", attempt_id="attempt-b", min_free_bytes=0) as pool:
        with pytest.raises(ArtifactError, match="IDENTITY"):
            pool.enqueue(manifest, item)
    assert "attempt-a" in item.key
    assert item.key != item.model_copy(update={"attempt_id": "attempt-b"}).key


def test_metadata_only_queue_and_durable_upload_barrier(dataset):
    _, manifest, schema, table, store = dataset
    entered, release = threading.Event(), threading.Event()
    original = store.upload_partition_file

    def blocked(path, key):
        # The callback receives a closed file with a readable footer.
        with pq.ParquetFile(path) as reader:
            assert reader.metadata.num_rows == 2
        entered.set()
        assert release.wait(5)
        original(path, key)

    store.upload_partition_file = blocked
    pool = uploader(dataset)
    try:
        item = pool.publish(manifest, "0", [table], schema, max_partition_bytes=8192)
        assert entered.wait(5)
        manifest.finish(pool.disk_guard)
        assert manifest.read().local_complete and not manifest.durable
        reference = next(iter(pool.pending.values()))[0]
        assert isinstance(reference, PartitionReference)
        assert isinstance(reference.metadata, PartitionArtifact)
        assert set(reference.__dataclass_fields__) == {"directory", "metadata"}
        with pytest.raises(ArtifactError, match="NOT_DURABLE"):
            pool.upload_manifest(manifest)
        assert not manifest.cleanup(item)
    finally:
        release.set()
        pool.shutdown()
    assert manifest.durable
    key = pool.upload_manifest(manifest)
    assert json.loads(store.download_bytes(key))["attempt_id"] == "attempt-a"
    assert store.exists(item.key + ".receipt.json")


def test_upload_retry_and_recovery_after_remote_ack_loss(dataset):
    _, manifest, _, _, store = dataset
    item = local_partition(dataset)
    manifest.finish()
    original = store.upload_partition_file
    calls = []

    def lose_ack(path, key):
        calls.append(key)
        original(path, key)
        raise OSError("temporary lost acknowledgment")

    store.upload_partition_file = lose_ack
    with uploader(dataset) as pool:
        pool.enqueue(manifest, item)
    assert len(calls) == 1  # Retry verifies the already uploaded content.
    assert manifest.durable
    (manifest.directory / CHECKPOINT).unlink()
    with uploader(dataset) as recovered:
        recovered.enqueue(DatasetManifest.load(manifest.directory), item)
    assert len(calls) == 1 and manifest.durable


def test_retry_exhaustion_is_separate_from_local_completion(dataset):
    _, manifest, _, _, store = dataset
    item = local_partition(dataset)
    manifest.finish()
    store.upload_partition_file = Mock(side_effect=OSError("private SDK text"))
    pool = uploader(dataset, max_attempts=2)
    pool.enqueue(manifest, item)
    with pytest.raises(UploadError, match="UPLOAD_FAILED_OSError") as error:
        pool.shutdown()
    assert "private" not in str(error.value)
    assert store.upload_partition_file.call_count == 2
    assert manifest.read().local_complete and not manifest.durable
    assert not (manifest.directory / CHECKPOINT).exists()


def test_remote_checksum_mismatch_never_records_durable_receipt(dataset):
    _, manifest, _, _, store = dataset
    item = local_partition(dataset)
    manifest.finish()
    store.upload_partition_file = lambda _path, key: store.upload_bytes(b"wrong bytes", key)
    pool = uploader(dataset)
    pool.enqueue(manifest, item)
    with pytest.raises(ArtifactError, match="UPLOAD_CHECKSUM_MISMATCH"):
        pool.shutdown()
    assert not manifest.durable
    assert not store.exists(item.key + ".receipt.json")


@pytest.mark.parametrize("limit", ["partitions", "bytes"])
def test_producer_backpressure_before_consuming_input(dataset, limit):
    _, manifest, schema, table, store = dataset
    entered, release, consumed = threading.Event(), threading.Event(), threading.Event()
    original = store.upload_partition_file

    def blocked(path, key):
        entered.set()
        assert release.wait(5)
        original(path, key)

    store.upload_partition_file = blocked
    pool = uploader(dataset, max_pending_partitions=1 if limit == "partitions" else 8,
                    max_pending_bytes=8192 if limit == "partitions" else 4096)
    errors = []

    def tables():
        consumed.set()
        yield table

    def produce():
        try:
            pool.publish(manifest, "1", tables(), schema, max_partition_bytes=4096)
        except Exception as error:
            errors.append(error)

    try:
        pool.publish(manifest, "0", [table], schema, max_partition_bytes=4096)
        assert entered.wait(5)
        thread = threading.Thread(target=produce)
        thread.start()
        assert not consumed.wait(0.15)
        assert pool.snapshot()["pending_partitions"] == 1
        release.set()
        thread.join(timeout=5)
        assert not thread.is_alive() and not errors and consumed.is_set()
    finally:
        release.set()
        pool.shutdown()


def test_upload_concurrency_bound_includes_active_calls(dataset):
    _, manifest, schema, table, store = dataset
    lock, release, two_active = threading.Lock(), threading.Event(), threading.Event()
    active = peak = 0
    original = store.upload_partition_file

    def blocked(path, key):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            if active == 2:
                two_active.set()
        assert release.wait(5)
        original(path, key)
        with lock:
            active -= 1

    store.upload_partition_file = blocked
    pool = uploader(dataset, upload_concurrency=2)
    try:
        for i in range(3):
            pool.publish(manifest, str(i), [table], schema, max_partition_bytes=4096)
        assert two_active.wait(5)
        assert pool.snapshot()["active_uploads"] == 2
        assert pool.snapshot()["pending_partitions"] == 3
    finally:
        release.set()
        pool.shutdown()
    assert peak == 2


@pytest.mark.parametrize("kind", ["budget", "free_space"])
def test_disk_limit_blocks_before_producer_or_temp_file(dataset, kind):
    workspace, manifest, schema, table, store = dataset
    consumed = []

    def tables():
        consumed.append(True)
        yield table

    pool = BackgroundUploader(store, workspace, run_id="run", attempt_id="attempt-a",
        local_disk_budget=1024 if kind == "budget" else 1024**2, min_free_bytes=0,
        disk_usage=lambda _path: SimpleNamespace(free=0 if kind == "free_space" else 1024**3),
        admission_timeout=0)
    try:
        with pytest.raises(ArtifactError, match="LOCAL_DISK_LIMIT"):
            pool.publish(manifest, "0", tables(), schema, max_partition_bytes=4096)
        assert not consumed and not manifest.read().partitions
        assert not list(workspace.rglob("*.tmp"))
    finally:
        pool.shutdown()


def test_disk_growth_after_admission_stops_writer(dataset):
    workspace, manifest, schema, table, _ = dataset
    pool = uploader(dataset)

    def tables():
        (workspace / "external.bin").write_bytes(b"x" * 1024**2)
        yield table

    try:
        with pytest.raises(ArtifactError, match="LOCAL_DISK_LIMIT"):
            pool.publish(manifest, "0", tables(), schema, max_partition_bytes=4096)
        assert not manifest.read().partitions
        assert pool.snapshot()["pending_bytes"] == 0
    finally:
        pool.shutdown()


def test_file_lease_prevents_cleanup_and_reader_releases_it(dataset):
    _, manifest, _, _, _ = dataset
    item = local_partition(dataset)
    manifest.finish()
    with uploader(dataset) as pool:
        pool.enqueue(manifest, item)
    batches = manifest.iter_batches(item, batch_size=1)
    next(batches)
    assert not manifest.cleanup(item)
    batches.close()
    assert manifest.cleanup(item)
    assert not manifest.path(item).exists()
    with pytest.raises(ArtifactError, match="MISSING"):
        with partition_lease(manifest.path(item)):
            pass


def hold_lease(path, ready, release):
    with partition_lease(path):
        ready.set()
        release.wait(10)


@pytest.mark.parametrize("crash", [False, True])
def test_file_lease_across_processes(dataset, crash):
    _, manifest, _, _, _ = dataset
    item = local_partition(dataset)
    manifest.finish()
    with uploader(dataset) as pool:
        pool.enqueue(manifest, item)
    ctx = multiprocessing.get_context("spawn")
    ready, release = ctx.Event(), ctx.Event()
    child = ctx.Process(target=hold_lease, args=(manifest.path(item), ready, release))
    child.start()
    try:
        assert ready.wait(10)
        assert not manifest.cleanup(item)
        if crash:
            child.terminate()
    finally:
        if not crash:
            release.set()
        child.join(timeout=10)
        if child.is_alive():
            child.terminate()
            child.join()
    assert (child.exitcode != 0) if crash else (child.exitcode == 0)
    assert manifest.cleanup(item)


def test_drain_timeout_keeps_active_upload_lease(dataset):
    _, manifest, _, _, store = dataset
    item = local_partition(dataset)
    # Already durable but another explicit verification/upload call is pending.
    manifest.finish()
    with uploader(dataset) as first:
        first.enqueue(manifest, item)
    entered, release = threading.Event(), threading.Event()
    original = store.iter_chunks

    def blocked(key, **kwargs):
        entered.set()
        assert release.wait(5)
        yield from original(key, **kwargs)

    store.iter_chunks = blocked
    pool = uploader(dataset)
    pool.enqueue(manifest, item)
    assert entered.wait(5)
    try:
        with pytest.raises(UploadError, match="DRAIN_TIMEOUT"):
            pool.shutdown(timeout=0.01)
        assert not manifest.cleanup(item)
    finally:
        release.set()
        for thread in pool.threads:
            thread.join(timeout=5)
    assert manifest.cleanup(item)


def test_r2_partition_transfer_has_no_nested_transfer_threads(tmp_path):
    store = R2ObjectStore("https://r2.example.test", "test-access", "test-secret", "test-bucket")
    store.client.upload_file = Mock()
    store.upload_partition_file(tmp_path / "part.parquet", "partition-key")
    config = store.client.upload_file.call_args.kwargs["Config"]
    assert config.use_threads is False and config.max_concurrency == 1


def test_r2_streaming_readback_closes_body():
    import io
    store = R2ObjectStore("https://r2.example.test", "test-access", "test-secret", "test-bucket")
    body = io.BytesIO(b"12345678")
    store.client.get_object = Mock(return_value={"Body": body})
    assert list(store.iter_chunks("key", chunk_size=3)) == [b"123", b"456", b"78"]
    assert body.closed


def test_workspace_has_one_uploader_owner(dataset):
    pool = uploader(dataset)
    try:
        with pytest.raises(TimeoutError, match="PARTITION_LOCK_BUSY"):
            uploader(dataset)
    finally:
        pool.shutdown()
    with uploader(dataset):
        pass


def test_local_store_long_attempt_key_and_root_containment(tmp_path):
    store = LocalObjectStore(tmp_path / "nested" / ("w" * 100))
    key = "partitions/v1/run/attempt/artifact/" + "a" * 64 + ".parquet.receipt.json"
    store.upload_bytes(b"receipt", key)
    assert b"".join(store.iter_chunks(key)) == b"receipt"
    with pytest.raises(ValueError, match="escapes"):
        store.exists("../../sibling/key")


def test_local_retention_counts_against_disk_budget(dataset):
    workspace, manifest, schema, table, store = dataset
    pool = BackgroundUploader(store, workspace, run_id="run", attempt_id="attempt-a",
        min_free_bytes=0, local_disk_budget=24000, admission_timeout=0)
    stopped = False
    try:
        for index in range(30):
            try:
                pool.publish(manifest, str(index), [table], schema, max_partition_bytes=4096)
            except ArtifactError as error:
                assert str(error) == "LOCAL_DISK_LIMIT"
                stopped = True
                break
            with pool.condition:
                assert pool.condition.wait_for(lambda: not pool.pending, timeout=5)
            assert pool.snapshot()["local_bytes"] <= 24000
        assert stopped and manifest.read().partitions
        assert pool.snapshot()["pending_partitions"] == 0
    finally:
        pool.shutdown()


def test_corrupt_upload_checkpoint_cannot_authorize_cleanup(dataset):
    _, manifest, _, _, _ = dataset
    item = local_partition(dataset)
    manifest.finish()
    with uploader(dataset) as pool:
        pool.enqueue(manifest, item)
    checkpoint = manifest.directory / CHECKPOINT
    data = json.loads(checkpoint.read_bytes())
    data["receipts"]["0"]["sha256"] = "0" * 64
    checkpoint.write_text(json.dumps(data))
    with pytest.raises(ArtifactError, match="CHECKPOINT_CONFLICT"):
        manifest.cleanup(item)
    assert manifest.path(item).exists()


def test_early_remote_verification_failure_closes_stream(dataset):
    _, _, _, _, store = dataset
    closed = []

    def chunks(_key):
        try:
            yield b"too many bytes"
            yield b"unused"
        finally:
            closed.append(True)

    store.iter_chunks = chunks
    with uploader(dataset) as pool:
        with pytest.raises(ArtifactError, match="CHECKSUM_MISMATCH"):
            pool._verify_remote("key", "0" * 64, 1)
    assert closed == [True]
