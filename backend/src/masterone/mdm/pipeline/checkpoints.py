"""Fenced versioned compute checkpoints, immutable objects and conservative GC.

Database is the authoritative commit index. Remote objects or local-ready files
alone are never adopted. Restore copies verified bytes into a new attempt's
manifest, so different attempts never share writable artifact identity.
"""
import io
import json
import logging
import os
from contextlib import ExitStack, contextmanager
from hashlib import sha256
from pathlib import Path
from typing import Literal

import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select

from masterone.config import get_settings
from masterone.mdm.generation_staging import config_identity, digest, encode, fingerprints, lock_run
from masterone.mdm.job_queue import current_claim, guard, ownership
from masterone.mdm.models import (
    ResolutionCheckpoint as Checkpoint,
)
from masterone.mdm.models import (
    ResolutionCheckpointDependency as Dependency,
)
from masterone.mdm.models import (
    ResolutionCheckpointObject as Object,
)
from masterone.mdm.models import (
    ResolutionPublication,
    ResolutionRecord,
    ResolutionTask,
)
from masterone.mdm.pipeline.golden_v2 import record_payload
from masterone.storage.artifact import (
    ArtifactError,
    ArtifactManifest,
    PartitionArtifact,
    atomic_json,
    file_lock,
    schema_version,
)

log = logging.getLogger(__name__)
FORMAT = "p119_compute_checkpoint_v1"
REFERENCE_VERSION = "p119_onemaster_boundaries_v1"
HASH = r"^[a-f0-9]{64}$"
STAGES = ("prepare", "normalized", "candidates", "scores", "decision-prep", "cluster")


def snapshot_payload(row):
    return {key: row.get(key) for key in ("id", "external_id", "source_system", "entity_type",
        "raw_fields", "normalized_fields", "quality_flags", "source_updated_at")}


class CheckpointError(RuntimeError):
    """Only stable safe codes; payloads/credentials never enter logs."""


class Pins(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    snapshot_id: str = Field(pattern=HASH)
    config_hash: str = Field(pattern=HASH)
    model_hash: str = Field(pattern=HASH)
    reference_version: str
    stage_version: str = Field(pattern=HASH)


class CheckpointManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    format: Literal["p119_compute_checkpoint_v1"] = FORMAT
    run_id: str
    attempt_id: str
    stage: Literal["prepare", "normalized", "candidates", "scores", "decision-prep", "cluster"]
    scope: str
    pins: Pins
    input_artifact_hash: str = Field(pattern=HASH)
    output_artifact_hash: str = Field(pattern=HASH)
    local_complete: Literal[True] = True
    durable_complete: bool
    dependencies: dict[str, str] = Field(default_factory=dict)
    partition: PartitionArtifact | None = None
    artifact: ArtifactManifest | None = None
    object_manifest_key: str | None = None
    metadata: dict = Field(default_factory=dict)
    local_directory: str


def artifact_hash(data):
    return digest([data.schema_version, [[p.partition_id, p.sha256, p.row_count, p.byte_size]
        for p in sorted(data.partitions, key=lambda item: item.partition_id)]])


class DatabasePartitionStore:
    """Bounded immutable object adapter when R2 is absent. DB commit is durable.

    This is not a queue/broker. One BLOB at most partition/manifest limit is
    materialized by the driver; it never holds a complete stage in memory.
    """
    def __init__(self, factory, run_id, claim=None):
        self.factory, self.run_id, self.claim = factory, run_id, claim
        self.limit = max(get_settings().stream_partition_bytes, 8*1024**2)

    def exists(self, key):
        with self.factory() as db:
            row = db.get(Object, key)
            return row is not None and row.run_id == self.run_id

    def upload_bytes(self, content, key, content_type=None):
        if not content or len(content) > self.limit or not key.startswith(f"partitions/v1/{self.run_id}/"):
            raise CheckpointError("CHECKPOINT_OBJECT_LIMIT")
        checksum = sha256(content).hexdigest()
        with ownership(self.claim), self.factory.begin() as db:
            guard(db, self.run_id)
            row = db.get(Object, key)
            if row is not None:
                if (row.run_id, row.sha256, row.byte_count, row.content) != (self.run_id, checksum, len(content), content):
                    raise CheckpointError("CHECKPOINT_OBJECT_CONFLICT")
            else:
                db.add(Object(key=key, run_id=self.run_id, sha256=checksum, byte_count=len(content), content=content))
            guard(db, self.run_id)

    def upload_partition_file(self, path, key):
        path = Path(path)
        if path.stat().st_size > self.limit:
            raise CheckpointError("CHECKPOINT_OBJECT_LIMIT")
        self.upload_bytes(path.read_bytes(), key)

    def download_bytes(self, key):
        with self.factory() as db:
            row = db.get(Object, key)
            if (row is None or row.run_id != self.run_id or row.byte_count > self.limit
                    or len(row.content) != row.byte_count or sha256(row.content).hexdigest() != row.sha256):
                raise CheckpointError("CHECKPOINT_OBJECT_CORRUPT")
            return row.content

    def iter_chunks(self, key, chunk_size=1024*1024):
        content = self.download_bytes(key)
        for offset in range(0, len(content), chunk_size):
            yield content[offset:offset+chunk_size]


class CheckpointManager:
    def __init__(self, factory, run_id, attempt_id, workspace, *, policy_version,
                 candidate_config=None, scoring_config=None, negative_pairs=None,
                 execution_blocked_pairs=frozenset(), store=None, fault=None, event=None, normalize=False):
        self.factory, self.run_id, self.attempt_id = factory, run_id, attempt_id
        self.workspace, self.claim = Path(workspace).resolve(), current_claim()
        self.store = store or DatabasePartitionStore(factory, run_id, self.claim)
        self.fault, self.event = fault or (lambda *_: None), event or (lambda *_: None)
        self.inputs, self.stage_inputs, self.metadata, self.completed, self.bindings = {}, {}, {}, {}, {}
        with self.transaction() as db:
            snapshot, review, _ = fingerprints(db, run_id)
            records = db.scalars(select(ResolutionRecord).where(ResolutionRecord.run_id == run_id)
                .order_by(ResolutionRecord.id).execution_options(yield_per=512))
            payload_digest = sha256()
            for row in records:
                payload_digest.update(encode(snapshot_payload(record_payload(row))) + b"\n")
            self.payload_hash = payload_digest.hexdigest()
        self.snapshot = snapshot
        configuration = config_identity(policy_version, scoring_config, candidate_config)
        backend = "DB" if isinstance(self.store, DatabasePartitionStore) else "OBJECT"
        self.config_hash = digest([configuration, backend, getattr(self.store, "bucket", None),
            review, sorted(negative_pairs or ()), sorted(execution_blocked_pairs), normalize])
        from masterone.mdm.pipeline import matching
        root = Path(matching.__file__).parent
        self.model_hash = digest({name: sha256((root / name).read_bytes()).hexdigest() for name in (
            "matching.py", "standardization.py", "hard_constraints.py", "evidence_floor.py", "policy_engine.py", "decision_semantics.py")})
        self.snapshot_id = digest([run_id, snapshot, self.payload_hash])
        self.stage_code_hash = digest([sha256(Path(__file__).read_bytes()).hexdigest(),
            sha256(Path(__file__).with_name("streaming.py").read_bytes()).hexdigest()])

    @contextmanager
    def transaction(self):
        with ownership(self.claim), self.factory.begin() as db:
            guard(db, self.run_id, attempt_id=self.attempt_id)
            yield db
            guard(db, self.run_id, attempt_id=self.attempt_id)

    def pins(self, stage):
        return Pins(snapshot_id=self.snapshot_id, config_hash=self.config_hash, model_hash=self.model_hash,
            reference_version=REFERENCE_VERSION,
            stage_version=digest([stage, self.stage_code_hash]))

    def report(self, stage, scope, reason, origin=None):
        fields = {"run_id": self.run_id, "attempt_id": self.attempt_id,
            "stage": stage, "partition": scope, "reason": reason, "origin_attempt_id": origin}
        log.info("checkpoint_resolution", extra={"checkpoint": fields})
        self.event("checkpoint:" + reason, stage)

    def bind(self, writer, input_hash, dependencies=()):
        stage = writer.manifest.read().artifact_id
        self.bindings[stage] = writer
        self.stage_inputs[stage] = (input_hash, tuple(dependencies))
        # Pin before the first file can be published, including upload/DB races.
        atomic_json(writer.manifest.directory / ".checkpoint-pin", {"format": FORMAT, "run_id": self.run_id,
            "attempt_id": self.attempt_id}, writer.uploader.disk_guard)

    def input(self, writer, scope, checksum):
        self.inputs[(writer.manifest.read().artifact_id, scope)] = checksum

    def _manifest(self, writer, scope, durable, item=None, object_key=None):
        data = writer.manifest.read()
        stage = data.artifact_id
        input_hash, upstream = self.stage_inputs[stage]
        dependencies = {}
        if item is None:
            with self.factory() as db:
                for ref in data.partitions:
                    key = self._id(stage, ref.partition_id, self.inputs.get((stage, ref.partition_id), input_hash), ref.sha256)
                    row = db.get(Checkpoint, key)
                    if row is None or row.state != "DURABLE_COMPLETE":
                        raise CheckpointError("CHECKPOINT_PARTITION_BARRIER")
                    dependencies[key] = row.manifest_sha256
                for key in upstream:
                    row = db.get(Checkpoint, key)
                    if row is None or row.state != "DURABLE_COMPLETE":
                        raise CheckpointError("CHECKPOINT_DEPENDENCY_BARRIER")
                    dependencies[key] = row.manifest_sha256
        else:
            input_hash = self.inputs.get((stage, scope), input_hash)
        return CheckpointManifest(run_id=self.run_id, attempt_id=self.attempt_id, stage=stage, scope=scope,
            pins=self.pins(stage), input_artifact_hash=input_hash,
            output_artifact_hash=item.sha256 if item else artifact_hash(data), durable_complete=durable,
            dependencies=dependencies, partition=item, artifact=data if item is None else None,
            object_manifest_key=object_key, metadata=self.metadata.get(stage, {}) if item is None else {},
            local_directory=str(writer.manifest.directory))

    def _id(self, stage, scope, input_hash, output_hash):
        return digest([self.run_id, self.attempt_id, stage, scope, digest(self.pins(stage).model_dump()), input_hash, output_hash])

    def persist(self, manifest):
        body = manifest.model_dump(mode="json")
        content = encode(body)
        if len(content) > 8*1024**2:
            raise CheckpointError("CHECKPOINT_MANIFEST_LIMIT")
        checksum = sha256(content).hexdigest()
        identity = self._id(manifest.stage, manifest.scope, manifest.input_artifact_hash, manifest.output_artifact_hash)
        with self.transaction() as db:
            old = db.get(Checkpoint, identity)
            if old is not None:
                if digest(old.manifest) != old.manifest_sha256:
                    raise CheckpointError("CHECKPOINT_MANIFEST_CORRUPT")
                old_body = {**old.manifest, "durable_complete": manifest.durable_complete}
                if old_body != body:
                    raise CheckpointError("CHECKPOINT_ACK_CONFLICT")
                if old.state == "DURABLE_COMPLETE":
                    return identity
                if old.state != "LOCAL_COMPLETE":
                    raise CheckpointError("CHECKPOINT_STATE_CONFLICT")
                old.state, old.manifest, old.manifest_sha256 = "DURABLE_COMPLETE" if manifest.durable_complete else "LOCAL_COMPLETE", body, checksum
            else:
                db.add(Checkpoint(id=identity, run_id=self.run_id, attempt_id=self.attempt_id, stage=manifest.stage,
                    scope=manifest.scope, pins_sha256=digest(manifest.pins.model_dump()), input_sha256=manifest.input_artifact_hash,
                    output_sha256=manifest.output_artifact_hash, manifest_sha256=checksum, manifest=body,
                    state="DURABLE_COMPLETE" if manifest.durable_complete else "LOCAL_COMPLETE"))
                db.flush()
                for parent in manifest.dependencies:
                    db.add(Dependency(child_id=identity, parent_id=parent))
            self.fault("before_checkpoint_commit", manifest.stage, manifest.scope)
        self.fault("after_checkpoint_commit", manifest.stage, manifest.scope)
        return identity

    def local(self, writer, item):
        return self.persist(self._manifest(writer, item.partition_id, False, item))

    def uploaded(self, reference):
        writer = self.bindings[reference.metadata.artifact_id]
        self.persist(self._manifest(writer, reference.metadata.partition_id, True, reference.metadata))
        self.fault("partition_durable", reference.metadata.artifact_id, reference.metadata.partition_id)

    def finish(self, writer, object_key, metadata=None):
        stage = writer.manifest.read().artifact_id
        if metadata is not None:
            self.metadata[stage] = metadata
        if stage == "prepare":
            actual = sha256()
            for item in sorted(writer.manifest.read().partitions, key=lambda p: p.partition_id):
                for batch in writer.manifest.iter_batches(item):
                    for row in batch.to_pylist():
                        actual.update(encode(snapshot_payload(json.loads(row["payload"]))) + b"\n")
            if actual.hexdigest() != self.payload_hash:
                raise CheckpointError("CHECKPOINT_SNAPSHOT_CHANGED")
        manifest = self._manifest(writer, "__stage__", True, object_key=object_key)
        key = self.persist(manifest)
        self.completed[stage] = (key, manifest.output_artifact_hash)
        self.fault("stage_committed", stage, "__stage__")
        return key

    def _bytes(self, key, checksum, count=None):
        limit = max(get_settings().stream_partition_bytes, 8*1024**2)
        output = bytearray()
        for chunk in self.store.iter_chunks(key):
            output.extend(chunk)
            if len(output) > limit:
                raise CheckpointError("CHECKPOINT_OBJECT_LIMIT")
        if (count is not None and len(output) != count) or sha256(output).hexdigest() != checksum:
            raise CheckpointError("CHECKPOINT_OBJECT_CORRUPT")
        return bytes(output)

    def _validate(self, db, row, visited=None):
        visited = set() if visited is None else visited
        if row.id in visited:
            raise CheckpointError("CHECKPOINT_DEPENDENCY_CYCLE")
        visited = visited | {row.id}
        if len(visited) > 64 or row.state != "DURABLE_COMPLETE" or digest(row.manifest) != row.manifest_sha256:
            raise CheckpointError("CHECKPOINT_MANIFEST_CORRUPT")
        try:
            manifest = CheckpointManifest.model_validate(row.manifest)
        except ValidationError:
            raise CheckpointError("CHECKPOINT_MANIFEST_VERSION") from None
        if (not manifest.durable_complete or manifest.run_id != self.run_id or manifest.attempt_id != row.attempt_id
                or manifest.stage != row.stage or manifest.scope != row.scope
                or manifest.pins != self.pins(manifest.stage) or row.pins_sha256 != digest(manifest.pins.model_dump())
                or manifest.input_artifact_hash != row.input_sha256 or manifest.output_artifact_hash != row.output_sha256):
            raise CheckpointError("CHECKPOINT_STALE")
        actual_edges = set(db.scalars(select(Dependency.parent_id).where(Dependency.child_id == row.id)))
        if actual_edges != set(manifest.dependencies):
            raise CheckpointError("CHECKPOINT_DEPENDENCY_CORRUPT")
        for parent, checksum in manifest.dependencies.items():
            dependency = db.get(Checkpoint, parent)
            if dependency is None or dependency.run_id != self.run_id or dependency.manifest_sha256 != checksum:
                raise CheckpointError("CHECKPOINT_DEPENDENCY_STALE")
            self._validate(db, dependency, visited)
        if manifest.partition is not None:
            item = manifest.partition
            if (item.run_id, item.attempt_id, item.artifact_id, item.partition_id, item.sha256) != (
                    manifest.run_id, manifest.attempt_id, manifest.stage, manifest.scope, manifest.output_artifact_hash):
                raise CheckpointError("CHECKPOINT_PARTITION_IDENTITY")
            content = self._bytes(item.key, item.sha256, item.byte_size)
            receipt = {"format": "p119_partition_receipt_v1", "partition": item.model_dump(mode="json"), "key": item.key}
            expected = json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode()
            self._bytes(item.key + ".receipt.json", sha256(expected).hexdigest(), len(expected))
            with pq.ParquetFile(io.BytesIO(content)) as reader:
                if reader.metadata.num_rows != item.row_count or schema_version(reader.schema_arrow) != item.schema_version:
                    raise CheckpointError("CHECKPOINT_PARTITION_SCHEMA")
            return manifest, content
        data = manifest.artifact
        if (data is None or not data.local_complete or not manifest.object_manifest_key
                or (data.run_id, data.attempt_id, data.artifact_id) != (manifest.run_id, manifest.attempt_id, manifest.stage)
                or artifact_hash(data) != manifest.output_artifact_hash):
            raise CheckpointError("CHECKPOINT_STAGE_INCOMPLETE")
        payload = json.dumps(data.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
        self._bytes(manifest.object_manifest_key, sha256(payload).hexdigest(), len(payload))
        if len(manifest.dependencies) < len(data.partitions):
            raise CheckpointError("CHECKPOINT_STAGE_COVERAGE")
        children = [CheckpointManifest.model_validate(db.get(Checkpoint, key).manifest).partition for key in manifest.dependencies]
        actual = {child.partition_id: child for child in children if child is not None and child.artifact_id == manifest.stage}
        if actual != {part.partition_id: part for part in data.partitions}:
            raise CheckpointError("CHECKPOINT_STAGE_COVERAGE")
        parents = {key: CheckpointManifest.model_validate(db.get(Checkpoint, key).manifest)
            for key in manifest.dependencies}
        upstream = {value.stage: (key, value) for key, value in parents.items() if value.artifact is not None}
        expected = {"prepare": set(), "normalized": {"prepare"}, "candidates": {"normalized"},
            "scores": {"candidates"}, "decision-prep": {"scores"}, "cluster": {"decision-prep", "normalized"}}[manifest.stage]
        if set(upstream) != expected or len(upstream) != sum(value.artifact is not None for value in parents.values()):
            raise CheckpointError("CHECKPOINT_INPUT_DEPENDENCIES")
        if expected:
            input_hash = (digest({stage: value.output_artifact_hash for stage, (_, value) in upstream.items()})
                if manifest.stage == "cluster" else next(iter(upstream.values()))[1].output_artifact_hash)
            if manifest.input_artifact_hash != input_hash:
                raise CheckpointError("CHECKPOINT_INPUT_HASH")
        elif manifest.input_artifact_hash != self.snapshot:
            raise CheckpointError("CHECKPOINT_INPUT_HASH")
        if manifest.stage == "cluster":
            prep = upstream["decision-prep"][1]
            score_id = manifest.metadata.get("score_checkpoint_id")
            score_parents = [key for key in prep.dependencies if db.get(Checkpoint, key).stage == "scores"
                and db.get(Checkpoint, key).scope == "__stage__"]
            if score_parents != [score_id]:
                raise CheckpointError("CHECKPOINT_SCORE_REFERENCE")
        return manifest, None

    def lookup(self, stage, scope, input_hash=None):
        with self.transaction() as db:
            query = select(Checkpoint).where(Checkpoint.run_id == self.run_id, Checkpoint.stage == stage,
                Checkpoint.scope == scope, Checkpoint.state == "DURABLE_COMPLETE",
                Checkpoint.pins_sha256 == digest(self.pins(stage).model_dump()))
            if input_hash is not None:
                query = query.where(Checkpoint.input_sha256 == input_hash)
            rows = db.scalars(query.order_by(Checkpoint.created_at.desc(), Checkpoint.id)).all()
            for row in rows:
                try:
                    manifest, content = self._validate(db, row)
                except (CheckpointError, ArtifactError, ValueError, OSError) as error:
                    reason = str(error) if isinstance(error, CheckpointError) else "CHECKPOINT_PARTITION_CORRUPT"
                    row.state = "INVALID"
                    self.report(stage, scope, reason, row.attempt_id)
                    continue
                self.report(stage, scope, "RESUME", row.attempt_id)
                return manifest, content
            reason = "RECOMPUTE"
            if db.scalars(select(Checkpoint.id).where(Checkpoint.run_id == self.run_id, Checkpoint.stage == stage,
                    Checkpoint.scope == scope).limit(1)).first():
                reason = "RECOMPUTE_STALE_OR_INCOMPLETE"
            self.report(stage, scope, reason)
            return None

    def reuse_partition(self, artifacts, writer, scope, input_hash):
        self.input(writer, scope, input_hash)
        cached = self.lookup(writer.manifest.read().artifact_id, scope, input_hash)
        if cached is None:
            return None
        _, content = cached
        with pq.ParquetFile(io.BytesIO(content)) as reader:
            tables = (pa.Table.from_batches([batch]) for batch in reader.iter_batches(batch_size=get_settings().stream_partition_rows))
            item = writer.write(scope, tables)
        self.local(writer, item)
        from masterone.storage.background_upload import PartitionReference
        return PartitionReference(writer.manifest.directory, item)

    def read_artifact(self, manifest):
        for part in sorted(manifest.artifact.partitions, key=lambda item: item.partition_id):
            content = self._bytes(part.key, part.sha256, part.byte_size)
            with pq.ParquetFile(io.BytesIO(content)) as reader:
                if reader.metadata.num_rows != part.row_count or schema_version(reader.schema_arrow) != part.schema_version:
                    raise CheckpointError("CHECKPOINT_PARTITION_SCHEMA")
                yield from reader.iter_batches(batch_size=get_settings().stream_partition_rows)

    def restore_result(self):
        cached = self.lookup("cluster", "__stage__")
        if cached is None:
            return None
        manifest, _ = cached
        result = dict(manifest.metadata["result"])
        from masterone.mdm.pipeline.cluster_policy import enabled
        from masterone.mdm.pipeline.decision_store import DecisionStore
        pairs = DecisionStore(self.workspace) if enabled() else []
        result.update(pairs=pairs, proposals=[], review_cases=[])
        try:
            for batch in self.read_artifact(manifest):
                for row in batch.to_pylist():
                    item = json.loads(row["payload"])
                    if item["kind"] == "pairs":
                        pairs.add(item["value"]) if isinstance(pairs, DecisionStore) else pairs.append(item["value"])
                    elif item["kind"] in {"proposals", "review_cases"}:
                        result[item["kind"]].append(item["value"])
                    else:
                        raise CheckpointError("CHECKPOINT_RESULT_KIND")
            if isinstance(pairs, DecisionStore):
                pairs.seal()
            if result["candidate_count"] != len(pairs) + result["insufficient_count"]:
                raise CheckpointError("CHECKPOINT_RESULT_COVERAGE")
            with self.transaction() as db:
                row = db.get(Checkpoint, manifest.metadata["score_checkpoint_id"])
                if row is None or row.stage != "scores" or row.run_id != self.run_id:
                    raise CheckpointError("CHECKPOINT_SCORE_REFERENCE")
                scores, _ = self._validate(db, row)
            from masterone.mdm.pipeline.scoring_engine import SCORE_SCHEMA
            from masterone.storage.artifact import _LimitedSink
            from masterone.storage.background_upload import BackgroundUploader
            self.workspace.mkdir(parents=True, exist_ok=True)
            path = self.workspace / "scored.parquet"
            with BackgroundUploader(self.store, self.workspace, run_id=self.run_id, attempt_id=self.attempt_id,
                    local_disk_budget=get_settings().stream_disk_bytes) as uploader:
                with _LimitedSink(path, get_settings().stream_disk_bytes, uploader.disk_guard) as sink:
                    with pq.ParquetWriter(sink, SCORE_SCHEMA, compression="zstd") as writer:
                        for batch in self.read_artifact(scores):
                            writer.write_batch(batch)
            result.update(scoring_artifact=str(path), _streaming_workspace=str(self.workspace),
                streaming={"mode": "resume", "reason": "CHECKPOINT_RESULT_RESUMED"})
            return result
        except BaseException:
            if isinstance(pairs, DecisionStore):
                pairs.close()
            raise

    def save_result(self, computed):
        from masterone.mdm.pipeline.streaming import StreamingArtifacts, chunks, json_table
        with StreamingArtifacts(self.workspace, self.run_id, self.attempt_id, self.store,
                resume=self, event=self.event) as artifacts:
            writer = artifacts.writer("cluster", input_hash=digest({"decision-prep": self.completed["decision-prep"][1],
                    "normalized": self.completed["normalized"][1]}),
                dependencies=(self.completed["decision-prep"][0], self.completed["normalized"][0]))
            rows = ({"kind": kind, "value": value} for kind in ("pairs", "proposals", "review_cases")
                for value in computed[kind])
            for index, batch in enumerate(chunks(rows, get_settings().stream_partition_rows)):
                artifacts.write(writer, f"p{index:08d}", json_table(batch))
            writer.close()
            self.metadata["cluster"] = {"score_checkpoint_id": self.completed["scores"][0],
                "result": {k: v for k, v in computed.items() if k not in {
                    "pairs", "proposals", "review_cases", "scoring_artifact", "_streaming_workspace", "streaming"}}}
            artifacts.barrier(writer)


def collect_checkpoint(factory, checkpoint_id, store=None, *, local_root, fault=None):
    """Explicit conservative GC: never delete a live serving/run dependency.

    Retains tombstones and lock files. Current jobs block GC, and lookup/GC share
    the run lock. Remote verification/copy is protected for the entire DB borrow.
    """
    fault = fault or (lambda *_: None)
    with ExitStack() as leases, factory() as db:
        row = db.get(Checkpoint, checkpoint_id)
        if row is None or row.state == "DELETED":
            return True
        lock_run(db, row.run_id)
        task = db.query(ResolutionTask).filter_by(run_id=row.run_id).one_or_none()
        publication = db.get(ResolutionPublication, row.run_id)
        if (task and task.status in {"RUNNING", "QUEUED"}) or (publication and publication.active_generation_id):
            return False
        children = db.scalars(select(Checkpoint).join(Dependency, Dependency.child_id == Checkpoint.id)
            .where(Dependency.parent_id == row.id, Checkpoint.state != "DELETED")).first()
        if children is not None:
            return False
        if digest(row.manifest) != row.manifest_sha256:
            raise CheckpointError("CHECKPOINT_GC_MANIFEST_CORRUPT")
        manifest = CheckpointManifest.model_validate(row.manifest)
        directory = Path(manifest.local_directory)
        if directory.is_symlink() or not directory.resolve().is_relative_to(Path(local_root).resolve()):
            raise CheckpointError("CHECKPOINT_GC_UNSAFE_PATH")
        # Native leases also protect manual/direct consumers without DB job rows.
        if manifest.partition is not None:
            path = directory / manifest.partition.path
            try:
                leases.enter_context(file_lock(path.with_suffix(".lease"), timeout=0))
            except TimeoutError:
                return False
        # Retire before any remote deletion. A crash/DB failure afterward leaves
        # a retryable tombstone, never a reusable pointer to deleted objects.
        row.state = "GC_PENDING"
        run_id = row.run_id
        db.commit()
        fault("after_gc_retire")
        db.begin()
        lock_run(db, run_id)
        row = db.get(Checkpoint, checkpoint_id, populate_existing=True)
        row.state = "DELETED"
        db.flush()
        remaining = db.scalars(select(Checkpoint).where(Checkpoint.run_id == row.run_id,
            Checkpoint.state != "DELETED")).all()
        referenced_keys = set()
        for live in remaining:
            parsed = CheckpointManifest.model_validate(live.manifest)
            if parsed.partition:
                referenced_keys.update((parsed.partition.key, parsed.partition.key + ".receipt.json"))
            if parsed.object_manifest_key:
                referenced_keys.add(parsed.object_manifest_key)
        keys = [manifest.object_manifest_key] if manifest.object_manifest_key else [manifest.partition.key, manifest.partition.key + ".receipt.json"]
        for key in keys:
            if key in referenced_keys:
                continue
            if store is not None:
                store.delete(key)
            else:
                obj = db.get(Object, key)
                if obj is not None:
                    db.delete(obj)
        if not any(live.manifest.get("local_directory") == str(directory) for live in remaining):
            # Only the exact manifest directory recorded by this checkpoint;
            # never recursively delete an attempt directory or its lock files.
            with file_lock(directory / ".manifest.lock"):
                (directory / ".checkpoint-pin").unlink(missing_ok=True)
            if manifest.partition and (directory / manifest.partition.path).is_file():
                path = directory / manifest.partition.path
                os.chmod(path, 0o600)
                path.unlink()  # exclusive GC lease retained across both commits
        fault("before_gc_commit")
        db.commit()
        return True
