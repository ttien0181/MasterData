from copy import deepcopy

import pytest
from alembic import command
from masterone.auth.models import User
from masterone.config import get_settings
from masterone.db.base import Base
from masterone.mdm.generation_staging import (
    GenerationError,
    GenerationPublisher,
)
from masterone.mdm.migrate import HEAD, alembic_config
from masterone.mdm.models import (
    ResolutionAuditEvent,
    ResolutionFile,
    ResolutionPair,
    ResolutionProposal,
    ResolutionPublication,
    ResolutionRecord,
    ResolutionReviewCase,
    ResolutionRun,
    ResolutionTask,
)
from masterone.mdm.models import (
    ResolutionGeneration as Generation,
)
from masterone.mdm.models import (
    ResolutionGenerationAttempt as Attempt,
)
from masterone.mdm.models import (
    ResolutionPartitionReceipt as Receipt,
)
from masterone.mdm.models import (
    ResolutionStagedResult as Row,
)
from masterone.mdm.pipeline.golden import build_golden
from masterone.mdm.pipeline.stage_runner import StageRunner
from masterone.mdm.resolution_persistence import persist_snapshot
from masterone.mdm.resolution_service import _compute, mark_failed, run_pipeline
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from .test_decision_semantics import record


@pytest.fixture
def staging(monkeypatch):
    monkeypatch.setattr(get_settings(), "generation_staging_enabled", True)
    monkeypatch.setattr(get_settings(), "generation_batch_size", 2)


@pytest.fixture
def snapshot(tmp_path):
    engine = create_engine("sqlite:///" + (tmp_path / "generation.db").as_posix())

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    records = [record(r, email="common@example.test", dob="1990-01-01", address="Hanoi") for r in "abc"]
    with factory.begin() as db:
        db.add(User(id="owner", email="owner@example.test", email_normalized="owner@example.test", password_hash="unused", role="STEWARD"))
        db.flush()
        db.add(ResolutionRun(id="run", owner_user_id="owner", policy_version="v", record_count=3))
        db.flush()
        db.add(ResolutionFile(id="file", run_id="run", filename="test.csv", sha256="test", row_count=3))
        db.flush()
        db.add_all([ResolutionRecord(id=r["id"], run_id="run", external_id=r["external_id"], source_system=r["source_system"],
            file_id="file", entity_type="PERSON", raw_fields=r["raw_fields"], normalized_fields=r["normalized_fields"], quality_flags=r["quality_flags"], extra_fields={}) for r in records])
        db.add(ResolutionTask(id="task", run_id="run"))
    computed = _compute(records, "run", "v", scoring_output_dir=tmp_path / "scores")
    # Exercise each result kind independently from the decision evaluator tests.
    proposal = computed["proposals"][0]
    proposal["member_record_ids"] = ["a", "b"]
    proposal["edge_order"] = [e for e in proposal["edge_order"] if {e["left_id"], e["right_id"]} <= {"a", "b"}]
    proposal["golden_fields"] = build_golden(["a", "b"], {r["id"]: r for r in records})
    pair = next(p for p in computed["pairs"] if {p["left_id"], p["right_id"]} == {"b", "c"})
    computed["review_cases"] = [{"id": "review-bc", "pair_ids": [pair["id"]], "member_record_ids": ["b", "c"],
        "primary_group": "C", "subgroup": "C1", "signature_hash": "case-signature", "triage_reason": "REVIEW"}]
    yield factory, computed, records
    engine.dispose()


def public(factory):
    with factory() as db:
        publication = db.get(ResolutionPublication, "run")
        return (publication.active_generation_id if publication else None, db.query(ResolutionPair).count(),
                db.query(ResolutionProposal).count(), db.query(ResolutionReviewCase).count())


def fail_at(boundary):
    def failpoint(name):
        if name == boundary:
            raise RuntimeError("injected crash")
    return failpoint


@pytest.mark.parametrize("boundary", ["before_partition_commit", "after_partition_commit", "after_seal", "before_promote", "during_promote"])
def test_crash_never_exposes_partial_serving(snapshot, boundary):
    factory, computed, _ = snapshot
    publisher = GenerationPublisher(factory, batch_size=2, failpoint=fail_at(boundary))
    identity = publisher.begin("run", config={"v": 1})
    with pytest.raises(RuntimeError, match="injected crash"):
        publisher.stage(identity, computed)
        publisher.promote(identity)
    publisher.fail(identity)
    assert public(factory) == (None, 0, 0, 0)
    with factory() as db:
        assert db.get(Attempt, identity.attempt_id).status == "FAILED"
        assert db.get(Generation, identity.generation_id).state != "PROMOTED"


def test_duplicate_retry_and_partition_ack_loss(snapshot):
    factory, computed, _ = snapshot
    publisher = GenerationPublisher(factory, batch_size=2, failpoint=fail_at("after_partition_commit"))
    first = publisher.begin("run", config={"v": 1})
    with pytest.raises(RuntimeError):
        publisher.stage(first, computed)
    publisher.fail(first)
    publisher.failpoint = lambda name: None
    retry = publisher.begin("run", config={"v": 1})
    assert first.generation_id == retry.generation_id and first.attempt_id != retry.attempt_id
    publisher.stage(retry, computed)
    publisher.stage(retry, computed)
    publisher.promote(retry)
    publisher.promote(retry)
    assert public(factory) == (retry.generation_id, 3, 1, 1)
    with factory() as db:
        assert db.query(Row).count() == 8
        assert db.query(Receipt).count() == 7
        assert db.get(Attempt, retry.attempt_id).status == "SUCCEEDED"


def test_db_promote_ack_loss_is_success_and_replay(snapshot):
    factory, computed, _ = snapshot
    publisher = GenerationPublisher(factory, batch_size=2, failpoint=fail_at("after_promote_commit"))
    identity = publisher.begin("run", config={"v": 1})
    publisher.stage(identity, computed)
    with pytest.raises(RuntimeError, match="injected crash"):
        publisher.promote(identity)
    publisher.fail(identity)
    mark_failed("run", factory)
    assert public(factory) == (identity.generation_id, 3, 1, 1)
    with factory() as db:
        assert db.get(ResolutionTask, "task").status == "COMPLETED"
        assert db.get(Attempt, identity.attempt_id).status == "SUCCEEDED"
    publisher.failpoint = lambda name: None
    retry = publisher.begin("run", config={"v": 1})
    publisher.promote(retry)
    assert public(factory) == (identity.generation_id, 3, 1, 1)


def test_other_reader_sees_complete_old_or_complete_new_generation(snapshot):
    factory, computed, _ = snapshot
    observed = []

    def read_during_writes(name):
        if name in {"after_partition_commit", "during_promote"}:
            observed.append((name, public(factory)))

    publisher = GenerationPublisher(factory, batch_size=2, failpoint=read_during_writes)
    identity = publisher.begin("run", config={"v": 1})
    publisher.stage(identity, computed)
    publisher.promote(identity)
    assert observed and any(name == "during_promote" for name, _ in observed)
    assert all(state == (None, 0, 0, 0) for _, state in observed)
    assert public(factory) == (identity.generation_id, 3, 1, 1)


class MemoryObjects:
    bucket = "test-only"

    def __init__(self):
        self.objects = {}
        self.fail_upload = False

    def upload_bytes(self, content, key, content_type):
        if self.fail_upload:
            raise OSError("injected R2 failure")
        self.objects[key] = content

    def download_bytes(self, key):
        return self.objects[key]


def test_r2_failure_keeps_active_generation_and_no_local_fallback(snapshot):
    factory, computed, _ = snapshot
    store = MemoryObjects()
    publisher = GenerationPublisher(factory, store=store, batch_size=2)
    first = publisher.begin("run", config={"v": 1})
    publisher.stage(first, computed)
    publisher.promote(first)
    before = public(factory)
    second = publisher.begin("run", config={"v": 2})
    store.fail_upload = True
    with pytest.raises(OSError, match="R2 failure"):
        publisher.stage(second, computed)
    publisher.fail(second)
    assert public(factory) == before
    with factory() as db:
        assert db.query(Receipt).filter_by(generation_id=second.generation_id).count() == 0
        assert all(r.artifact_bytes is None for r in db.query(Receipt))


def test_missing_durable_artifact_blocks_promote(snapshot):
    factory, computed, _ = snapshot
    store = MemoryObjects()
    publisher = GenerationPublisher(factory, store=store, batch_size=2)
    identity = publisher.begin("run", config={"v": 1})
    publisher.stage(identity, computed)
    store.objects.pop(next(iter(store.objects)))
    with pytest.raises(KeyError):
        publisher.promote(identity)
    assert public(factory) == (None, 0, 0, 0)


@pytest.mark.parametrize("damage", ["payload", "receipt_count", "artifact", "missing_row", "missing_receipt", "manifest"])
def test_validation_rejects_corruption(snapshot, damage):
    factory, computed, _ = snapshot
    publisher = GenerationPublisher(factory, batch_size=2)
    identity = publisher.begin("run", config={"v": 1})
    publisher.stage(identity, computed)
    with factory.begin() as db:
        row = db.query(Row).filter_by(kind="pair").first()
        receipt = db.query(Receipt).filter_by(kind="pair").first()
        if damage == "payload":
            row.payload = {**row.payload, "score": 99}
        elif damage == "receipt_count":
            receipt.row_count += 1
        elif damage == "artifact":
            receipt.artifact_bytes = b"corrupted"
        elif damage == "missing_row":
            db.delete(row)
        elif damage == "missing_receipt":
            db.delete(receipt)
        else:
            db.get(Generation, identity.generation_id).expected_counts = {k: 0 for k in ("pair", "proposal", "membership", "golden", "review")}
    with pytest.raises(GenerationError):
        publisher.promote(identity)
    assert public(factory) == (None, 0, 0, 0)


@pytest.mark.parametrize("damage,code", [
    ("membership", "MEMBERSHIP_CONSISTENCY"), ("golden", "GOLDEN_COVERAGE"),
    ("provenance", "GOLDEN_PROVENANCE"), ("review", "REVIEW_REFERENCE"),
])
def test_result_references_and_coverage(snapshot, damage, code):
    factory, computed, _ = snapshot
    computed = deepcopy(computed)
    proposal = computed["proposals"][0]
    if damage == "membership":
        proposal["member_record_ids"] = ["a", "a"]
    elif damage == "golden":
        proposal["golden_fields"]["member_record_ids"] = ["a"]
    elif damage == "provenance":
        proposal["golden_fields"]["fields"]["name"]["source_record_id"] = "c"
    else:
        computed["review_cases"][0]["member_record_ids"] = ["a"]
    publisher = GenerationPublisher(factory, batch_size=2)
    identity = publisher.begin("run", config={"v": 1})
    with pytest.raises((GenerationError, IntegrityError)) as error:
        publisher.stage(identity, computed)
        publisher.promote(identity)
    if isinstance(error.value, GenerationError):
        assert code in str(error.value) or "DUPLICATE_ROW" in str(error.value)
    assert public(factory) == (None, 0, 0, 0)


def test_cross_run_reference_and_membership_unique(snapshot):
    factory, computed, records = snapshot
    with factory.begin() as db:
        db.add(ResolutionRun(id="other", owner_user_id="owner", record_count=1))
        db.flush()
        db.add(ResolutionFile(id="other-file", run_id="other", filename="other.csv", sha256="other", row_count=1))
        db.flush()
        db.add(ResolutionRecord(id="other-record", run_id="other", file_id="other-file", external_id="x", source_system="CSV", entity_type="PERSON",
            raw_fields={}, normalized_fields={}, quality_flags={}, extra_fields={}))
    publisher = GenerationPublisher(factory, batch_size=2)
    identity = publisher.begin("run", config={"v": 1})
    computed = deepcopy(computed)
    computed["pairs"][0]["right_id"] = "other-record"
    publisher.stage(identity, computed)
    with pytest.raises(GenerationError, match="RECORD_REFERENCE"):
        publisher.promote(identity)
    assert public(factory) == (None, 0, 0, 0)


def test_record_cannot_belong_to_two_proposals(snapshot):
    factory, computed, _ = snapshot
    computed = deepcopy(computed)
    second = deepcopy(computed["proposals"][0])
    second["id"] = "overlapping-proposal"
    computed["proposals"].append(second)
    publisher = GenerationPublisher(factory, batch_size=2)
    identity = publisher.begin("run", config={"v": 1})
    with pytest.raises(GenerationError, match="UNIQUE_OR_REFERENCE"):
        publisher.stage(identity, computed)
    assert public(factory) == (None, 0, 0, 0)


def test_seal_requires_all_declared_rows_and_candidate_coverage(snapshot):
    factory, computed, _ = snapshot
    publisher = GenerationPublisher(factory, batch_size=2)
    identity = publisher.begin("run", config={"v": 1})
    counts = {"pair": 3, "proposal": 1, "membership": 2, "golden": 1, "review": 1}
    with pytest.raises(GenerationError, match="CANDIDATE_COVERAGE"):
        publisher.seal(identity, counts, {"candidate_count": 2, "insufficient_count": 0})
    publisher.load_partition(identity, "pair", 0, computed["pairs"][:2])
    publisher.seal(identity, counts, {"candidate_count": 3, "insufficient_count": 0})
    with pytest.raises(GenerationError, match="ROW_COUNT"):
        publisher.promote(identity)
    assert public(factory) == (None, 0, 0, 0)


def test_conflicting_retry_does_not_replace_receipt(snapshot):
    factory, computed, _ = snapshot
    publisher = GenerationPublisher(factory, batch_size=2)
    identity = publisher.begin("run", config={"v": 1})
    publisher.load_partition(identity, "pair", 0, computed["pairs"][:2])
    changed = deepcopy(computed["pairs"][:2])
    changed[0]["score"] += 1
    with pytest.raises(GenerationError, match="PARTITION_CONFLICT"):
        publisher.load_partition(identity, "pair", 0, changed)
    with factory() as db:
        assert db.query(Row).count() == 2
        assert db.query(Receipt).count() == 1


def test_attempt_fencing_and_stale_failure(snapshot):
    factory, computed, _ = snapshot
    publisher = GenerationPublisher(factory, batch_size=2)
    old = publisher.begin("run", config={"v": 1})
    current = publisher.begin("run", config={"v": 1})
    with pytest.raises(GenerationError, match="FENCED"):
        publisher.stage(old, computed)
    publisher.fail(old)
    with factory() as db:
        assert db.get(Attempt, old.attempt_id).status == "SUPERSEDED"
        assert db.get(Attempt, current.attempt_id).status == "RUNNING"
    publisher.stage(current, computed)
    publisher.promote(current)


def test_reviewer_state_and_golden_override_survive_new_generation(snapshot):
    factory, computed, _ = snapshot
    publisher = GenerationPublisher(factory, batch_size=2)
    first = publisher.begin("run", config={"v": 1})
    publisher.stage(first, computed)
    publisher.promote(first)
    proposal_id = computed["proposals"][0]["id"]
    pair_id = computed["pairs"][0]["id"]
    with factory.begin() as db:
        p = db.get(ResolutionPair, pair_id)
        p.user_decision = "REJECTED"
        p.revision = 7
        proposal = db.get(ResolutionProposal, proposal_id)
        proposal.golden_fields = {**proposal.golden_fields, "_golden_override": {"name": "Reviewer chosen"}}
        case = db.get(ResolutionReviewCase, "review-bc")
        case.status = "APPROVED"
        case.revision = 9
        case.first_reviewer_id = "owner"
        case.first_decision = "ACCEPTED"
        db.add(ResolutionAuditEvent(id="audit", run_id="run", actor_user_id="owner", pair_id=pair_id, action="REVIEW", details={"preserved": True}))
    retry = publisher.begin("run", config={"v": 1})
    assert retry.generation_id != first.generation_id
    publisher.stage(retry, computed)
    publisher.promote(retry)
    with factory() as db:
        assert db.get(ResolutionPair, pair_id).user_decision == "REJECTED"
        assert db.get(ResolutionPair, pair_id).revision == 7
        assert db.get(ResolutionProposal, proposal_id).golden_fields["_golden_override"] == {"name": "Reviewer chosen"}
        assert db.get(ResolutionReviewCase, "review-bc").revision == 9
        assert db.get(ResolutionReviewCase, "review-bc").first_reviewer_id == "owner"
        assert db.get(ResolutionAuditEvent, "audit").details == {"preserved": True}
    with factory.begin() as db, pytest.raises(RuntimeError, match="publication gate"):
        persist_snapshot(db, "run", computed)


@pytest.mark.parametrize("human", [False, True])
def test_input_or_review_change_after_seal_blocks_publication(snapshot, human):
    factory, computed, _ = snapshot
    publisher = GenerationPublisher(factory, batch_size=2)
    first = publisher.begin("run", config={"v": 1})
    publisher.stage(first, computed)
    publisher.promote(first)
    second = publisher.begin("run", config={"v": 2})
    publisher.stage(second, computed)
    before = public(factory)
    with factory.begin() as db:
        if human:
            p = db.get(ResolutionPair, computed["pairs"][0]["id"])
            p.user_decision = "REJECTED"
            p.revision += 1
        else:
            r = db.get(ResolutionRecord, "a")
            r.raw_fields = {**r.raw_fields, "name": "changed"}
    with pytest.raises(GenerationError, match="INPUT_CHANGED"):
        publisher.promote(second)
    assert public(factory) == before


def test_pipeline_uses_generation_gate_and_idempotent_completion(snapshot, staging, monkeypatch):
    factory, _, _ = snapshot
    run_pipeline("run", factory, lambda *args: None)
    before = public(factory)
    assert before[0] is not None
    monkeypatch.setattr("masterone.mdm.resolution_service._compute", lambda *args, **kw: (_ for _ in ()).throw(AssertionError("committed generation must not recompute")))
    run_pipeline("run", factory, lambda *args: None)
    assert public(factory) == before
    monkeypatch.setattr(get_settings(), "generation_staging_enabled", False)
    run_pipeline("run", factory, lambda *args: None)
    assert public(factory) == before  # Existing protected runs keep their gate.


def test_pipeline_start_failure_is_not_left_queued(snapshot, staging):
    factory, _, _ = snapshot
    with factory.begin() as db:
        db.get(ResolutionRun, "run").record_count = 99
    with pytest.raises(GenerationError, match="INPUT_COUNT"):
        run_pipeline("run", factory, lambda *args: None)
    with factory() as db:
        assert db.get(ResolutionTask, "task").status == "RETRYABLE_FAILED"
        assert db.get(ResolutionTask, "task").error_code == "GENERATION_START_FAILED"


def test_pipeline_upload_failure_keeps_serving_private(snapshot, staging, monkeypatch):
    factory, _, _ = snapshot
    store = MemoryObjects()
    store.fail_upload = True
    monkeypatch.setattr("masterone.mdm.generation_staging.get_store", lambda settings: store)
    with pytest.raises(OSError, match="R2 failure"):
        run_pipeline("run", factory, lambda *args: None)
    assert public(factory) == (None, 0, 0, 0)
    with factory() as db:
        assert db.get(ResolutionTask, "task").status == "RETRYABLE_FAILED"


def test_direct_stage_runner_uses_gate(snapshot, staging, tmp_path):
    factory, _, records = snapshot
    url = factory.kw["bind"].url.render_as_string(hide_password=False)
    runner = StageRunner("run", "direct-attempt", tmp_path / "direct", db_url=url)
    try:
        runner.run_all(records, policy_version="v", return_data=False)
        assert public(factory)[0] is not None
        with factory() as db:
            assert db.get(Attempt, "direct-attempt").status == "SUCCEEDED"
    finally:
        runner.close_decisions()
        runner.con.close()


def test_generation_migration_preservation_and_nondestructive_rollback(tmp_path, monkeypatch):
    monkeypatch.delenv("MDM_DATABASE_URL", raising=False)
    url = "sqlite:///" + (tmp_path / "migration.db").as_posix()
    cfg = alembic_config(url)
    command.upgrade(cfg, "0021_input_contract")
    engine = create_engine(url)
    with engine.begin() as db:
        db.execute(User.__table__.insert().values(id="owner", email="owner@example.test", email_normalized="owner@example.test", password_hash="unused", role="STEWARD"))
        db.execute(text("INSERT INTO mdm_resolution_runs (id, owner_user_id, name, status, phase, policy_version, record_count, file_count, candidate_count, insufficient_count, created_at) VALUES ('run', 'owner', 'Legacy', 'UPLOADED', 'upload', 'person-v1', 2, 0, 0, 0, CURRENT_TIMESTAMP)"))
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory.begin() as db:
        db.add(ResolutionFile(id="file", run_id="run", filename="test.csv", sha256="test", row_count=2))
        db.flush()
        db.add_all([ResolutionRecord(id=rid, run_id="run", file_id="file", external_id=rid, source_system="CSV",
            entity_type="PERSON", raw_fields={"name": rid}, normalized_fields={}, quality_flags=[], extra_fields={}) for rid in "ab"])
        db.flush()
        db.add(ResolutionPair(id="old-pair", run_id="run", left_record_id="a", right_record_id="b",
            blocking_reasons=[], field_evidence=[], score=20, pair_decision="REVIEW", decision_reason="REVIEW",
            triage_status="REVIEW", triage_reason="REVIEW", user_decision="REJECTED", revision=5))
        db.add(ResolutionProposal(id="old-proposal", run_id="run", member_record_ids=["a", "b"],
            golden_fields={"_golden_override": {"name": "Legacy override"}}, origin="REVIEW", status="REJECTED", revision=7))
        db.add(ResolutionReviewCase(id="old-case", run_id="run", pair_ids=["old-pair"], member_record_ids=["a", "b"],
            primary_group="D", subgroup="D1", signature_hash="old", triage_reason="REVIEW", status="REJECTED", revision=3))
        db.flush()
        db.add(ResolutionAuditEvent(id="old-audit", run_id="run", actor_user_id="owner", pair_id="old-pair", proposal_id="old-proposal", action="REVIEW", details={"kept": True}))
    command.upgrade(cfg, "head")
    with factory() as db:
        assert db.get(ResolutionPublication, "run") is None
        assert db.get(ResolutionRun, "run").name == "Legacy"
    publisher = GenerationPublisher(factory, batch_size=2)
    identity = publisher.begin("run", config={"v": 1})
    publisher.stage(identity, {"pairs": [], "proposals": [], "review_cases": [], "candidate_count": 0, "insufficient_count": 0})
    publisher.promote(identity)
    command.downgrade(cfg, "0021_input_contract")
    with factory() as db:
        assert db.get(ResolutionPublication, "run").active_generation_id == identity.generation_id
        assert db.query(Receipt).count() == 1
        assert db.get(Generation, identity.generation_id).state == "PROMOTED"
    command.upgrade(cfg, "head")
    with factory() as db:
        assert db.get(ResolutionRun, "run").name == "Legacy"
        assert db.get(ResolutionPair, "old-pair").user_decision == "REJECTED"
        assert db.get(ResolutionPair, "old-pair").revision == 5
        assert db.get(ResolutionProposal, "old-proposal").golden_fields == {"_golden_override": {"name": "Legacy override"}}
        assert db.get(ResolutionReviewCase, "old-case").revision == 3
        assert db.get(ResolutionAuditEvent, "old-audit").details == {"kept": True}
        assert db.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == HEAD
    assert {"mdm_resolution_generations", "mdm_resolution_generation_attempts", "mdm_resolution_staged_results", "mdm_resolution_partition_receipts"} <= set(inspect(engine).get_table_names())
    engine.dispose()
