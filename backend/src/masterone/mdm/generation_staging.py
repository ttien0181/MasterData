"""Private, receipt-backed result loads and a fenced atomic publication gate.

No API reads staging. Partition checkpoints live in DB or immutable object keys,
never a local-ready path. Retried content must match exactly. Serving reconciliation
uses the existing conservative preservation contract, not destructive recompute.
"""
import json
from dataclasses import asdict, dataclass
from hashlib import sha256
from itertools import islice
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import aliased

from masterone.config import get_settings
from masterone.db.base import now_utc
from masterone.mdm.models import (
    ResolutionGeneration as Generation,
)
from masterone.mdm.models import (
    ResolutionGenerationAttempt as Attempt,
)
from masterone.mdm.models import (
    ResolutionNegativeEdge,
    ResolutionPair,
    ResolutionProposal,
    ResolutionPublication,
    ResolutionRecord,
    ResolutionReviewCase,
    ResolutionRun,
    ResolutionTask,
)
from masterone.mdm.models import (
    ResolutionPartitionReceipt as Receipt,
)
from masterone.mdm.models import (
    ResolutionStagedResult as Row,
)
from masterone.mdm.resolution_persistence import persist_snapshot
from masterone.storage.r2 import get_store

KINDS = ("pair", "proposal", "membership", "golden", "review")
_AUTO_STORE = object()


class GenerationError(RuntimeError):
    """Safe code only: never include source payloads, DSNs or object credentials."""


def failure_code(error):
    if isinstance(error, GenerationError):
        return str(error)
    if isinstance(error, SQLAlchemyError):
        return "GENERATION_DB_FAILED"
    if isinstance(error, OSError):
        return "GENERATION_ARTIFACT_IO_FAILED"
    return "GENERATION_FAILED"


def encode(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value):
    result = sha256()
    for chunk in _canonical_chunks(value):
        result.update(chunk)
    return result.hexdigest()


def _string_chunks(value):
    """Bound escaping and UTF-8 allocations even for one large string leaf."""
    yield b'"'
    for position in range(0, len(value), 4096):
        yield json.encoder.encode_basestring(value[position:position + 4096])[1:-1].encode('utf-8')
    yield b'"'


def digest_sequence(values):
    """Canonical array hash from a single-pass iterable without an array copy."""
    result = sha256(b'[')
    first = True
    for value in values:
        if not first:
            result.update(b',')
        first = False
        for chunk in _canonical_chunks(value):
            result.update(chunk)
    result.update(b']')
    return result.hexdigest()


def _fits_json_chunk(value):
    """Conservative upper bound before using the C JSON encoder on a subtree.

    Bound traversal, nesting and escaped bytes; oversized leaves and containers
    continue through the original streaming encoder. No large encoding is tried.
    """
    remaining, nodes = 32768, 1024
    pending = [(value, 0)]
    while pending:
        current, depth = pending.pop()
        nodes -= 1
        if nodes < 0 or depth > 48:
            return False
        if isinstance(current, str):
            remaining -= 6 * len(current) + 2
        elif isinstance(current, (dict, list, tuple)):
            if len(current) > nodes:
                return False
            remaining -= 4 * len(current) + 2
            if isinstance(current, dict):
                for key, item in current.items():
                    pending.append((key, depth + 1))
                    pending.append((item, depth + 1))
            else:
                pending.extend((item, depth + 1) for item in current)
        elif isinstance(current, int):
            remaining -= current.bit_length() // 3 + 8
        elif current is None or isinstance(current, float):
            remaining -= 32
        else:
            return False
        if remaining < 0:
            return False
    return True


def _canonical_chunks(value):
    """The encode() byte contract, emitted with bounded leaf/container traversal.

    Dictionary keys still sort in Python order, exactly like JSON sort_keys; array
    members and encoded strings are never assembled into a complete JSON value.
    """
    active = set()
    stack = [('value', value)]
    while stack:
        event = stack.pop()
        if event[0] == 'close':
            active.remove(event[2])
            yield event[1]
        elif event[0] == 'items':
            iterator, first, mapping = event[1:]
            try:
                item = next(iterator)
            except StopIteration:
                continue
            if not first:
                yield b','
            stack.append(('items', iterator, False, mapping))
            if mapping:
                key, current = item
                if not isinstance(key, str):
                    if key is not None and not isinstance(key, (int, float, bool)):
                        raise TypeError('JSON keys must be str, int, float, bool or None')
                    key = json.dumps(key, allow_nan=False)
                yield from _string_chunks(key)
                yield b':'
                stack.append(('value', current))
            else:
                stack.append(('value', item))
        else:
            current = event[1]
            if isinstance(current, (dict, list, tuple)):
                if _fits_json_chunk(current):
                    yield json.dumps(current, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':'), allow_nan=False).encode('utf-8')
                    continue
                identity = id(current)
                if identity in active:
                    raise ValueError('Circular reference detected')
                active.add(identity)
                mapping = isinstance(current, dict)
                yield b'{' if mapping else b'['
                stack.append(('close', b'}' if mapping else b']', identity))
                iterator = iter(sorted(current.items())) if mapping else iter(current)
                stack.append(('items', iterator, True, mapping))
            elif isinstance(current, str):
                yield from _string_chunks(current)
            else:
                yield json.dumps(current, sort_keys=True, ensure_ascii=False,
                                 separators=(',', ':'), allow_nan=False).encode('utf-8')


def batches(values, size):
    iterator = iter(values)
    while batch := list(islice(iterator, size)):
        yield batch


def lock_run(db, run_id):
    # SQLite ignores FOR UPDATE. Acquire its writer reservation before reading
    # the fence/base pointer; PostgreSQL uses the row lock shared with reviewers.
    if db.get_bind().dialect.name == "sqlite":
        connection = db.connection()
        if not connection.connection.driver_connection.in_transaction:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
    return db.query(ResolutionRun).filter_by(id=run_id).with_for_update().one()


def fingerprints(db, run_id):
    """Stream immutable inputs and human state, independently from serving additions."""
    snapshot, review = sha256(), sha256()
    count = 0
    query = select(ResolutionRecord).where(ResolutionRecord.run_id == run_id).order_by(ResolutionRecord.id)
    for row in db.scalars(query.execution_options(yield_per=512)):
        snapshot.update(encode([row.id, row.external_id, row.source_system, row.entity_type,
            row.raw_fields, row.normalized_fields, row.quality_flags, row.extra_fields,
            row.original_fields, row.mapping_provenance, row.lineage]) + b"\n")
        count += 1
    for model in (ResolutionPair, ResolutionProposal, ResolutionReviewCase, ResolutionNegativeEdge):
        query = select(model).where(model.run_id == run_id).order_by(model.id)
        for row in db.scalars(query.execution_options(yield_per=512)):
            if model is ResolutionPair:
                value = ["pair", row.id, row.user_decision, row.revision] if row.user_decision not in {None, "UNDECIDED"} else None
            elif model is ResolutionProposal:
                human = row.origin == "REVIEW" or row.status != "AUTO_READY" or "_golden_override" in row.golden_fields
                value = ["proposal", row.id, row.status, row.revision, row.member_record_ids, row.golden_fields] if human else None
            elif model is ResolutionReviewCase:
                value = ["review", row.id, row.status, row.revision, row.first_decision, row.second_decision] if row.status != "OPEN" or row.revision > 1 else None
            else:
                value = ["negative", row.id, row.left_record_id, row.right_record_id, row.active, row.reason]
            if value is not None:
                review.update(encode(value) + b"\n")
    return snapshot.hexdigest(), review.hexdigest(), count


def config_identity(policy_version, scoring_config=None, candidate_config=None):
    from masterone.mdm.engine_adapter import STREAMING_FEATURES, feature
    from masterone.mdm.pipeline.candidate_engine import CandidateConfig
    from masterone.mdm.pipeline.scoring_engine import ScoringConfig

    settings = get_settings()
    root = Path(__file__).parent
    # Pin implementation + nonsecret policy/config, not deployment credentials.
    files = sorted([*root.joinpath("pipeline").glob("*.py"), *root.joinpath("config").glob("*.yaml"),
                    root / "engine_adapter.py", root / "resolution_persistence.py"])
    return {"contract": "p119_generation_v1", "policy": policy_version,
        "scoring": asdict(scoring_config or ScoringConfig()), "candidate": asdict(candidate_config or CandidateConfig()),
        "decision_v2": feature("decision_semantics_v2_enabled"), "cluster_v2": feature("cluster_safety_v2_enabled"),
        "semantic_address": settings.semantic_address_enabled,
        "streaming": {name: feature(name) for name in STREAMING_FEATURES},
        "resume": feature("pipeline_resume_enabled"),
        "streaming_limits": {name: getattr(settings, name) for name in (
            "stream_partition_rows", "stream_partition_bytes", "stream_queue_partitions", "stream_queue_bytes",
            "stream_consumer_workers", "stream_upload_workers", "stream_disk_bytes", "stream_timeout_seconds")},
        "semantic_budget": settings.semantic_address_max_pairs_per_run,
        "semantic_model": settings.gemini_model, "batch_size": settings.generation_batch_size,
        "publication_code": sha256(Path(__file__).read_bytes()).hexdigest(),
        "code": {str(p.relative_to(root)).replace("\\", "/"): sha256(p.read_bytes()).hexdigest() for p in files}}


@dataclass(frozen=True)
class GenerationIdentity:
    run_id: str
    generation_id: str
    attempt_id: str


class GenerationPublisher:
    def __init__(self, factory, *, store=_AUTO_STORE, batch_size=None, failpoint=None):
        self.factory = factory
        self.store = get_store(get_settings()) if store is _AUTO_STORE else store
        self.batch_size = batch_size or get_settings().generation_batch_size
        if not 1 <= self.batch_size <= 2048:
            raise ValueError("Invalid generation batch size")
        self.failpoint = failpoint or (lambda name: None)

    def begin(self, run_id, *, config, attempt_id=None):
        attempt_id = attempt_id or str(uuid4())
        with self.factory.begin() as db:
            from masterone.mdm.job_queue import guard
            guard(db, run_id, attempt_id=attempt_id)
            run = lock_run(db, run_id)
            publication = db.get(ResolutionPublication, run_id)
            if publication is None:
                publication = ResolutionPublication(run_id=run_id)
                db.add(publication)
                db.flush()
            snapshot, review, count = fingerprints(db, run_id)
            if count != run.record_count:
                raise GenerationError("GENERATION_INPUT_COUNT")
            backend = "OBJECT" if self.store is not None else "DB"
            config_hash = digest({"config": config, "batch_size": self.batch_size,
                "backend": backend, "bucket": getattr(self.store, "bucket", None)})
            identity_hash = digest([run_id, snapshot, review, config_hash])
            generation_id = str(uuid5(NAMESPACE_URL, "p119:generation:" + identity_hash))
            generation = db.get(Generation, generation_id)
            if generation is None:
                generation = Generation(id=generation_id, run_id=run_id, identity_sha256=identity_hash,
                    snapshot_sha256=snapshot, review_sha256=review, config_sha256=config_hash,
                    record_count=count, base_generation_id=publication.active_generation_id, artifact_backend=backend)
                db.add(generation)
                db.flush()
            elif generation.identity_sha256 != identity_hash or generation.run_id != run_id:
                raise GenerationError("GENERATION_IDENTITY_CONFLICT")
            attempt = db.get(Attempt, attempt_id)
            if attempt is not None:
                if attempt.generation_id != generation_id or publication.pipeline_attempt_id != attempt_id:
                    raise GenerationError("GENERATION_ATTEMPT_CONFLICT")
            else:
                previous = db.get(Attempt, publication.pipeline_attempt_id) if publication.pipeline_attempt_id else None
                if previous is not None and previous.status == "RUNNING":
                    previous.status = "SUPERSEDED"
                    previous.finished_at = now_utc()
                attempt = Attempt(id=attempt_id, generation_id=generation_id)
                db.add(attempt)
                db.flush()
                publication.pipeline_attempt_id = attempt_id
            if generation.state == "PROMOTED":
                if publication.active_generation_id != generation_id:
                    raise GenerationError("GENERATION_STALE_REPLAY")
            else:
                attempt.status = "RUNNING"
                attempt.error_code = None
                attempt.finished_at = None
            guard(db, run_id)
        return GenerationIdentity(run_id, generation_id, attempt_id)

    def _lock(self, db, identity):
        from masterone.mdm.job_queue import guard
        guard(db, identity.run_id, attempt_id=identity.attempt_id)
        run = lock_run(db, identity.run_id)
        generation = db.get(Generation, identity.generation_id)
        attempt = db.get(Attempt, identity.attempt_id)
        publication = db.get(ResolutionPublication, identity.run_id)
        if (generation is None or generation.run_id != run.id or attempt is None
                or attempt.generation_id != generation.id or publication is None or publication.pipeline_attempt_id != attempt.id):
            raise GenerationError("GENERATION_ATTEMPT_FENCED")
        if attempt.status not in {"RUNNING", "SUCCEEDED"}:
            raise GenerationError("GENERATION_ATTEMPT_INACTIVE")
        return run, generation, attempt

    def start_failed(self, run_id, attempt_id, previous_attempt_id):
        """Reconcile begin acknowledgment loss without failing a newer owner."""
        with self.factory() as db:
            attempt = db.get(Attempt, attempt_id)
            identity = GenerationIdentity(run_id, attempt.generation_id, attempt_id) if attempt else None
        if identity is not None:
            self.fail(identity, "GENERATION_START_FAILED")
            return
        with self.factory.begin() as db:
            from masterone.mdm.job_queue import guard
            guard(db, run_id)
            run = lock_run(db, run_id)
            publication = db.get(ResolutionPublication, run_id)
            current = publication.pipeline_attempt_id if publication else None
            task = db.query(ResolutionTask).filter_by(run_id=run_id).one_or_none()
            if current == previous_attempt_id and task is not None and task.status == "QUEUED":
                task.status = run.status = "RETRYABLE_FAILED"
                task.phase = run.phase = "failed"
                task.error_code = "GENERATION_START_FAILED"
                task.updated_at = now_utc()

    def _artifact(self, identity, kind, partition_no, content):
        checksum = sha256(content).hexdigest()
        key = None
        if self.store is not None:
            key = f"generations/{identity.run_id}/{identity.generation_id}/{kind}/{partition_no:08d}-{checksum}.json"
            exists = getattr(self.store, "exists", None)
            if exists is None or not exists(key):
                self.store.upload_bytes(content, key, "application/json")
            if self.store.download_bytes(key) != content:
                raise GenerationError("GENERATION_ARTIFACT_CHECKSUM")
        return checksum, key

    def resume_ready(self, identity):
        """A sealed generation can promote directly after full validation.

        A bad receipt is an error, not permission to mutate committed private
        rows. Existing serving data and reviewer decisions stay untouched.
        """
        with self.factory.begin() as db:
            _, generation, _ = self._lock(db, identity)
            if generation.state != "SEALED":
                return False
            self.validate(db, identity, generation)
            return True

    @staticmethod
    def _row(kind, payload, generation_id, partition_no):
        payload = json.loads(encode(payload))
        values = {"generation_id": generation_id, "kind": kind, "partition_no": partition_no,
                  "payload": payload, "sha256": digest(payload)}
        if kind == "pair":
            if payload.get("user_decision") not in {None, "UNDECIDED"}:
                raise GenerationError("GENERATION_HUMAN_STATE_IN_PAYLOAD")
            values.update(row_key=payload["id"], left_id=payload["left_id"], right_id=payload["right_id"])
            if values["left_id"] >= values["right_id"]:
                raise GenerationError("GENERATION_PAIR_ORDER")
        elif kind == "membership":
            values.update(row_key=payload["record_id"], record_id=payload["record_id"], proposal_id=payload["proposal_id"])
        elif kind == "golden":
            values.update(row_key=payload["proposal_id"], proposal_id=payload["proposal_id"])
        else:
            values.update(row_key=payload["id"])
            if kind == "proposal" and (payload.get("status") != "AUTO_READY" or payload.get("revision") != 1):
                raise GenerationError("GENERATION_HUMAN_STATE_IN_PAYLOAD")
        if not isinstance(values["row_key"], str) or not 0 < len(values["row_key"]) <= 64:
            raise GenerationError("GENERATION_ROW_KEY")
        return values

    def load_partition(self, identity, kind, partition_no, payloads):
        if kind not in KINDS or partition_no < 0 or not 0 < len(payloads) <= self.batch_size:
            raise GenerationError("GENERATION_PARTITION_SHAPE")
        rows = [self._row(kind, p, identity.generation_id, partition_no) for p in payloads]
        if len({r["row_key"] for r in rows}) != len(rows):
            raise GenerationError("GENERATION_DUPLICATE_ROW")
        content = encode(payloads)
        checksum, key = self._artifact(identity, kind, partition_no, content)
        try:
            self._commit_partition(identity, kind, partition_no, rows, content, checksum, key)
        except IntegrityError:
            # SQLAlchemy's exception string includes JSON parameters/PII.
            raise GenerationError("GENERATION_UNIQUE_OR_REFERENCE") from None
        self.failpoint("after_partition_commit")

    def _commit_partition(self, identity, kind, partition_no, rows, content, checksum, key):
        with self.factory.begin() as db:
            _, generation, _ = self._lock(db, identity)
            old = db.get(Receipt, (generation.id, kind, partition_no))
            if old is not None:
                if old.sha256 != checksum or old.row_count != len(rows):
                    raise GenerationError("GENERATION_PARTITION_CONFLICT")
                return
            if generation.state != "STAGING":
                raise GenerationError("GENERATION_ALREADY_SEALED")
            # A row may occur exactly once in a generation, not across partitions.
            db.execute(Row.__table__.insert(), rows)
            db.add(Receipt(generation_id=generation.id, kind=kind, partition_no=partition_no,
                attempt_id=identity.attempt_id, row_count=len(rows), byte_count=len(content), sha256=checksum,
                object_key=key, artifact_bytes=content if key is None else None))
            self.failpoint("before_partition_commit")
            self._lock(db, identity)

    def stage(self, identity, computed):
        def proposals():
            for p in computed["proposals"]:
                yield {k: v for k, v in p.items() if k != "golden_fields"}

        from masterone.mdm.pipeline.semantic_address import with_annotations
        pairs = with_annotations(computed["pairs"], computed.get("semantic_annotations") or {})
        sources = {"pair": pairs, "proposal": proposals(),
            "membership": ({"record_id": rid, "proposal_id": p["id"]} for p in computed["proposals"] for rid in p["member_record_ids"]),
            "golden": ({"proposal_id": p["id"], "golden_fields": p["golden_fields"]} for p in computed["proposals"]),
            "review": computed["review_cases"]}
        from masterone.mdm.engine_adapter import feature
        if feature("stream_decide_materialize_enabled") and self.store is not None:
            from tempfile import mkdtemp

            from masterone.mdm.pipeline.streaming import stage_partitions
            counts = stage_partitions(self, identity, sources,
                Path(computed.get("_streaming_workspace") or mkdtemp(prefix="p119-materialize-")))
        else:
            counts = {}
            for kind in KINDS:
                counts[kind] = 0
                for index, batch in enumerate(batches(sources[kind], self.batch_size)):
                    self.load_partition(identity, kind, index, batch)
                    counts[kind] += len(batch)
        metrics = {"candidate_count": computed["candidate_count"], "insufficient_count": computed["insufficient_count"]}
        self.seal(identity, counts, metrics)

    def seal(self, identity, counts, metrics):
        if set(counts) != set(KINDS) or any(type(n) is not int or n < 0 for n in counts.values()):
            raise GenerationError("GENERATION_EXPECTED_COUNTS")
        if (set(metrics) != {"candidate_count", "insufficient_count"}
                or any(type(n) is not int or n < 0 for n in metrics.values())
                or metrics["candidate_count"] != counts["pair"] + metrics["insufficient_count"]):
            raise GenerationError("GENERATION_CANDIDATE_COVERAGE")
        with self.factory.begin() as db:
            _, generation, _ = self._lock(db, identity)
            receipts = db.execute(select(Receipt.kind, Receipt.partition_no, Receipt.row_count, Receipt.sha256)
                .where(Receipt.generation_id == generation.id).order_by(Receipt.kind, Receipt.partition_no)).all()
            manifest = {"contract": "p119_generation_v1", "generation_id": generation.id,
                "input": generation.snapshot_sha256, "config": generation.config_sha256,
                "counts": counts, "metrics": metrics,
                "partitions": [[r.kind, r.partition_no, r.row_count, r.sha256] for r in receipts if r.kind != "manifest"]}
            content = encode(manifest)
            checksum, key = self._artifact(identity, "manifest", 0, content)
            if generation.manifest_sha256 is not None:
                if generation.manifest_sha256 != checksum:
                    raise GenerationError("GENERATION_MANIFEST_CONFLICT")
                return
            generation.expected_counts = counts
            generation.metrics = metrics
            generation.manifest_sha256 = checksum
            generation.state = "SEALED"
            db.add(Receipt(generation_id=generation.id, kind="manifest", partition_no=0,
                attempt_id=identity.attempt_id, row_count=0, byte_count=len(content), sha256=checksum,
                object_key=key, artifact_bytes=content if key is None else None))
            self._lock(db, identity)
        self.failpoint("after_seal")

    def _rows(self, db, identity, kind):
        # Keyset batches permit bounded reads while serving writes use this session.
        last = ""
        while True:
            batch = db.scalars(select(Row).where(Row.generation_id == identity.generation_id,
                Row.kind == kind, Row.row_key > last).order_by(Row.row_key).limit(self.batch_size)).all()
            if not batch:
                return
            yield from batch
            last = batch[-1].row_key

    def _read_artifact(self, generation, receipt):
        if generation.artifact_backend == "DB":
            content = receipt.artifact_bytes
            if receipt.object_key is not None:
                raise GenerationError("GENERATION_ARTIFACT_BACKEND")
        else:
            if self.store is None or receipt.object_key is None or receipt.artifact_bytes is not None:
                raise GenerationError("GENERATION_DURABILITY_MISSING")
            content = self.store.download_bytes(receipt.object_key)
        if content is None or len(content) != receipt.byte_count or sha256(content).hexdigest() != receipt.sha256:
            raise GenerationError("GENERATION_ARTIFACT_CHECKSUM")
        return content

    def validate(self, db, identity, generation):
        if generation.state != "SEALED" or generation.expected_counts is None:
            raise GenerationError("GENERATION_INCOMPLETE")
        actual = dict(db.execute(select(Row.kind, func.count()).where(Row.generation_id == generation.id).group_by(Row.kind)).all())
        if any(actual.get(k, 0) != generation.expected_counts[k] for k in KINDS) or set(actual) - set(KINDS):
            raise GenerationError("GENERATION_ROW_COUNT")
        manifest = None
        actual_parts = []
        next_part = {k: 0 for k in KINDS}
        for receipt in db.scalars(select(Receipt).where(Receipt.generation_id == generation.id)
                                  .order_by(Receipt.kind, Receipt.partition_no).execution_options(yield_per=1)):
            attempt = db.get(Attempt, receipt.attempt_id)
            if attempt is None or attempt.generation_id != generation.id:
                raise GenerationError("GENERATION_RECEIPT_IDENTITY")
            content = self._read_artifact(generation, receipt)
            if receipt.kind == "manifest":
                if receipt.partition_no != 0 or receipt.sha256 != generation.manifest_sha256:
                    raise GenerationError("GENERATION_MANIFEST_CONFLICT")
                manifest = json.loads(content)
                continue
            if receipt.kind not in KINDS or receipt.partition_no != next_part[receipt.kind]:
                raise GenerationError("GENERATION_PARTITION_GAP")
            next_part[receipt.kind] += 1
            if not 0 < receipt.row_count <= self.batch_size:
                raise GenerationError("GENERATION_RECEIPT_COUNT")
            scope = (Row.generation_id == generation.id, Row.kind == receipt.kind, Row.partition_no == receipt.partition_no)
            if db.scalar(select(func.count()).select_from(Row).where(*scope)) != receipt.row_count:
                raise GenerationError("GENERATION_RECEIPT_COUNT")
            partition = db.scalars(select(Row).where(*scope).limit(self.batch_size)).all()
            source = json.loads(content)
            if len(partition) != receipt.row_count or len(source) != receipt.row_count:
                raise GenerationError("GENERATION_RECEIPT_COUNT")
            expected = {digest(p) for p in source}
            if expected != {r.sha256 for r in partition} or len(expected) != len(source):
                raise GenerationError("GENERATION_RECEIPT_CHECKSUM")
            actual_parts.append([receipt.kind, receipt.partition_no, receipt.row_count, receipt.sha256])
        expected_manifest = {"contract": "p119_generation_v1", "generation_id": generation.id,
            "input": generation.snapshot_sha256, "config": generation.config_sha256,
            "counts": generation.expected_counts, "metrics": generation.metrics, "partitions": actual_parts}
        if manifest != expected_manifest:
            raise GenerationError("GENERATION_MANIFEST_CONFLICT")
        # Every staged row is covered by a committed receipt; recompute its digest,
        # typed keys and references, rather than trusting JSON or foreign keys alone.
        for kind in KINDS:
            for row in self._rows(db, identity, kind):
                expected = self._row(kind, row.payload, generation.id, row.partition_no)
                for name in ("sha256", "row_key", "left_id", "right_id", "record_id", "proposal_id"):
                    if getattr(row, name) != expected.get(name):
                        raise GenerationError("GENERATION_CORRUPT_ROW")
                if db.get(Receipt, (generation.id, kind, row.partition_no)) is None:
                    raise GenerationError("GENERATION_DURABILITY_MISSING")
                self._validate_references(db, identity, kind, row.payload)
        if generation.expected_counts["golden"] != generation.expected_counts["proposal"]:
            raise GenerationError("GENERATION_GOLDEN_COVERAGE")
        member = aliased(Row)
        proposal = aliased(Row)
        dangling = select(member.row_key).where(member.generation_id == generation.id, member.kind == "membership",
            ~select(proposal.row_key).where(proposal.generation_id == generation.id, proposal.kind == "proposal",
                proposal.row_key == member.proposal_id).exists()).limit(1)
        if db.scalar(dangling) is not None:
            raise GenerationError("GENERATION_MEMBERSHIP_REFERENCE")

    def _validate_references(self, db, identity, kind, payload):
        def record(rid):
            row = db.get(ResolutionRecord, rid)
            if row is None or row.run_id != identity.run_id:
                raise GenerationError("GENERATION_RECORD_REFERENCE")

        def staged(which, key):
            row = db.get(Row, (identity.generation_id, which, key))
            if row is None:
                raise GenerationError("GENERATION_RESULT_REFERENCE")
            return row.payload

        if kind == "pair":
            record(payload["left_id"])
            record(payload["right_id"])
            required = ("blocking_reasons", "field_evidence", "score", "decision", "reason", "triage_status",
                        "triage_reason", "primary_group", "subgroup", "signature", "normalization_tags", "policy_id", "policy_version")
            if any(k not in payload for k in required):
                raise GenerationError("GENERATION_PAIR_SCHEMA")
        elif kind == "membership":
            record(payload["record_id"])
        elif kind == "proposal":
            ids = payload["member_record_ids"]
            if len(ids) < 2 or len(set(ids)) != len(ids):
                raise GenerationError("GENERATION_MEMBERSHIP_CONSISTENCY")
            members = set(db.scalars(select(Row.record_id).where(Row.generation_id == identity.generation_id,
                Row.kind == "membership", Row.proposal_id == payload["id"])))
            if members != set(ids):
                raise GenerationError("GENERATION_MEMBERSHIP_CONSISTENCY")
            staged("golden", payload["id"])
            for edge in payload.get("edge_order", []):
                pair = staged("pair", edge["pair_id"])
                if not {pair["left_id"], pair["right_id"]} <= members:
                    raise GenerationError("GENERATION_PROPOSAL_EDGE_REFERENCE")
        elif kind == "golden":
            proposal = staged("proposal", payload["proposal_id"])
            golden = payload["golden_fields"]
            if set(golden.get("member_record_ids", [])) != set(proposal["member_record_ids"]) or not isinstance(golden.get("fields"), dict):
                raise GenerationError("GENERATION_GOLDEN_COVERAGE")
            for field in golden["fields"].values():
                for observation in [field, *field.get("alternatives", []), *field.get("values", [])]:
                    observations = [observation, *observation.get("sources", [])]
                    if any(o.get("source_record_id") is not None and o["source_record_id"] not in proposal["member_record_ids"] for o in observations):
                        raise GenerationError("GENERATION_GOLDEN_PROVENANCE")
        elif kind == "review":
            if any(k not in payload for k in ("primary_group", "subgroup", "signature_hash", "triage_reason")):
                raise GenerationError("GENERATION_REVIEW_SCHEMA")
            pair_ids = payload["pair_ids"]
            if not pair_ids or len(set(pair_ids)) != len(pair_ids):
                raise GenerationError("GENERATION_REVIEW_REFERENCE")
            ids = {rid for pid in pair_ids for p in [staged("pair", pid)] for rid in (p["left_id"], p["right_id"])}
            if ids != set(payload["member_record_ids"]) or len(ids) != len(payload["member_record_ids"]):
                raise GenerationError("GENERATION_REVIEW_REFERENCE")

    def promote(self, identity):
        with self.factory.begin() as db:
            run, generation, attempt = self._lock(db, identity)
            publication = db.get(ResolutionPublication, run.id)
            if generation.state == "PROMOTED":
                if publication.active_generation_id != generation.id:
                    raise GenerationError("GENERATION_STALE_REPLAY")
            else:
                if publication.active_generation_id != generation.base_generation_id:
                    raise GenerationError("GENERATION_STALE_BASE")
                if fingerprints(db, run.id) != (generation.snapshot_sha256, generation.review_sha256, generation.record_count):
                    raise GenerationError("GENERATION_INPUT_CHANGED")
                self.validate(db, identity, generation)
                self.failpoint("before_promote")

                def proposals():
                    for row in self._rows(db, identity, "proposal"):
                        golden = db.get(Row, (generation.id, "golden", row.row_key)).payload["golden_fields"]
                        yield {**row.payload, "golden_fields": golden}

                persist_snapshot(db, run.id, {"pairs": (r.payload for r in self._rows(db, identity, "pair")),
                    "proposals": proposals(), "review_cases": (r.payload for r in self._rows(db, identity, "review"))},
                    generation_id=generation.id)
                db.flush()
                self.failpoint("during_promote")
                generation.state = "PROMOTED"
                generation.promoted_at = now_utc()
                publication.active_generation_id = generation.id
            from masterone.mdm.job_queue import completed
            completed(db, identity.run_id)
            attempt.status = "SUCCEEDED"
            attempt.finished_at = now_utc()
            run.status = "COMPLETED"
            run.phase = "completed"
            run.completed_at = generation.promoted_at
            run.candidate_count = generation.metrics["candidate_count"]
            run.insufficient_count = generation.metrics["insufficient_count"]
            task = db.query(ResolutionTask).filter_by(run_id=run.id).one_or_none()
            if task is not None:
                task.status = "COMPLETED"
                task.phase = "completed"
                task.processed_count = task.total_count = generation.record_count
                task.error_code = None
                task.updated_at = now_utc()
        self.failpoint("after_promote_commit")

    def fail(self, identity, code="GENERATION_FAILED"):
        with self.factory.begin() as db:
            from masterone.mdm.job_queue import guard
            guard(db, identity.run_id, attempt_id=identity.attempt_id)
            run = lock_run(db, identity.run_id)
            attempt = db.get(Attempt, identity.attempt_id)
            generation = db.get(Generation, identity.generation_id)
            publication = db.get(ResolutionPublication, run.id)
            # Lost acknowledgment after a successful commit is still success.
            if generation.state == "PROMOTED" or attempt.status == "SUCCEEDED":
                return
            if attempt.status == "RUNNING":
                attempt.status = "FAILED"
                attempt.error_code = code
                attempt.finished_at = now_utc()
            if publication.pipeline_attempt_id != attempt.id:
                return
            run.status = "RETRYABLE_FAILED"
            run.phase = "failed"
            task = db.query(ResolutionTask).filter_by(run_id=run.id).one_or_none()
            if task is not None:
                task.status = "RETRYABLE_FAILED"
                task.phase = "failed"
                task.error_code = code
                task.updated_at = now_utc()
