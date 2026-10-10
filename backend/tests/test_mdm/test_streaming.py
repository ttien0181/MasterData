"""Controlled overlap must be equivalent, bounded and fenced by global barriers."""
import json
import threading
import time
from copy import deepcopy
from dataclasses import replace

import pytest
from masterone.config import Settings, get_settings
from masterone.mdm.engine_adapter import STREAMING_FEATURES, engine_context, feature
from masterone.mdm.generation_staging import GenerationPublisher
from masterone.mdm.models import ResolutionPublication
from masterone.mdm.pipeline.scoring_engine import ScoringConfig, iter_score_batches
from masterone.mdm.pipeline.streaming import ReferenceExchange, StreamingError, compute_scores
from masterone.mdm.resolution_service import _compute, run_pipeline
from masterone.storage.artifact import DatasetManifest
from masterone.storage.object_store import LocalObjectStore

from .test_generation_staging import snapshot as snapshot  # isolated DB fixture
from .test_scoring_engine import full_snapshot


def enable(monkeypatch, *, size=2, v2=False):
    settings = get_settings()
    for name in STREAMING_FEATURES:
        monkeypatch.setattr(settings, name, True)
    for name in ("decision_semantics_v2_enabled", "cluster_safety_v2_enabled"):
        monkeypatch.setattr(settings, name, v2)
    monkeypatch.setattr(settings, "stream_partition_rows", size)
    monkeypatch.setattr(settings, "stream_partition_bytes", 256*1024)
    monkeypatch.setattr(settings, "stream_queue_bytes", 512*1024)
    monkeypatch.setattr(settings, "stream_queue_partitions", 2)


def business(result):
    # DecisionStore and JSON transfer can differ in container representation.
    return json.loads(json.dumps({key: list(result[key]) if key == "pairs" else result.get(key, {})
        for key in ("pairs", "proposals", "review_cases", "candidate_count", "insufficient_count",
                    "candidate_metrics", "cluster_metrics")}, sort_keys=True))


def close(result):
    if hasattr(result["pairs"], "close"):
        result["pairs"].close()


@pytest.mark.parametrize("size", [1, 2, 4])
@pytest.mark.parametrize("v2", [False, True])
def test_equivalence_sizes_completion_order_and_barriers(tmp_path, monkeypatch, size, v2):
    settings = get_settings()
    for name in ("decision_semantics_v2_enabled", "cluster_safety_v2_enabled"):
        monkeypatch.setattr(settings, name, v2)
    records = full_snapshot()
    # Cross-partition exact-name candidates create multiple SCORE/DECIDE
    # partitions while the shared contact census still spans all seven records.
    for row in records[2:]:
        row["normalized_fields"]["name"] = "nguyen van an"
    vetoes = frozenset({("a", "b")})
    serial = _compute(deepcopy(records), "run", "person-v3", negative_pairs=vetoes,
        scoring_output_dir=tmp_path / "serial", execution_blocked_pairs=vetoes)
    enable(monkeypatch, size=size, v2=v2)
    events = []
    lock = threading.Lock()
    second_normalized = threading.Event()
    second_scored, second_decided = threading.Event(), threading.Event()

    def event(name, partition=None):
        # Make the first partition finish AFTER the next one in each consumer.
        if name == "normalize:pass_one" and partition == "p00000000":
            assert second_normalized.wait(5)
        if name == "score:batch" and partition == "p00000000":
            assert second_scored.wait(5)
        if name == "decide:pair_local" and partition == "p00000000":
            assert second_decided.wait(5)
        with lock:
            events.append((name, partition))
        if name == "normalized:local" and partition == "p00000001":
            second_normalized.set()
        if name == "scores:local" and partition == "p00000001":
            second_scored.set()
        if name == "decision-prep:local" and partition == "p00000001":
            second_decided.set()

    stream = _compute(iter(deepcopy(records)), "run", "person-v3", negative_pairs=vetoes,
        scoring_output_dir=tmp_path / "stream", execution_blocked_pairs=vetoes,
        _stream_store=LocalObjectStore(tmp_path / "remote"), _stream_event=event)
    try:
        assert business(stream) == business(serial)
        assert stream["streaming"]["mode"] == "streaming"
        for key in ("snapshot_hash", "scores_hash", "model_hash", "config_hash", "input_pairs", "coverage"):
            assert stream["scoring_metrics"][key] == serial["scoring_metrics"][key]
        assert list(iter_score_batches(stream["scoring_artifact"], 100)) == list(iter_score_batches(serial["scoring_artifact"], 100))
        positions = {name: i for i, (name, _) in enumerate(events) if not name.endswith(":local")}
        assert positions["normalized:complete"] < positions["normalize:global_statistics"] < positions["build:global_union"]
        assert positions["build:global_union"] < next(i for i, e in enumerate(events) if e[0] == "score:batch")
        assert positions["score:complete"] < positions["scores:durable"] < positions["decide:global_barrier"]
        assert positions["decision-prep:durable"] < positions["decide:global_barrier"]
        if size == 1:
            normalize_order = [partition for name, partition in events if name == "normalized:local"]
            assert normalize_order.index("p00000001") < normalize_order.index("p00000000")
            assert next(i for i, e in enumerate(events) if e[0] == "normalized:local") < positions["prepare:complete"]
        for stage in ("scores:local", "decision-prep:local"):
            order = [partition for name, partition in events if name == stage]
            assert order.index("p00000001") < order.index("p00000000")
    finally:
        close(serial)
        close(stream)


@pytest.mark.parametrize("boundary", ["prepare_normalize", "normalize_build", "build_score", "score_decide", "decide_materialize"])
def test_individual_flags_keep_other_barriers_serial(tmp_path, monkeypatch, boundary):
    enable(monkeypatch, size=1)
    for name in STREAMING_FEATURES:
        monkeypatch.setattr(get_settings(), name, name == "stream_" + boundary + "_enabled")
    events = []
    result = _compute(full_snapshot(), "run", "person-v3", scoring_output_dir=tmp_path / "work",
        _stream_store=LocalObjectStore(tmp_path / "remote"), _stream_event=lambda name, part=None: events.append(name))
    try:
        if boundary != "prepare_normalize":
            assert events.index("prepare:complete") < events.index("normalize:pass_one")
        if boundary != "build_score":
            assert events.index("candidates:complete") < events.index("score:batch")
        if boundary != "score_decide":
            assert events.index("score:complete") < events.index("decide:pair_local")
    finally:
        close(result)


def test_flags_default_off_and_old_profile_pins_off(monkeypatch):
    settings = Settings(_env_file=None)
    assert all(not getattr(settings, name) for name in STREAMING_FEATURES)
    enable(monkeypatch)
    with engine_context({"generation_staging_enabled": True}):
        assert all(not feature(name) for name in STREAMING_FEATURES)


@pytest.mark.parametrize("reason", ["store", "semantic", "splink", "disabled"])
def test_explicit_serial_fallback(tmp_path, monkeypatch, reason):
    enable(monkeypatch)
    config = ScoringConfig()
    store = LocalObjectStore(tmp_path / "remote")
    if reason == "store":
        store = None
    elif reason == "semantic":
        monkeypatch.setattr(get_settings(), "semantic_address_enabled", True)
    elif reason == "splink":
        # Eligibility only: never execute Splink without its pinned model.
        from masterone.mdm.pipeline.streaming import fallback_reason
        assert fallback_reason(replace(config, mode="splink"), store) == "STREAMING_SPLINK_SERIAL"
        return
    else:
        for name in STREAMING_FEATURES:
            monkeypatch.setattr(get_settings(), name, False)
    result = _compute([], "run", "v", scoring_config=config, scoring_output_dir=tmp_path / "work", _stream_store=store)
    assert result["streaming"]["mode"] == "serial"
    assert result["streaming"]["reason"] in {"STREAMING_STORE_UNAVAILABLE", "STREAMING_SEMANTIC_SERIAL", "STREAMING_DISABLED"}


def fixture_reference(tmp_path):
    import pyarrow as pa
    from masterone.storage.background_upload import PartitionReference
    schema = pa.schema([("value", pa.int64())])
    manifest = DatasetManifest.create(tmp_path / "partition", run_id="run", attempt_id="attempt", artifact_id="fixture", schema=schema)
    item = manifest.write_partition("p00000000", [pa.Table.from_pylist([{"value": 1}], schema=schema)], schema, max_bytes=65536)
    return manifest, PartitionReference(manifest.directory, item)


def test_full_queue_backpressures_before_production_and_keeps_lease(tmp_path):
    manifest, ref = fixture_reference(tmp_path)
    manifest.record_upload(ref.metadata)
    entered, release, produced = threading.Event(), threading.Event(), threading.Event()

    def slow(_):
        entered.set()
        assert release.wait(3)

    with ReferenceExchange(slow, workers=2, max_partitions=1, max_bytes=65536, timeout=2) as queue:
        queue.submit(lambda: ref, 65536)
        assert entered.wait(1)
        assert manifest.cleanup(ref.metadata) is False
        producer = threading.Thread(target=lambda: queue.submit(lambda: (produced.set(), ref)[1], 65536))
        producer.start()
        assert not produced.wait(.05)
        assert queue.pending == 1 and queue.bytes == 65536
        release.set()
        producer.join(2)
        assert produced.is_set()
        queue.finish()
    assert manifest.cleanup(ref.metadata)


def test_byte_budget_timeout_and_consumer_error_fail_closed(tmp_path):
    _, ref = fixture_reference(tmp_path)
    release = threading.Event()
    with ReferenceExchange(lambda _: release.wait(3), max_partitions=4, max_bytes=65536, timeout=.05) as queue:
        queue.submit(lambda: ref, 65536)
        with pytest.raises(StreamingError, match="BACKPRESSURE_TIMEOUT"):
            queue.submit(lambda: pytest.fail("Producer must not run"), 65536)
        release.set()
        queue.finish()
    def fail(_):
        raise ValueError("private")
    with pytest.raises(StreamingError, match="CONSUMER_FAILED"):
        with ReferenceExchange(fail) as queue:
            queue.submit(lambda: ref, 65536)
            queue.finish()


def test_context_profile_copied_to_consumer(tmp_path):
    _, ref = fixture_reference(tmp_path)
    seen = []
    with engine_context({"stream_score_decide_enabled": True}):
        with ReferenceExchange(lambda _: seen.append(feature("stream_score_decide_enabled"))) as queue:
            queue.submit(lambda: ref, 65536)
    assert seen == [True]


def test_slow_upload_overlaps_consumer_but_global_barrier_waits(tmp_path, monkeypatch):
    enable(monkeypatch, size=1)
    upload_entered, release, consumer_entered = threading.Event(), threading.Event(), threading.Event()
    events, errors = [], []
    class SlowStore(LocalObjectStore):
        def upload_partition_file(self, path, key):
            upload_entered.set()
            assert release.wait(5)
            super().upload_partition_file(path, key)
    def event(name, partition=None):
        events.append(name)
        if name == "normalize:pass_one":
            consumer_entered.set()
    def execute():
        try:
            compute_scores(full_snapshot(), "run", workspace=tmp_path / "work", attempt_id="attempt",
                store=SlowStore(tmp_path / "remote"), event=event)
        except BaseException as error:
            errors.append(error)
    thread = threading.Thread(target=execute)
    thread.start()
    try:
        assert upload_entered.wait(2) and consumer_entered.wait(2)
        assert "decide:global_barrier" not in events
    finally:
        release.set()
        thread.join(15)
    assert not thread.is_alive() and not errors
    assert "decide:global_barrier" in events


def test_failed_upload_never_crosses_global_barrier(tmp_path, monkeypatch):
    enable(monkeypatch)
    events = []
    class BrokenStore(LocalObjectStore):
        def upload_partition_file(self, path, key):
            raise ValueError("invalid remote")
    with pytest.raises((StreamingError, ValueError, RuntimeError)):
        compute_scores(full_snapshot(), "run", workspace=tmp_path / "work", store=BrokenStore(tmp_path / "remote"),
            event=lambda name, part=None: events.append(name))
    assert "decide:global_barrier" not in events


def test_generation_stages_references_and_promotes_only_after_durable(tmp_path, monkeypatch, snapshot):
    factory, computed, _ = snapshot
    enable(monkeypatch, size=1)
    publisher = GenerationPublisher(factory, store=LocalObjectStore(tmp_path / "remote"), batch_size=2)
    identity = publisher.begin("run", config={"test": "streaming"}, attempt_id="attempt")
    publisher.stage(identity, computed)
    with factory() as db:
        assert db.get(ResolutionPublication, "run").active_generation_id is None
    publisher.promote(identity)
    with factory() as db:
        assert db.get(ResolutionPublication, "run").active_generation_id == identity.generation_id


def test_materialize_upload_failure_cannot_seal_or_promote(tmp_path, monkeypatch, snapshot):
    from masterone.mdm.generation_staging import GenerationError
    from masterone.mdm.models import ResolutionGeneration
    factory, computed, _ = snapshot
    enable(monkeypatch)
    class BrokenStore(LocalObjectStore):
        def upload_partition_file(self, path, key):
            raise ValueError("failed")
    publisher = GenerationPublisher(factory, store=BrokenStore(tmp_path / "remote"))
    identity = publisher.begin("run", config={"test": "broken"}, attempt_id="attempt")
    with pytest.raises((StreamingError, ValueError, RuntimeError)):
        publisher.stage(identity, computed)
    with factory() as db:
        assert db.get(ResolutionGeneration, identity.generation_id).state != "SEALED"
        assert db.get(ResolutionPublication, "run").active_generation_id is None
    with pytest.raises(GenerationError):
        publisher.promote(identity)


@pytest.mark.parametrize("imported", [True, False])
def test_stage_runner_streaming_preserves_imported_normalization(tmp_path, monkeypatch, imported):
    from masterone.mdm.pipeline.stage_runner import StageRunner
    records = full_snapshot()
    if not imported:
        for record in records:
            record["normalized_fields"] = {}
    serial = StageRunner("run", "serial", output_dir=tmp_path / "serial")
    streamed = StageRunner("run", "attempt", output_dir=tmp_path / "stream")
    first = second = None
    try:
        first = serial.run_all(deepcopy(records))
        enable(monkeypatch, size=1, v2=True)
        # Compare the same global clustering policy; the runner's raw imported
        # fields must survive pass one, rather than being standardized again.
        monkeypatch.setattr(get_settings(), "cluster_safety_v2_enabled", False)
        monkeypatch.setattr(get_settings(), "decision_semantics_v2_enabled", False)
        store = LocalObjectStore(tmp_path / "remote")
        monkeypatch.setattr("masterone.storage.r2.get_store", lambda _: store)
        second = streamed.run_all(deepcopy(records))
        def canonical(output):
            result = business(output)
            result["pairs"].sort(key=lambda pair: pair["id"])
            return result
        assert canonical(first) == canonical(second)
        assert second["streaming"]["mode"] == "streaming"
    finally:
        if first:
            close(first)
        if second:
            close(second)
        serial.con.close()
        streamed.con.close()


def test_native_staging_replay_is_immutable_and_idempotent(tmp_path, monkeypatch, snapshot):
    from masterone.mdm.models import ResolutionPartitionReceipt
    factory, _, records = snapshot
    enable(monkeypatch)
    store = LocalObjectStore(tmp_path / "remote")
    publisher = GenerationPublisher(factory, store=store)
    identity = publisher.begin("run", config={"test": "replay"}, attempt_id="attempt")
    computed = _compute(records, "run", "v", scoring_output_dir=tmp_path / "work",
        _stream_store=store, _attempt_id=identity.attempt_id)
    try:
        publisher.stage(identity, computed)
        with factory() as db:
            receipt_count = db.query(ResolutionPartitionReceipt).count()
        publisher.stage(identity, computed)
        with factory() as db:
            assert db.query(ResolutionPartitionReceipt).count() == receipt_count
        publisher.promote(identity)
    finally:
        close(computed)


@pytest.mark.parametrize("records", [[], [{"id": "only", "normalized_fields": {}, "raw_fields": {}, "quality_flags": []}]])
def test_empty_candidates_complete_all_barriers(tmp_path, monkeypatch, records):
    enable(monkeypatch)
    serial = _compute(deepcopy(records), "run", "v", scoring_output_dir=tmp_path / "serial",
        _stream_store=LocalObjectStore(tmp_path / "remote0"))
    result = _compute(deepcopy(records), "run", "v", scoring_output_dir=tmp_path / "stream",
        _stream_store=LocalObjectStore(tmp_path / "remote"))
    assert business(result) == business(serial)
    assert result["candidate_count"] == 0 and result["scoring_metrics"]["coverage"] == "complete"


def test_missing_score_fails_before_global_decide(tmp_path, monkeypatch):
    from masterone.mdm.pipeline.scoring_engine import ScoringCoverageError, ScoringEngine
    enable(monkeypatch)
    original = ScoringEngine.score_batch
    monkeypatch.setattr(ScoringEngine, "score_batch", lambda self, rows: original(self, rows)[:-1])
    events = []
    with pytest.raises(ScoringCoverageError):
        compute_scores(full_snapshot(), "run", workspace=tmp_path / "work", store=LocalObjectStore(tmp_path / "remote"),
            event=lambda name, part=None: events.append(name))
    assert "decide:global_barrier" not in events


def test_consumer_timeout_retains_lease_until_actual_completion(tmp_path):
    manifest, ref = fixture_reference(tmp_path)
    manifest.record_upload(ref.metadata)
    release = threading.Event()
    try:
        with pytest.raises(StreamingError, match="CONSUMER_TIMEOUT"):
            with ReferenceExchange(lambda _: release.wait(3), timeout=.03) as queue:
                queue.submit(lambda: ref, 65536)
        assert manifest.cleanup(ref.metadata) is False
    finally:
        release.set()
    deadline = time.monotonic() + 2
    while queue.pending and time.monotonic() < deadline:
        time.sleep(.005)
    assert manifest.cleanup(ref.metadata)


def test_worker_entry_path_pins_flags_and_runs_staged_streaming(tmp_path, monkeypatch, snapshot):
    from masterone.mdm.job_queue import claim_next, ownership
    from masterone.mdm.models import ResolutionEngineSelection, ResolutionJobAttempt
    factory, _, _ = snapshot
    enable(monkeypatch)
    store = LocalObjectStore(tmp_path / "remote")
    monkeypatch.setattr("masterone.storage.r2.get_store", lambda _: store)
    monkeypatch.setattr("masterone.mdm.generation_staging.get_store", lambda _: store)
    monkeypatch.setattr("masterone.mdm.pipeline.streaming.get_store", lambda _: store)
    progress = []
    claim = claim_next(factory, "worker", lease_seconds=120)
    with ownership(claim):
        run_pipeline("run", factory, lambda *args: progress.append(args))
    assert "promoting" in [item[0] for item in progress]
    with factory() as db:
        configuration = db.get(ResolutionEngineSelection, "run").configuration
        assert all(configuration[name] for name in STREAMING_FEATURES)
        assert db.get(ResolutionPublication, "run").active_generation_id is not None
        assert db.get(ResolutionJobAttempt, claim.attempt_id).status == "SUCCEEDED"
