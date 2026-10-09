"""Recovery never substitutes availability for coverage, ownership or durability."""
import logging
import multiprocessing
import time
from copy import deepcopy
from dataclasses import replace

import pytest
from masterone.config import get_settings
from masterone.mdm.models import ResolutionCheckpoint, ResolutionCheckpointObject, ResolutionTask
from masterone.mdm.pipeline.checkpoints import (
    CheckpointError,
    CheckpointManager,
    CheckpointManifest,
    collect_checkpoint,
)
from masterone.mdm.pipeline.scoring_engine import ScoringConfig, ScoringEngine, iter_score_batches
from masterone.mdm.resolution_service import _compute
from masterone.storage.artifact import DatasetManifest, partition_lease

from .test_generation_staging import snapshot as snapshot
from .test_streaming import business, close


def enabled(monkeypatch):
    monkeypatch.setattr(get_settings(), "pipeline_resume_enabled", True)
    monkeypatch.setattr(get_settings(), "stream_partition_rows", 1)
    monkeypatch.setattr(get_settings(), "stream_partition_bytes", 256*1024)
    monkeypatch.setattr(get_settings(), "stream_queue_bytes", 512*1024)


def compute(factory, records, root, attempt, **kwargs):
    return _compute(deepcopy(records), "run", "v", _checkpoint_factory=factory,
        _attempt_id=attempt, scoring_output_dir=root / attempt, **kwargs)


def manager(factory, root, attempt="attempt"):
    return CheckpointManager(factory, "run", attempt, root / attempt, policy_version="v")


def test_full_resume_skips_compute_and_preserves_business(tmp_path, monkeypatch, snapshot, caplog):
    factory, _, records = snapshot
    enabled(monkeypatch)
    first = compute(factory, records, tmp_path, "first")
    def fail(*args, **kwargs):
        pytest.fail("Committed SCORE must not rerun")
    monkeypatch.setattr(ScoringEngine, "score_batch", fail)
    caplog.set_level(logging.INFO, logger="masterone.mdm.pipeline.checkpoints")
    second = compute(factory, records, tmp_path, "second")
    try:
        assert business(first) == business(second)
        assert second["streaming"]["mode"] == "resume"
        assert list(iter_score_batches(first["scoring_artifact"], 100)) == list(iter_score_batches(second["scoring_artifact"], 100))
        assert any(getattr(record, "checkpoint", {}).get("reason") == "RESUME" for record in caplog.records)
        with factory() as db:
            stages = {row.stage for row in db.query(ResolutionCheckpoint).filter_by(scope="__stage__", state="DURABLE_COMPLETE")}
            assert stages == {"prepare", "normalized", "candidates", "scores", "decision-prep", "cluster"}
    finally:
        close(first)
        close(second)


@pytest.mark.parametrize("stage", ["prepare", "normalized", "candidates", "scores", "decision-prep", "cluster"])
def test_fault_after_each_committed_stage_recovers(tmp_path, monkeypatch, snapshot, stage):
    factory, _, records = snapshot
    enabled(monkeypatch)
    def crash(phase, current, scope):
        if phase == "stage_committed" and current == stage:
            raise RuntimeError("injected boundary crash")
    with pytest.raises(RuntimeError, match="injected boundary crash"):
        compute(factory, records, tmp_path, "first", _checkpoint_fault=crash)
    resumed = compute(factory, records, tmp_path, "second")
    monkeypatch.setattr(get_settings(), "pipeline_resume_enabled", False)
    serial = _compute(records, "run", "v", scoring_output_dir=tmp_path / "serial")
    try:
        assert business(serial) == business(resumed)
    finally:
        close(serial)
        close(resumed)


def test_local_complete_is_not_eligible_for_resume(tmp_path, monkeypatch, snapshot):
    factory, _, records = snapshot
    enabled(monkeypatch)
    first = compute(factory, records, tmp_path, "first")
    close(first)
    with factory.begin() as db:
        for row in db.query(ResolutionCheckpoint):
            row.state = "LOCAL_COMPLETE"
    current = manager(factory, tmp_path)
    with factory() as db:
        row = db.query(ResolutionCheckpoint).filter_by(stage="scores", scope="__stage__").one()
        assert current.lookup("scores", "__stage__", row.input_sha256) is None


def test_manifest_corruption_recomputes_cluster_without_rescoring(tmp_path, monkeypatch, snapshot):
    factory, _, records = snapshot
    enabled(monkeypatch)
    first = compute(factory, records, tmp_path, "first")
    with factory.begin() as db:
        row = db.query(ResolutionCheckpoint).filter_by(stage="cluster", scope="__stage__").one()
        row.manifest = {**row.manifest, "format": "corrupted"}
    monkeypatch.setattr(ScoringEngine, "score_batch", lambda *args, **kwargs: pytest.fail("SCORE checkpoint still valid"))
    second = compute(factory, records, tmp_path, "second")
    try:
        assert business(first) == business(second)
        with factory() as db:
            assert db.query(ResolutionCheckpoint).filter_by(stage="cluster", scope="__stage__", state="INVALID").count() == 1
    finally:
        close(first)
        close(second)


def test_corrupt_partition_never_reused(tmp_path, monkeypatch, snapshot):
    factory, _, records = snapshot
    enabled(monkeypatch)
    first = compute(factory, records, tmp_path, "first")
    close(first)
    with factory.begin() as db:
        checkpoint = db.query(ResolutionCheckpoint).filter_by(stage="scores", scope="p00000000").one()
        parsed = CheckpointManifest.model_validate(checkpoint.manifest)
        blob = db.get(ResolutionCheckpointObject, parsed.partition.key)
        blob.content = b"corrupted"
    calls = []
    original = ScoringEngine.score_batch
    monkeypatch.setattr(ScoringEngine, "score_batch", lambda self, rows: (calls.append(len(rows)), original(self, rows))[1])
    second = compute(factory, records, tmp_path, "second")
    close(second)
    assert calls


def test_duplicate_ack_is_idempotent_conflicting_ack_is_rejected(tmp_path, monkeypatch, snapshot):
    factory, _, records = snapshot
    enabled(monkeypatch)
    result = compute(factory, records, tmp_path, "first")
    close(result)
    current = manager(factory, tmp_path, "first")
    with factory() as db:
        row = db.query(ResolutionCheckpoint).filter_by(stage="cluster", scope="__stage__").one()
        manifest = CheckpointManifest.model_validate(row.manifest)
        count = db.query(ResolutionCheckpoint).count()
    assert current.persist(manifest) == current.persist(manifest)
    with pytest.raises(CheckpointError, match="ACK_CONFLICT"):
        current.persist(manifest.model_copy(update={"metadata": {"changed": True}}))
    with factory() as db:
        assert db.query(ResolutionCheckpoint).count() == count


@pytest.mark.parametrize("changed", ["config", "model", "reference", "stage", "snapshot"])
def test_changed_dependencies_reject_reuse(tmp_path, monkeypatch, snapshot, changed):
    factory, _, records = snapshot
    enabled(monkeypatch)
    result = compute(factory, records, tmp_path, "first")
    close(result)
    current = manager(factory, tmp_path)
    with factory() as db:
        row = db.query(ResolutionCheckpoint).filter_by(stage="scores", scope="__stage__").one()
        input_hash = row.input_sha256
    if changed == "config":
        current.config_hash = "a"*64
    elif changed == "model":
        current.model_hash = "b"*64
    elif changed == "snapshot":
        current.snapshot_id = "c"*64
    else:
        original = current.pins
        field = "reference_version" if changed == "reference" else "stage_version"
        monkeypatch.setattr(current, "pins", lambda stage: original(stage).model_copy(update={field: "d"*64}))
    assert current.lookup("scores", "__stage__", input_hash) is None


def test_config_change_recomputes_with_new_scoring_identity(tmp_path, monkeypatch, snapshot):
    factory, _, records = snapshot
    enabled(monkeypatch)
    first = compute(factory, records, tmp_path, "first")
    second = compute(factory, records, tmp_path, "second", scoring_config=replace(ScoringConfig(), batch_size=1))
    try:
        assert second["streaming"]["mode"] == "streaming"
        assert first["scoring_metrics"]["config_hash"] != second["scoring_metrics"]["config_hash"]
    finally:
        close(first)
        close(second)


def test_gc_respects_dependencies_leases_and_workspace(tmp_path, monkeypatch, snapshot):
    factory, _, records = snapshot
    enabled(monkeypatch)
    result = compute(factory, records, tmp_path, "first")
    close(result)
    with factory.begin() as db:
        db.get(ResolutionTask, "task").status = "FAILED"
        root = db.query(ResolutionCheckpoint).filter_by(stage="cluster", scope="__stage__").one()
        part = db.query(ResolutionCheckpoint).filter_by(stage="cluster", scope="p00000000").one()
        root_id, part_id = root.id, part.id
        manifest = CheckpointManifest.model_validate(part.manifest)
    assert not collect_checkpoint(factory, part_id, local_root=tmp_path)
    assert collect_checkpoint(factory, root_id, local_root=tmp_path)
    native = DatasetManifest(manifest.local_directory, manifest.partition)
    assert not native.cleanup(manifest.partition)
    with partition_lease(native.path(manifest.partition)):
        assert not collect_checkpoint(factory, part_id, local_root=tmp_path)
    with pytest.raises(CheckpointError, match="UNSAFE_PATH"):
        collect_checkpoint(factory, part_id, local_root=tmp_path / "outside")
    assert collect_checkpoint(factory, part_id, local_root=tmp_path)
    assert collect_checkpoint(factory, part_id, local_root=tmp_path)


def test_db_transaction_failure_never_creates_durable_checkpoint(tmp_path, monkeypatch, snapshot):
    factory, _, records = snapshot
    enabled(monkeypatch)
    def fail(phase, stage, scope):
        if phase == "before_checkpoint_commit" and stage == "prepare":
            raise RuntimeError("transaction failure")
    with pytest.raises((RuntimeError, ValueError)):
        compute(factory, records, tmp_path, "first", _checkpoint_fault=fail)
    with factory() as db:
        assert db.query(ResolutionCheckpoint).filter_by(stage="prepare").count() == 0
    result = compute(factory, records, tmp_path, "second")
    close(result)


def boundary_worker(database_url, claim, boundary, marker):
    """Spawn-safe worker child; parent actually kills it after a committed gate."""
    from pathlib import Path

    from masterone.mdm.generation_staging import GenerationPublisher
    from masterone.mdm.job_queue import ownership
    from masterone.mdm.pipeline.checkpoints import CheckpointManager
    from masterone.mdm.resolution_service import run_pipeline
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    settings = get_settings()
    settings.pipeline_resume_enabled = True
    settings.stream_partition_rows = 1
    settings.stream_partition_bytes = 256*1024
    settings.stream_queue_bytes = 512*1024
    if boundary == "scores-partial":
        settings.stream_upload_workers = 1
    def pause():
        Path(marker).write_text("committed", encoding="utf-8")
        while True:
            time.sleep(.05)
    original = CheckpointManager.__init__
    def initialize(self, *args, **kwargs):
        def fault(phase, stage, scope):
            if (phase == "stage_committed" and stage == boundary) or (
                    boundary == "scores-partial" and phase == "partition_durable"
                    and stage == "scores" and scope == "p00000000"):
                pause()
        kwargs["fault"] = fault
        original(self, *args, **kwargs)
    CheckpointManager.__init__ = initialize
    if boundary == "materialize":
        original_publisher = GenerationPublisher.__init__
        def publisher_init(self, *args, **kwargs):
            kwargs["failpoint"] = lambda phase: pause() if phase == "after_seal" else None
            original_publisher(self, *args, **kwargs)
        GenerationPublisher.__init__ = publisher_init
    engine = create_engine(database_url)
    try:
        factory = sessionmaker(engine, expire_on_commit=False)
        with ownership(claim):
            run_pipeline("run", factory, lambda *_: None)
    finally:
        engine.dispose()


@pytest.mark.parametrize("boundary", ["prepare", "normalized", "candidates", "scores", "decision-prep", "cluster", "materialize"])
def test_killed_worker_resumes_at_each_stage_without_duplicate_results(tmp_path, monkeypatch, snapshot, boundary):
    from datetime import timedelta

    from masterone.db.base import now_utc
    from masterone.mdm.job_queue import claim_next, ownership, recover
    from masterone.mdm.models import (
        ResolutionJobAttempt,
        ResolutionPair,
        ResolutionProposal,
        ResolutionPublication,
        ResolutionStagedResult,
    )
    from masterone.mdm.resolution_service import run_pipeline
    from sqlalchemy import func, select
    factory, _, records = snapshot
    enabled(monkeypatch)
    old = claim_next(factory, "crash-worker", lease_seconds=180)
    with factory() as db:
        url = db.get_bind().url.render_as_string(hide_password=False)
    marker = tmp_path / "boundary.ready"
    process = multiprocessing.get_context("spawn").Process(target=boundary_worker, args=(url, old, boundary, str(marker)))
    process.start()
    deadline = time.monotonic() + 40
    try:
        while not marker.exists() and process.is_alive() and time.monotonic() < deadline:
            time.sleep(.02)
        assert marker.exists(), f"Child did not reach committed {boundary}; exit={process.exitcode}"
        process.kill()
        process.join(10)
        assert not process.is_alive() and process.exitcode != 0
    finally:
        if process.is_alive():
            process.terminate()
            process.join(10)
        process.close()
    with factory.begin() as db:
        db.get(ResolutionJobAttempt, old.attempt_id).lease_expires_at = now_utc() - timedelta(seconds=1)
    recover(factory)
    new = claim_next(factory, "replacement", lease_seconds=180)
    assert new is not None and new.attempt_id != old.attempt_id
    if boundary in {"scores", "decision-prep", "cluster", "materialize"}:
        monkeypatch.setattr(ScoringEngine, "score_batch", lambda *a, **k: pytest.fail("Committed SCORE should resume"))
    with ownership(new):
        run_pipeline("run", factory, lambda *_: None)
    with factory() as db:
        assert db.get(ResolutionTask, "task").status == "COMPLETED"
        generation_id = db.get(ResolutionPublication, "run").active_generation_id
        assert generation_id
        assert db.query(ResolutionPair).count() == 3
        assert db.query(ResolutionProposal).count() == 1
        # Database uniqueness includes pairs and memberships; one logical row
        # per pair/golden/review/member key, even after reloading old receipts.
        counts = dict(db.execute(select(ResolutionStagedResult.kind, func.count()).where(
            ResolutionStagedResult.generation_id == generation_id).group_by(ResolutionStagedResult.kind)).all())
        assert counts["pair"] == 3 and counts["membership"] == 3 and counts["golden"] == 1


def test_r2_failure_leaves_only_local_checkpoint_then_recovers(tmp_path, monkeypatch, snapshot):
    from masterone.storage.object_store import LocalObjectStore
    factory, _, records = snapshot
    enabled(monkeypatch)
    class BrokenR2(LocalObjectStore):
        def upload_partition_file(self, path, key):
            raise OSError("injected R2 outage")
    with pytest.raises((RuntimeError, ValueError)):
        compute(factory, records, tmp_path, "first", _stream_store=BrokenR2(tmp_path / "remote"))
    with factory() as db:
        assert db.query(ResolutionCheckpoint).filter_by(state="DURABLE_COMPLETE").count() == 0
    result = compute(factory, records, tmp_path, "second", _stream_store=LocalObjectStore(tmp_path / "remote"))
    close(result)


def test_cancel_protects_active_generation_and_reviewer_state(tmp_path, monkeypatch, snapshot):
    from masterone.mdm.generation_staging import GenerationPublisher
    from masterone.mdm.job_queue import OwnershipLostError, claim_next, ownership, recover, request_cancel
    from masterone.mdm.models import ResolutionPair, ResolutionProposal, ResolutionPublication, ResolutionReviewCase
    from masterone.mdm.resolution_service import run_pipeline
    factory, computed, _ = snapshot
    publisher = GenerationPublisher(factory, store=None)
    identity = publisher.begin("run", config={"seed": True}, attempt_id="seed")
    publisher.stage(identity, computed)
    publisher.promote(identity)
    with factory.begin() as db:
        pair = db.query(ResolutionPair).first()
        pair.user_decision, pair.revision = "REJECTED", 2
        proposal = db.query(ResolutionProposal).first()
        proposal.golden_fields = {**proposal.golden_fields, "_golden_override": {"name": "Steward choice"}}
        db.get(ResolutionTask, "task").status = "QUEUED"
    def public():
        with factory() as db:
            return (db.get(ResolutionPublication, "run").active_generation_id,
                [(p.id, p.user_decision, p.revision) for p in db.query(ResolutionPair).order_by(ResolutionPair.id)],
                [(p.id, p.member_record_ids, p.golden_fields, p.status, p.revision) for p in db.query(ResolutionProposal).order_by(ResolutionProposal.id)],
                [(c.id, c.status, c.revision) for c in db.query(ResolutionReviewCase).order_by(ResolutionReviewCase.id)])
    before = public()
    enabled(monkeypatch)
    claim = claim_next(factory, "cancel-owner", lease_seconds=180)
    original = GenerationPublisher.promote
    def cancel(self, current):
        request_cancel(factory, "run")
        return original(self, current)
    monkeypatch.setattr(GenerationPublisher, "promote", cancel)
    with ownership(claim), pytest.raises(OwnershipLostError):
        run_pipeline("run", factory, lambda *_: None)
    recover(factory)
    assert public() == before


def test_checkpoint_migration_roundtrip_preserves_history(tmp_path, monkeypatch):
    from alembic import command
    from masterone.auth.models import User
    from masterone.mdm.migrate import HEAD, alembic_config
    from masterone.mdm.models import ResolutionRun
    from sqlalchemy import create_engine, inspect, text
    from sqlalchemy.orm import Session
    monkeypatch.delenv("MDM_DATABASE_URL", raising=False)
    url = "sqlite:///" + (tmp_path / "migrations.db").as_posix()
    command.upgrade(alembic_config(url), HEAD)
    engine = create_engine(url)
    try:
        with Session(engine) as db, db.begin():
            db.add(User(id="owner", email="x@test", email_normalized="x@test", password_hash="unused", role="STEWARD"))
            db.flush()
            db.add(ResolutionRun(id="run", owner_user_id="owner", policy_version="v", record_count=0))
        with engine.begin() as db:
            db.execute(text("INSERT INTO mdm_checkpoint_objects (key,run_id,sha256,byte_count,content) VALUES ('retained','run',:checksum,1,:content)"), {"checksum": "a"*64, "content": b"x"})
        command.downgrade(alembic_config(url), "0024_job_ownership")
        assert "mdm_checkpoints" in inspect(engine).get_table_names()
        command.upgrade(alembic_config(url), HEAD)
        with engine.connect() as db:
            assert db.execute(text("SELECT content FROM mdm_checkpoint_objects WHERE key='retained'")).scalar_one() == b"x"
    finally:
        engine.dispose()


def test_killed_worker_reuses_only_committed_score_partitions(tmp_path, monkeypatch, snapshot):
    from datetime import timedelta

    from masterone.db.base import now_utc
    from masterone.mdm.job_queue import claim_next, ownership, recover
    from masterone.mdm.models import ResolutionJobAttempt
    from masterone.mdm.resolution_service import run_pipeline
    factory, _, _ = snapshot
    enabled(monkeypatch)
    monkeypatch.setattr(get_settings(), "stream_upload_workers", 1)
    old = claim_next(factory, "partial-owner", lease_seconds=180)
    with factory() as db:
        url = db.get_bind().url.render_as_string(hide_password=False)
    marker = tmp_path / "partial.ready"
    child = multiprocessing.get_context("spawn").Process(target=boundary_worker, args=(url, old, "scores-partial", str(marker)))
    child.start()
    deadline = time.monotonic() + 40
    try:
        while not marker.exists() and child.is_alive() and time.monotonic() < deadline:
            time.sleep(.02)
        assert marker.exists()
        child.kill()
        child.join(10)
        assert not child.is_alive()
    finally:
        if child.is_alive():
            child.terminate()
            child.join(10)
        child.close()
    with factory.begin() as db:
        assert db.query(ResolutionCheckpoint).filter_by(stage="scores", state="DURABLE_COMPLETE").count() == 1
        db.get(ResolutionJobAttempt, old.attempt_id).lease_expires_at = now_utc() - timedelta(seconds=1)
    recover(factory)
    current = claim_next(factory, "resuming-owner", lease_seconds=180)
    calls = []
    original = ScoringEngine.score_batch
    monkeypatch.setattr(ScoringEngine, "score_batch", lambda self, batch: (calls.extend(batch), original(self, batch))[1])
    with ownership(current):
        run_pipeline("run", factory, lambda *_: None)
    assert len(calls) == 2  # three canonical candidates minus the durable first partition


def test_gc_crash_leaves_retired_checkpoint_then_retries(tmp_path, monkeypatch, snapshot):
    factory, _, records = snapshot
    enabled(monkeypatch)
    result = compute(factory, records, tmp_path, "first")
    close(result)
    with factory.begin() as db:
        db.get(ResolutionTask, "task").status = "FAILED"
        checkpoint = db.query(ResolutionCheckpoint).filter_by(stage="cluster", scope="__stage__").one()
        key = checkpoint.id
    def crash(phase):
        if phase == "after_gc_retire":
            raise RuntimeError("GC crash")
    with pytest.raises(RuntimeError, match="GC crash"):
        collect_checkpoint(factory, key, local_root=tmp_path, fault=crash)
    with factory() as db:
        assert db.get(ResolutionCheckpoint, key).state == "GC_PENDING"
    assert collect_checkpoint(factory, key, local_root=tmp_path)
    with factory() as db:
        assert db.get(ResolutionCheckpoint, key).state == "DELETED"


def test_real_db_insert_failure_rolls_back_checkpoint(tmp_path, monkeypatch, snapshot):
    from masterone.storage.background_upload import UploadError
    from sqlalchemy import event
    from sqlalchemy.exc import OperationalError
    factory, _, records = snapshot
    enabled(monkeypatch)
    with factory() as db:
        engine = db.get_bind()
    def reject(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO mdm_checkpoints "):
            raise OperationalError("checkpoint insert", None, RuntimeError("injected DB transaction fault"))
    event.listen(engine, "before_cursor_execute", reject)
    try:
        with pytest.raises((OperationalError, UploadError)):
            compute(factory, records, tmp_path, "first")
    finally:
        event.remove(engine, "before_cursor_execute", reject)
    with factory() as db:
        assert db.query(ResolutionCheckpoint).count() == 0
    result = compute(factory, records, tmp_path, "second")
    close(result)


@pytest.mark.parametrize("empty", [False, True])
def test_resume_with_all_streaming_boundaries_and_empty_snapshot(tmp_path, monkeypatch, snapshot, empty):
    from masterone.mdm.engine_adapter import STREAMING_FEATURES
    from masterone.mdm.models import ResolutionRecord, ResolutionRun
    factory, _, records = snapshot
    enabled(monkeypatch)
    for flag in STREAMING_FEATURES:
        monkeypatch.setattr(get_settings(), flag, True)
    if empty:
        with factory.begin() as db:
            db.query(ResolutionRecord).delete()
            db.get(ResolutionRun, "run").record_count = 0
        records = []
    first = compute(factory, records, tmp_path, "first")
    if empty:
        with factory() as db:
            for stage in ("scores", "decision-prep"):
                partition = db.query(ResolutionCheckpoint).filter_by(stage=stage, scope="p00000000").one()
                complete = db.query(ResolutionCheckpoint).filter_by(stage=stage, scope="__stage__").one()
                assert partition.input_sha256 == complete.input_sha256
    monkeypatch.setattr(ScoringEngine, "score_batch", lambda *a, **k: pytest.fail("Durable result must resume"))
    second = compute(factory, records, tmp_path, "second")
    try:
        assert business(first) == business(second)
        assert second["streaming"]["mode"] == "resume"
    finally:
        close(first)
        close(second)


def test_normalization_mode_invalidates_checkpoints(tmp_path, monkeypatch, snapshot):
    factory, _, records = snapshot
    enabled(monkeypatch)
    result = compute(factory, records, tmp_path, "first")
    close(result)
    current = CheckpointManager(factory, "run", "second", tmp_path / "second", policy_version="v", normalize=True)
    assert current.lookup("cluster", "__stage__") is None


def test_rehashed_wrong_input_dependency_is_rejected(tmp_path, monkeypatch, snapshot, caplog):
    from masterone.mdm.generation_staging import digest
    factory, _, records = snapshot
    enabled(monkeypatch)
    first = compute(factory, records, tmp_path, "first")
    close(first)
    with factory.begin() as db:
        row = db.query(ResolutionCheckpoint).filter_by(stage="cluster", scope="__stage__").one()
        row.input_sha256 = "a"*64
        row.manifest = {**row.manifest, "input_artifact_hash": row.input_sha256}
        row.manifest_sha256 = digest(row.manifest)
    caplog.set_level(logging.INFO, logger="masterone.mdm.pipeline.checkpoints")
    assert manager(factory, tmp_path).lookup("cluster", "__stage__") is None
    assert any(getattr(record, "checkpoint", {}).get("reason") == "CHECKPOINT_INPUT_HASH" for record in caplog.records)
