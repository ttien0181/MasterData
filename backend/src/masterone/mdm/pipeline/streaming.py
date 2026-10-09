"""Selective OneMaster boundaries, retaining P-119's global business algorithms.

Queues contain leased immutable references, never tables. Completion order is
irrelevant: partition IDs define traversal order. Full indexes/cluster state still
use worker memory; this is controlled overlap, not a bounded-memory coordinator.
"""
import json
from concurrent.futures import ThreadPoolExecutor, wait
from contextlib import ExitStack
from contextvars import copy_context
from dataclasses import asdict
from itertools import islice
from pathlib import Path
from threading import Condition
from time import monotonic
from uuid import uuid4

import pyarrow as pa

from masterone.config import get_settings
from masterone.mdm.engine_adapter import STREAMING_FEATURES, feature
from masterone.mdm.pipeline.candidate_engine import CandidateEngine
from masterone.mdm.pipeline.scoring_engine import SCORE_SCHEMA, MemorySnapshot, ScoreResult, ScoringEngine
from masterone.storage.artifact import ArtifactWriter, DatasetManifest, partition_lease
from masterone.storage.background_upload import BackgroundUploader, PartitionReference
from masterone.storage.r2 import get_store

JSON_SCHEMA = pa.schema([("payload", pa.string())])
CANDIDATE_SCHEMA = pa.schema([("left_id", pa.string()), ("right_id", pa.string()),
                            ("reasons", pa.list_(pa.string()))])


class StreamingError(RuntimeError):
    """Safe failure code; never silently rerun a partial attempt serially."""


def requested():
    return any(feature(name) for name in STREAMING_FEATURES) or feature("pipeline_resume_enabled")


def fallback_reason(scoring_config=None, store=None):
    if not requested():
        return "STREAMING_DISABLED"
    if get_settings().semantic_address_enabled:
        return "STREAMING_SEMANTIC_SERIAL"
    if scoring_config is not None and scoring_config.mode != "rule":
        return "STREAMING_SPLINK_SERIAL"
    if store is None:
        return "STREAMING_STORE_UNAVAILABLE"
    return None


def chunks(rows, size):
    iterator = iter(rows)
    while batch := list(islice(iterator, size)):
        yield batch


def json_table(rows):
    return pa.Table.from_pylist([{"payload": json.dumps(row, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False)} for row in rows], schema=JSON_SCHEMA)


def read(reference, schema_json=False):
    manifest = DatasetManifest(reference.directory, reference.metadata)
    for batch in manifest.iter_batches(reference.metadata):
        for row in batch.to_pylist():
            yield json.loads(row["payload"]) if schema_json else row


def references(manifest):
    # Manifest append order can reflect thread completion; never use it as order.
    for item in sorted(manifest.read().partitions, key=lambda p: p.partition_id):
        yield PartitionReference(manifest.directory, item)


class ReferenceExchange:
    """Bound admission BEFORE producing a file, including queued/active work.

    A timed-out consumer keeps its lease until it actually returns. Its private
    writes cannot publish serving state. Worker supervision terminates the child
    process on cancellation, ownership loss or job deadline.
    """
    def __init__(self, consumer, *, workers=1, max_partitions=4, max_bytes=64*1024**2,
                 timeout=60, asynchronous=True):
        if min(workers, max_partitions, max_bytes, timeout) <= 0:
            raise ValueError("Invalid consumer budgets")
        self.consumer, self.max_partitions, self.max_bytes = consumer, max_partitions, max_bytes
        self.timeout, self.asynchronous = timeout, asynchronous
        self.condition, self.pending, self.bytes = Condition(), 0, 0
        self.error, self.closed = None, False
        self.futures = set()
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="partition-consumer")

    def _check(self):
        if self.error:
            raise StreamingError("STREAMING_CONSUMER_FAILED") from self.error
        if self.closed:
            raise StreamingError("STREAMING_QUEUE_CLOSED")

    def submit(self, produce, reserve_bytes):
        if reserve_bytes > self.max_bytes:
            raise StreamingError("STREAMING_QUEUE_BYTE_LIMIT")
        deadline = monotonic() + self.timeout
        with self.condition:
            self._check()
            while self.pending >= self.max_partitions or self.bytes + reserve_bytes > self.max_bytes:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise StreamingError("STREAMING_BACKPRESSURE_TIMEOUT")
                self.condition.wait(remaining)
                self._check()
            self.pending += 1
            self.bytes += reserve_bytes
        lease = None
        try:
            reference = produce()
            if reference.metadata.byte_size > reserve_bytes:
                raise StreamingError("STREAMING_QUEUE_BYTE_LIMIT")
            lease = partition_lease(reference.directory / reference.metadata.path)
            lease.__enter__()
            context = copy_context()  # frozen engine profile AND worker ownership

            def consume():
                try:
                    return context.run(self.consumer, reference)
                finally:
                    lease.__exit__(None, None, None)

            future = self.pool.submit(consume)
        except BaseException:
            if lease is not None:
                lease.__exit__(None, None, None)
            with self.condition:
                self.pending -= 1
                self.bytes -= reserve_bytes
                self.condition.notify_all()
            raise
        with self.condition:
            self.futures.add(future)

        def finished(task):
            with self.condition:
                error = task.exception()
                if error is not None and self.error is None:
                    self.error = error
                self.futures.discard(task)
                self.pending -= 1
                self.bytes -= reserve_bytes
                self.condition.notify_all()
        future.add_done_callback(finished)
        if not self.asynchronous:
            future.result(timeout=self.timeout)
        return reference

    def finish(self):
        with self.condition:
            active = tuple(self.futures)
        _, outstanding = wait(active, timeout=self.timeout)
        if outstanding:
            raise StreamingError("STREAMING_CONSUMER_TIMEOUT")
        for task in active:
            if task.exception() is not None:
                raise StreamingError("STREAMING_CONSUMER_FAILED") from task.exception()
        with self.condition:
            self._check()

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        try:
            if kind is None:
                self.finish()
        finally:
            with self.condition:
                self.closed = True
                self.condition.notify_all()
            self.pool.shutdown(wait=False, cancel_futures=False)


class StreamingArtifacts:
    def __init__(self, workspace, run_id, attempt_id, store, *, event=None, resume=None):
        self.workspace, self.run_id, self.attempt_id = Path(workspace), run_id, attempt_id
        self.settings, self.store = get_settings(), store
        self.event = event or (lambda name, partition=None: None)
        self.stack, self.writers = ExitStack(), []
        self.resume = resume

    def __enter__(self):
        s = self.settings
        self.uploader = self.stack.enter_context(BackgroundUploader(self.store, self.workspace,
            run_id=self.run_id, attempt_id=self.attempt_id,
            max_pending_partitions=s.stream_queue_partitions, max_pending_bytes=s.stream_queue_bytes,
            upload_concurrency=s.stream_upload_workers, local_disk_budget=s.stream_disk_bytes,
            admission_timeout=s.stream_timeout_seconds))
        if self.resume:
            self.uploader.on_uploaded = self.resume.uploaded
        return self

    def __exit__(self, *args):
        return self.stack.__exit__(*args)

    def writer(self, name, schema=JSON_SCHEMA, *, directory=None, input_hash=None, dependencies=()):
        manifest = DatasetManifest.create(self.workspace / (directory or name), run_id=self.run_id,
            attempt_id=self.attempt_id, artifact_id=name, schema=schema, disk_guard=self.uploader.disk_guard)
        writer = ArtifactWriter(manifest, self.uploader, schema=schema,
            max_partition_bytes=self.settings.stream_partition_bytes)
        self.writers.append(writer)
        if self.resume and name in ("prepare", "normalized", "candidates", "scores", "decision-prep", "cluster"):
            self.resume.bind(writer, input_hash or self.resume.snapshot, dependencies)
        return writer

    def write(self, writer, partition, table):
        item = writer.write(partition, [table])
        if self.resume:
            self.resume.local(writer, item)
        self.event(writer.manifest.read().artifact_id + ":local", partition)
        return PartitionReference(writer.manifest.directory, item)

    def exchange(self, consumer, boundary, *, workers=None):
        s = self.settings
        return self.stack.enter_context(ReferenceExchange(consumer,
            workers=workers or s.stream_consumer_workers, max_partitions=s.stream_queue_partitions,
            max_bytes=s.stream_queue_bytes, timeout=s.stream_timeout_seconds,
            asynchronous=feature("stream_" + boundary + "_enabled")))

    def barrier(self, *writers):
        self.uploader.flush(timeout=self.settings.stream_timeout_seconds)
        for writer in writers:
            if not writer.manifest.read().local_complete:
                raise StreamingError("STREAMING_LOCAL_BARRIER")
            key = self.uploader.upload_manifest(writer.manifest)
            if self.resume:
                self.resume.finish(writer, key)
            self.event(writer.manifest.read().artifact_id + ":durable")


def compute_scores(records, run_id, *, workspace, attempt_id=None, store=None,
                   candidate_config=None, scoring_config=None, negative_pairs=None,
                   normalize=False, event=None, resume=None):
    """Return globally validated score artifact + optional private DECIDE prep.

    No clustering/union here. Caller retains its original global decision path.
    Input producer can be a DB iterator. Normalized snapshot/index are still
    held in worker memory, as in the existing candidate/cluster implementation.
    """
    store = store if store is not None else get_store(get_settings())
    reason = fallback_reason(scoring_config, store)
    if reason:
        raise StreamingError(reason)
    attempt_id = attempt_id or str(uuid4())
    with StreamingArtifacts(workspace, run_id, attempt_id, store, event=event, resume=resume) as artifacts:
        size = artifacts.settings.stream_partition_rows
        prepared, normalized = artifacts.writer("prepare"), artifacts.writer("normalized")

        def pass_one(ref):
            from masterone.mdm.pipeline.standardization import standardize
            artifacts.event("normalize:pass_one", ref.metadata.partition_id)
            if resume and resume.reuse_partition(artifacts, normalized, ref.metadata.partition_id, ref.metadata.sha256):
                return
            rows = list(read(ref, True))
            if normalize:
                for row in rows:
                    if not row.get("normalized_fields"):
                        fields, flags = standardize(row.get("raw_fields", {}), row.get("entity_type", "PERSON"))
                        row["normalized_fields"], row["quality_flags"] = fields, flags
            artifacts.write(normalized, ref.metadata.partition_id, json_table(rows))

        early = artifacts.exchange(pass_one, "prepare_normalize")
        for index, batch in enumerate(chunks(records, size)):
            def produce(i=index, b=batch):
                if resume:
                    from masterone.mdm.generation_staging import digest
                    cached = resume.reuse_partition(artifacts, prepared, f"p{i:08d}", digest(b))
                    if cached:
                        return cached
                return artifacts.write(prepared, f"p{i:08d}", json_table(b))
            if feature("stream_prepare_normalize_enabled"):
                early.submit(produce, artifacts.settings.stream_partition_bytes)
            else:
                produce()
        prepared.close()
        artifacts.event("prepare:complete")
        if not feature("stream_prepare_normalize_enabled"):
            for ref in references(prepared.manifest):
                early.submit(lambda r=ref: r, ref.metadata.byte_size)
        early.finish()
        if resume and not normalized.manifest.read().partitions:
            # With overlap and zero input batches, no pass-one callback runs.
            # Pin the empty source before close can enqueue its upload.
            resume.input(normalized, "p00000000", prepared.manifest.read().partitions[0].sha256)
        normalized.close()
        if resume:
            artifacts.barrier(prepared)
            resume.stage_inputs["normalized"] = (resume.completed["prepare"][1], (resume.completed["prepare"][0],))
            artifacts.barrier(normalized)
        # Global census is constructed only from the COMPLETE snapshot below.
        artifacts.event("normalized:complete")
        records = [row for ref in references(normalized.manifest) for row in read(ref, True)]
        scorer = ScoringEngine(MemorySnapshot(records), scoring_config, negative_pairs=negative_pairs)
        artifacts.event("normalize:global_statistics")
        cached_build = resume.lookup("candidates", "__stage__", resume.completed["normalized"][1]) if resume else None
        if cached_build:
            from masterone.mdm.pipeline.candidate_engine import CandidateResult
            manifest, _ = cached_build
            blocks = {}
            for part in manifest.artifact.partitions:
                content = resume._bytes(part.key, part.sha256, part.byte_size)
                import io

                import pyarrow.parquet as pq
                with pq.ParquetFile(io.BytesIO(content)) as reader:
                    for batch in reader.iter_batches():
                        for row in batch.to_pylist():
                            blocks[(row["left_id"], row["right_id"])] = set(row["reasons"])
            candidates = CandidateResult(blocks, manifest.metadata["metrics"], manifest.metadata["block_log"])
        else:
            engine = CandidateEngine(candidate_config)
            build = artifacts.exchange(lambda ref: engine.add_records(read(ref, True)), "normalize_build", workers=1)
            for ref in references(normalized.manifest):
                build.submit(lambda r=ref: r, ref.metadata.byte_size)
            build.finish()
            candidates = engine.finalize()
        artifacts.event("build:global_union")
        candidate_writer = artifacts.writer("candidates", CANDIDATE_SCHEMA,
            input_hash=resume.completed["normalized"][1] if resume else None,
            dependencies=(resume.completed["normalized"][0],) if resume else ())
        if resume:
            resume.metadata["candidates"] = {"metrics": candidates.metrics, "block_log": candidates.block_log}
        score_writer = artifacts.writer("scores", SCORE_SCHEMA)
        decision_writer = artifacts.writer("decision-prep")
        by_id = scorer.snapshot.records

        def prepare_decisions(ref):
            artifacts.event("decide:pair_local", ref.metadata.partition_id)
            if resume and resume.reuse_partition(artifacts, decision_writer, ref.metadata.partition_id, ref.metadata.sha256):
                return
            rows = [{"left_id": score.left_id, "right_id": score.right_id,
                     "result": scorer.decide(score, by_id)}
                    for score in (ScoreResult.from_row(row) for row in read(ref))]
            artifacts.write(decision_writer, ref.metadata.partition_id, json_table(rows))

        decide = artifacts.exchange(prepare_decisions, "score_decide")

        def score_partition(ref):
            artifacts.event("score:batch", ref.metadata.partition_id)
            cached = resume.reuse_partition(artifacts, score_writer, ref.metadata.partition_id, ref.metadata.sha256) if resume else None
            if cached:
                if feature("stream_score_decide_enabled"):
                    decide.submit(lambda: cached, cached.metadata.byte_size)
                return
            scored = scorer.score_batch(list(read(ref)))
            def produce():
                return artifacts.write(score_writer, ref.metadata.partition_id,
                    pa.Table.from_pylist([asdict(s) for s in scored], schema=SCORE_SCHEMA))
            if feature("stream_score_decide_enabled"):
                decide.submit(produce, artifacts.settings.stream_partition_bytes)
            else:
                produce()

        score = artifacts.exchange(score_partition, "build_score")
        canonical = [{"left_id": left, "right_id": right, "reasons": sorted(reasons)}
                     for (left, right), reasons in sorted(candidates.pairs.items())]
        for index, batch in enumerate(chunks(canonical, min(size, scorer.config.batch_size))):
            def produce(i=index, b=batch):
                if resume:
                    cached = resume.reuse_partition(artifacts, candidate_writer, f"p{i:08d}", resume.completed["normalized"][1])
                    if cached:
                        return cached
                return artifacts.write(candidate_writer, f"p{i:08d}", pa.Table.from_pylist(b, schema=CANDIDATE_SCHEMA))
            if feature("stream_build_score_enabled"):
                score.submit(produce, artifacts.settings.stream_partition_bytes)
            else:
                produce()
        candidate_writer.close()
        if resume:
            artifacts.barrier(candidate_writer)
            resume.stage_inputs["scores"] = (resume.completed["candidates"][1], (resume.completed["candidates"][0],))
        artifacts.event("candidates:complete")
        if not feature("stream_build_score_enabled"):
            for ref in references(candidate_writer.manifest):
                score.submit(lambda r=ref: r, ref.metadata.byte_size)
        score.finish()
        score_writer.close()
        # Preserve ScoringEngine's exact coverage/identity/census/source checks.
        scored_rows = (ScoreResult.from_row(row) for ref in references(score_writer.manifest) for row in read(ref))

        def provider(batch):
            return list(islice(scored_rows, len(batch)))

        score_path = Path(workspace) / "scored.parquet"
        metrics = scorer.write_scores(iter(canonical), score_path,
            expected_count=len(canonical), _score_provider=provider,
            _disk_guard=artifacts.uploader.disk_guard, _max_output_bytes=artifacts.settings.stream_disk_bytes)
        if next(scored_rows, None) is not None:
            raise StreamingError("STREAMING_SCORE_COVERAGE")
        artifacts.event("score:complete")
        if resume:
            resume.metadata["scores"] = {"metrics": metrics}
            artifacts.barrier(score_writer)
            resume.stage_inputs["decision-prep"] = (resume.completed["scores"][1], (resume.completed["scores"][0],))
        if not feature("stream_score_decide_enabled"):
            for ref in references(score_writer.manifest):
                decide.submit(lambda r=ref: r, ref.metadata.byte_size)
        decide.finish()
        decision_writer.close()
        artifacts.barrier(decision_writer) if resume else artifacts.barrier(prepared, normalized, candidate_writer, score_writer, decision_writer)
        artifacts.event("decide:global_barrier")
        # Read later in canonical partition order, after all score/upload barriers.
        prep_manifest = decision_writer.manifest
    decisions = (row for ref in references(prep_manifest) for row in read(ref, True))
    return records, candidates, scorer, metrics, score_path, decisions


def stage_partitions(publisher, identity, sources, workspace):
    """Stage private rows concurrently; seal/promotion remain global DB gates."""
    counts = {}
    with StreamingArtifacts(workspace, identity.run_id, identity.attempt_id, publisher.store) as artifacts:
        invocation = uuid4().hex[:8]
        for kind, source in sources.items():
            # Replaying stage() never mutates a completed native manifest.
            # Fresh private directories retain the SAME content-addressed remote
            # identity and DB receipts; all retained files still count in quota.
            writer = artifacts.writer("materialize-" + kind, directory="materialize-" + kind + "-" + invocation)

            def load(ref, k=kind):
                publisher.load_partition(identity, k, int(ref.metadata.partition_id[1:]), list(read(ref, True)))

            consumer = artifacts.exchange(load, "decide_materialize")
            counts[kind] = 0
            for index, batch in enumerate(chunks(source, min(publisher.batch_size, artifacts.settings.stream_partition_rows))):
                consumer.submit(lambda i=index, b=batch: artifacts.write(writer, f"p{i:08d}", json_table(b)),
                    artifacts.settings.stream_partition_bytes)
                counts[kind] += len(batch)
            writer.close()
            consumer.finish()
            artifacts.barrier(writer)
        artifacts.event("materialize:durable")
    return counts
