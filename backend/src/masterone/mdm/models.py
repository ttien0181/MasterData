from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    event,
    false,
    inspect,
    update,
)
from sqlalchemy.orm import Mapped, Session, mapped_column

# The foreign keys below point at "users"; importing it here registers that table in every process.
from masterone.auth import models as _auth_models  # noqa: F401
from masterone.db.base import Base, now_utc


class ResolutionRun(Base):
    __tablename__ = "mdm_resolution_runs"
    __table_args__ = (UniqueConstraint("upload_session_id", name="uq_mdm_run_upload_session"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    upload_session_id: Mapped[str | None] = mapped_column(
        ForeignKey("mdm_resolution_upload_sessions.id", ondelete="SET NULL"), nullable=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="UPLOADED")
    phase: Mapped[str] = mapped_column(String(40), default="upload")
    policy_version: Mapped[str] = mapped_column(String(40), default="person-v1")
    record_count: Mapped[int] = mapped_column(BigInteger, default=0)
    file_count: Mapped[int] = mapped_column(Integer, default=0)
    candidate_count: Mapped[int] = mapped_column(Integer, default=0)
    insufficient_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ResolutionFile(Base):
    __tablename__ = "mdm_resolution_files"
    __table_args__ = (UniqueConstraint("run_id", "sha256", name="uq_mdm_resolution_file_hash"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    run_id: Mapped[str] = mapped_column(ForeignKey("mdm_resolution_runs.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64))
    row_count: Mapped[int] = mapped_column(BigInteger)
    # R2 key of the original uploaded CSV bytes (null when R2 is not configured)
    r2_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ResolutionUploadSession(Base):
    __tablename__ = "mdm_resolution_upload_sessions"
    __table_args__ = (Index("ix_mdm_upload_sessions_owner_status", "owner_user_id", "status", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="UPLOADING")
    storage_backend: Mapped[str] = mapped_column(String(12), default="local")
    total_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)


class ResolutionUploadFile(Base):
    __tablename__ = "mdm_resolution_upload_files"
    __table_args__ = (UniqueConstraint("session_id", "position", name="uq_mdm_upload_file_position"),
                      UniqueConstraint("replaces_file_id", name="uq_mdm_upload_file_replacement"),
                      Index("ix_mdm_upload_files_session", "session_id", "position"))

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    session_id: Mapped[str] = mapped_column(ForeignKey("mdm_resolution_upload_sessions.id", ondelete="CASCADE"), index=True)
    replaces_file_id: Mapped[str | None] = mapped_column(
        ForeignKey("mdm_resolution_upload_files.id", ondelete="SET NULL"), nullable=True)
    position: Mapped[int] = mapped_column(Integer)
    filename: Mapped[str] = mapped_column(String(255))
    expected_size: Mapped[int] = mapped_column(BigInteger)
    received_size: Mapped[int] = mapped_column(BigInteger, default=0)
    status: Mapped[str] = mapped_column(String(24), default="UPLOADING")
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    row_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source_counts: Mapped[dict] = mapped_column(JSON, default=dict)
    validation_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)


class ResolutionUploadPart(Base):
    __tablename__ = "mdm_resolution_upload_parts"
    __table_args__ = (UniqueConstraint("upload_file_id", "part_number", name="uq_mdm_upload_part_number"),
                      Index("ix_mdm_upload_parts_file", "upload_file_id", "part_number"))

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    upload_file_id: Mapped[str] = mapped_column(ForeignKey("mdm_resolution_upload_files.id", ondelete="CASCADE"), index=True)
    part_number: Mapped[int] = mapped_column(BigInteger)
    byte_size: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ResolutionRecord(Base):
    __tablename__ = "mdm_resolution_records"
    __table_args__ = (
        UniqueConstraint("run_id", "source_system", "external_id", name="uq_mdm_resolution_record_source_external"),
        Index("ix_mdm_resolution_records_run_source", "run_id", "source_system"),
        Index("ix_mdm_resolution_records_run_entity_type", "run_id", "entity_type"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    run_id: Mapped[str] = mapped_column(ForeignKey("mdm_resolution_runs.id", ondelete="CASCADE"), index=True)
    file_id: Mapped[str] = mapped_column(ForeignKey("mdm_resolution_files.id", ondelete="CASCADE"), index=True)
    external_id: Mapped[str] = mapped_column(String(200))
    source_system: Mapped[str] = mapped_column(String(32))
    # PERSON | ORGANIZATION | UNKNOWN — inferred from data or set by steward
    entity_type: Mapped[str] = mapped_column(String(16), default="UNKNOWN")
    # Ground-truth cluster label from benchmark CSV (entity_id column); null for production data
    benchmark_entity_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    original_fields: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    mapping_provenance: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    lineage: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    raw_fields: Mapped[dict] = mapped_column(JSON)
    normalized_fields: Mapped[dict] = mapped_column(JSON)
    quality_flags: Mapped[list] = mapped_column(JSON, default=list)
    # Extra CSV columns (noise_type, difficulty, temporal_case, source-specific IDs, etc.)
    extra_fields: Mapped[dict] = mapped_column(JSON, default=dict)


class ResolutionTask(Base):
    __tablename__ = "mdm_resolution_tasks"
    __table_args__ = (
        Index("ix_mdm_resolution_tasks_queue", "status", "created_at", "id"),
        Index("ix_mdm_resolution_tasks_status_created", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    run_id: Mapped[str] = mapped_column(ForeignKey("mdm_resolution_runs.id", ondelete="CASCADE"), unique=True)
    status: Mapped[str] = mapped_column(String(24), default="QUEUED")
    phase: Mapped[str] = mapped_column(String(40), default="queued")
    processed_count: Mapped[int] = mapped_column(Integer, default=0)
    total_count: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)
    attempt_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, server_default="3")
    cancel_requested: Mapped[bool] = mapped_column(default=False, server_default=false())


class ResolutionJobAttempt(Base):
    __tablename__ = "mdm_resolution_job_attempts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("mdm_resolution_tasks.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("mdm_resolution_runs.id", ondelete="CASCADE"), index=True)
    worker_id: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(24), default="RUNNING")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)


class ResolutionPair(Base):
    __tablename__ = "mdm_resolution_pairs"
    __table_args__ = (
        UniqueConstraint("run_id", "left_record_id", "right_record_id", name="uq_mdm_resolution_pair_records"),
        Index("ix_mdm_resolution_pairs_run_triage", "run_id", "triage_status"),
        Index("ix_mdm_resolution_pairs_run_staged", "run_id", "staged_for_merge"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("mdm_resolution_runs.id", ondelete="CASCADE"), index=True)
    left_record_id: Mapped[str] = mapped_column(ForeignKey("mdm_resolution_records.id"))
    right_record_id: Mapped[str] = mapped_column(ForeignKey("mdm_resolution_records.id"))
    blocking_reasons: Mapped[list] = mapped_column(JSON)
    field_evidence: Mapped[list] = mapped_column(JSON)
    score: Mapped[float] = mapped_column()
    pair_decision: Mapped[str] = mapped_column(String(24))
    decision_reason: Mapped[str] = mapped_column(String(80))
    triage_status: Mapped[str] = mapped_column(String(32))
    triage_reason: Mapped[str] = mapped_column(String(80))
    primary_group: Mapped[str | None] = mapped_column(String(32), nullable=True)
    subgroup: Mapped[str | None] = mapped_column(String(80), nullable=True)
    signature: Mapped[str | None] = mapped_column(String(128), nullable=True)
    normalization_tags: Mapped[list] = mapped_column(JSON, default=list)
    policy_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    policy_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    user_decision: Mapped[str] = mapped_column(String(24), default="UNDECIDED")
    staged_for_merge: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    revision: Mapped[int] = mapped_column(Integer, default=1)


class ResolutionReviewCase(Base):
    __tablename__ = "mdm_resolution_review_cases"
    __table_args__ = (Index("ix_mdm_resolution_review_cases_run_category", "run_id", "primary_group"),
                      Index("ix_mdm_resolution_review_cases_signature", "run_id", "signature_hash"))

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("mdm_resolution_runs.id", ondelete="CASCADE"), index=True)
    pair_ids: Mapped[list] = mapped_column(JSON)
    member_record_ids: Mapped[list] = mapped_column(JSON)
    primary_group: Mapped[str] = mapped_column(String(48))
    subgroup: Mapped[str] = mapped_column(String(48))
    signature_hash: Mapped[str] = mapped_column(String(64), default="")
    triage_reason: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(24), default="OPEN")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    # TASK-6.2: four-eyes approval for Group D (§SVC-02-FR-006)
    first_reviewer_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    first_decision: Mapped[str | None] = mapped_column(String(24), nullable=True)
    first_decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    second_reviewer_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    second_decision: Mapped[str | None] = mapped_column(String(24), nullable=True)
    second_decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ResolutionProposal(Base):
    __tablename__ = "mdm_resolution_proposals"
    __table_args__ = (Index("ix_mdm_resolution_proposals_run_status", "run_id", "status"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("mdm_resolution_runs.id", ondelete="CASCADE"), index=True)
    member_record_ids: Mapped[list] = mapped_column(JSON)
    golden_fields: Mapped[dict] = mapped_column(JSON)
    # PERSON | ORGANIZATION | UNKNOWN — must match all member records
    entity_type: Mapped[str] = mapped_column(String(16), default="UNKNOWN")
    status: Mapped[str] = mapped_column(String(32), default="AUTO_READY")
    origin: Mapped[str] = mapped_column(String(16), default="AUTO")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    decision_reason: Mapped[str | None] = mapped_column(String(80), nullable=True)


class ResolutionExplanation(Base):
    __tablename__ = "mdm_resolution_explanations"
    __table_args__ = (UniqueConstraint("run_id", "signature_hash", name="uq_mdm_resolution_explanation_signature"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    run_id: Mapped[str] = mapped_column(ForeignKey("mdm_resolution_runs.id", ondelete="CASCADE"), index=True)
    signature_hash: Mapped[str] = mapped_column(String(64))
    primary_group: Mapped[str] = mapped_column(String(48))
    subgroup: Mapped[str] = mapped_column(String(48))
    status: Mapped[str] = mapped_column(String(24), default="PENDING")
    explanation: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    usage: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)


class ResolutionAuditEvent(Base):
    __tablename__ = "mdm_resolution_audit_events"
    __table_args__ = (Index("ix_mdm_resolution_audit_run_created", "run_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    run_id: Mapped[str] = mapped_column(ForeignKey("mdm_resolution_runs.id", ondelete="CASCADE"), index=True)
    actor_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    pair_id: Mapped[str | None] = mapped_column(ForeignKey("mdm_resolution_pairs.id"), nullable=True)
    proposal_id: Mapped[str | None] = mapped_column(ForeignKey("mdm_resolution_proposals.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(40))
    details: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ResolutionExport(Base):
    __tablename__ = "mdm_resolution_exports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    run_id: Mapped[str] = mapped_column(ForeignKey("mdm_resolution_runs.id", ondelete="CASCADE"), index=True)
    parent_export_id: Mapped[str | None] = mapped_column(ForeignKey("mdm_resolution_exports.id"), nullable=True)
    actor_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    # csv_bytes is NULL when the file is stored in R2 (r2_key is set instead)
    csv_bytes: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    r2_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    row_count: Mapped[int] = mapped_column(Integer)
    merged_group_count: Mapped[int] = mapped_column(Integer)
    pending_review_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ResolutionPreview(Base):
    __tablename__ = "mdm_resolution_previews"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    run_id: Mapped[str] = mapped_column(ForeignKey("mdm_resolution_runs.id", ondelete="CASCADE"), index=True)
    actor_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    policy_version: Mapped[str] = mapped_column(String(40))
    pair_set_hash: Mapped[str] = mapped_column(String(64))
    proposal_revisions: Mapped[dict] = mapped_column(JSON)
    choices: Mapped[dict] = mapped_column(JSON)
    readiness_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    staged_plan: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    acknowledge_unreviewed: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(String(20), default="OPEN")
    execute_request_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    export_id: Mapped[str | None] = mapped_column(ForeignKey("mdm_resolution_exports.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ResolutionMergeEvent(Base):
    __tablename__ = "mdm_resolution_merge_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    run_id: Mapped[str] = mapped_column(ForeignKey("mdm_resolution_runs.id", ondelete="CASCADE"), index=True)
    preview_id: Mapped[str] = mapped_column(ForeignKey("mdm_resolution_previews.id"))
    export_id: Mapped[str] = mapped_column(ForeignKey("mdm_resolution_exports.id"))
    actor_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    proposal_id: Mapped[str] = mapped_column(ForeignKey("mdm_resolution_proposals.id"))
    member_record_ids: Mapped[list] = mapped_column(JSON)
    pair_ids: Mapped[list] = mapped_column(JSON)
    golden_fields: Mapped[dict] = mapped_column(JSON)
    guard_results: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ResolutionSplitEvent(Base):
    __tablename__ = "mdm_resolution_split_events"
    __table_args__ = (UniqueConstraint("run_id", "request_id", name="uq_mdm_resolution_split_request"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    run_id: Mapped[str] = mapped_column(ForeignKey("mdm_resolution_runs.id", ondelete="CASCADE"), index=True)
    before_export_id: Mapped[str] = mapped_column(ForeignKey("mdm_resolution_exports.id"))
    after_export_id: Mapped[str] = mapped_column(ForeignKey("mdm_resolution_exports.id"))
    actor_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    request_id: Mapped[str] = mapped_column(String(80))
    proposal_id: Mapped[str] = mapped_column(ForeignKey("mdm_resolution_proposals.id"))
    separated_record_id: Mapped[str] = mapped_column(ForeignKey("mdm_resolution_records.id"))
    retained_record_ids: Mapped[list] = mapped_column(JSON)
    negative_pair_ids: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)




# ── TASK-5.1: Persistent entities (§6.3) ─────────────────────────────────────

class ResolutionEntity(Base):
    __tablename__ = "mdm_resolution_entities"
    __table_args__ = (
        Index("ix_mdm_resolution_entities_run", "run_id"),
        Index("ix_mdm_resolution_entities_status", "run_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    run_id: Mapped[str] = mapped_column(ForeignKey("mdm_resolution_runs.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE")  # ACTIVE | SPLIT | MERGED
    entity_type: Mapped[str] = mapped_column(String(16), default="UNKNOWN")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)


class ResolutionEntityMember(Base):
    __tablename__ = "mdm_resolution_entity_members"
    __table_args__ = (
        UniqueConstraint("entity_id", "record_id", name="uq_mdm_entity_member"),
        Index("ix_mdm_entity_members_record", "record_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    entity_id: Mapped[str] = mapped_column(ForeignKey("mdm_resolution_entities.id", ondelete="CASCADE"), index=True)
    record_id: Mapped[str] = mapped_column(ForeignKey("mdm_resolution_records.id"))
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    added_by: Mapped[str] = mapped_column(String(36), default="SYSTEM")  # "SYSTEM" | user_id


class ResolutionEntityVersion(Base):
    __tablename__ = "mdm_resolution_entity_versions"
    __table_args__ = (
        UniqueConstraint("entity_id", "version", name="uq_mdm_entity_version"),
        Index("ix_mdm_entity_versions_entity", "entity_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    entity_id: Mapped[str] = mapped_column(ForeignKey("mdm_resolution_entities.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    golden_fields: Mapped[dict] = mapped_column(JSON)
    changed_by: Mapped[str] = mapped_column(String(36), default="SYSTEM")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


# ── TASK-5.2: Negative edges table (§6.3) ────────────────────────────────────

class ResolutionNegativeEdge(Base):
    __tablename__ = "mdm_resolution_negative_edges"
    __table_args__ = (
        Index("ix_mdm_negative_edges_run", "run_id"),
        Index("ix_mdm_negative_edges_pair", "run_id", "left_record_id", "right_record_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    run_id: Mapped[str] = mapped_column(ForeignKey("mdm_resolution_runs.id", ondelete="CASCADE"), index=True)
    left_record_id: Mapped[str] = mapped_column(ForeignKey("mdm_resolution_records.id"))
    right_record_id: Mapped[str] = mapped_column(ForeignKey("mdm_resolution_records.id"))
    created_by: Mapped[str] = mapped_column(String(36))  # user_id or "SYSTEM"
    reason: Mapped[str] = mapped_column(String(32))  # STEWARD_REJECT | HARD_CONSTRAINT | SPLIT
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


# ── TASK-5.3: Golden field values table (§6.3) ───────────────────────────────

class ResolutionGoldenFieldValue(Base):
    __tablename__ = "mdm_resolution_golden_field_values"
    __table_args__ = (
        Index("ix_mdm_golden_field_values_entity", "entity_id", "version"),
        UniqueConstraint("entity_id", "version", "field_name", name="uq_mdm_golden_field"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    entity_id: Mapped[str] = mapped_column(ForeignKey("mdm_resolution_entities.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    field_name: Mapped[str] = mapped_column(String(64))
    display_value: Mapped[str] = mapped_column(Text)
    source_record_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # SOURCE_PRIORITY | STEWARD_OVERRIDE | QUALITY_TIER
    provenance: Mapped[str] = mapped_column(String(24), default="SOURCE_PRIORITY")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


# ── TASK-8.1: Stage result persistence (§XC-RUN-001) ─────────────────────────

class ResolutionStageResult(Base):
    """One row per (run, attempt, stage) execution — records success, duration, metrics."""
    __tablename__ = "mdm_resolution_stage_results"
    __table_args__ = (
        Index("ix_mdm_stage_results_run", "run_id", "attempt_id"),
        UniqueConstraint("run_id", "attempt_id", "stage_name", name="uq_mdm_stage_result"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    run_id: Mapped[str] = mapped_column(ForeignKey("mdm_resolution_runs.id", ondelete="CASCADE"), index=True)
    attempt_id: Mapped[str] = mapped_column(String(80))
    stage_name: Mapped[str] = mapped_column(String(24))  # PREPARE | NORMALIZE | ...
    success: Mapped[bool] = mapped_column(default=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    output_artifacts: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ResolutionEngineSelection(Base):
    __tablename__ = "mdm_resolution_engine_selections"
    run_id: Mapped[str] = mapped_column(ForeignKey("mdm_resolution_runs.id", ondelete="CASCADE"), primary_key=True)
    engine_version: Mapped[str] = mapped_column(String(32))
    configuration: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ResolutionGeneration(Base):
    __tablename__ = "mdm_resolution_generations"
    __table_args__ = (UniqueConstraint("run_id", "identity_sha256", name="uq_mdm_generation_identity"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("mdm_resolution_runs.id", ondelete="CASCADE"), index=True)
    identity_sha256: Mapped[str] = mapped_column(String(64))
    snapshot_sha256: Mapped[str] = mapped_column(String(64))
    review_sha256: Mapped[str] = mapped_column(String(64))
    config_sha256: Mapped[str] = mapped_column(String(64))
    base_generation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    artifact_backend: Mapped[str] = mapped_column(String(8))  # DB | OBJECT
    state: Mapped[str] = mapped_column(String(16), default="STAGING")
    record_count: Mapped[int] = mapped_column(BigInteger)
    expected_counts: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    manifest_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ResolutionGenerationAttempt(Base):
    __tablename__ = "mdm_resolution_generation_attempts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    generation_id: Mapped[str] = mapped_column(ForeignKey("mdm_resolution_generations.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="RUNNING")
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ResolutionPublication(Base):
    """Separate pointer keeps legacy ResolutionRun schema/ORM inserts compatible."""
    __tablename__ = "mdm_resolution_publications"

    run_id: Mapped[str] = mapped_column(ForeignKey("mdm_resolution_runs.id", ondelete="CASCADE"), primary_key=True)
    active_generation_id: Mapped[str | None] = mapped_column(ForeignKey("mdm_resolution_generations.id", ondelete="SET NULL"), nullable=True)
    pipeline_attempt_id: Mapped[str | None] = mapped_column(ForeignKey("mdm_resolution_generation_attempts.id", ondelete="SET NULL"), nullable=True)


class ResolutionStagedResult(Base):
    """Private pair/proposal/membership/golden/review projections; never API rows."""
    __tablename__ = "mdm_resolution_staged_results"
    __table_args__ = (
        CheckConstraint("kind IN ('pair','proposal','membership','golden','review')", name="ck_mdm_stage_kind"),
        CheckConstraint("kind != 'pair' OR left_id < right_id", name="ck_mdm_stage_pair_order"),
        UniqueConstraint("generation_id", "kind", "left_id", "right_id", name="uq_mdm_stage_pair"),
        UniqueConstraint("generation_id", "kind", "record_id", name="uq_mdm_stage_member"),
        Index("ix_mdm_stage_partition", "generation_id", "kind", "partition_no"),
    )

    generation_id: Mapped[str] = mapped_column(ForeignKey("mdm_resolution_generations.id", ondelete="CASCADE"), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    row_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    partition_no: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON)
    sha256: Mapped[str] = mapped_column(String(64))
    left_id: Mapped[str | None] = mapped_column(ForeignKey("mdm_resolution_records.id"), nullable=True)
    right_id: Mapped[str | None] = mapped_column(ForeignKey("mdm_resolution_records.id"), nullable=True)
    record_id: Mapped[str | None] = mapped_column(ForeignKey("mdm_resolution_records.id"), nullable=True)
    proposal_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ResolutionPartitionReceipt(Base):
    __tablename__ = "mdm_resolution_partition_receipts"
    __table_args__ = (CheckConstraint("row_count >= 0 AND byte_count > 0", name="ck_mdm_receipt_counts"),)

    generation_id: Mapped[str] = mapped_column(ForeignKey("mdm_resolution_generations.id", ondelete="CASCADE"), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    partition_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    attempt_id: Mapped[str] = mapped_column(ForeignKey("mdm_resolution_generation_attempts.id"))
    row_count: Mapped[int] = mapped_column(Integer)
    byte_count: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    object_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    artifact_bytes: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ResolutionCheckpointObject(Base):
    __tablename__ = "mdm_checkpoint_objects"
    key: Mapped[str] = mapped_column(String(512), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("mdm_resolution_runs.id", ondelete="CASCADE"), index=True)
    sha256: Mapped[str] = mapped_column(String(64))
    byte_count: Mapped[int] = mapped_column(BigInteger)
    content: Mapped[bytes] = mapped_column(LargeBinary)


class ResolutionCheckpoint(Base):
    __tablename__ = "mdm_checkpoints"
    __table_args__ = (Index("ix_mdm_checkpoint_lookup", "run_id", "stage", "scope", "pins_sha256", "input_sha256", "state"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("mdm_resolution_runs.id", ondelete="CASCADE"))
    attempt_id: Mapped[str] = mapped_column(String(128))
    stage: Mapped[str] = mapped_column(String(32))
    scope: Mapped[str] = mapped_column(String(128))
    pins_sha256: Mapped[str] = mapped_column(String(64))
    input_sha256: Mapped[str] = mapped_column(String(64))
    output_sha256: Mapped[str] = mapped_column(String(64))
    manifest_sha256: Mapped[str] = mapped_column(String(64))
    manifest: Mapped[dict] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(24))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ResolutionCheckpointDependency(Base):
    __tablename__ = "mdm_checkpoint_dependencies"
    child_id: Mapped[str] = mapped_column(ForeignKey("mdm_checkpoints.id"), primary_key=True)
    parent_id: Mapped[str] = mapped_column(ForeignKey("mdm_checkpoints.id"), primary_key=True)


class LLMRequest(Base):
    __tablename__ = "llm_requests"
    __table_args__ = (Index("ix_llm_requests_created_status", "created_at", "status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    request_type: Mapped[str] = mapped_column(String(50))
    model: Mapped[str] = mapped_column(String(50))
    provider: Mapped[str] = mapped_column(String(50))
    prompt_version: Mapped[str] = mapped_column(String(50))
    schema_version: Mapped[str] = mapped_column(String(50))
    request_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24))
    pair_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    response_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class LLMPolicyControl(Base):
    """Shared emergency switch and Gemini quota cooldown; every process reads these rows."""
    __tablename__ = "llm_policy_control"

    id: Mapped[str] = mapped_column(String(24), primary_key=True)
    enabled: Mapped[bool] = mapped_column(default=False)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    gemini_blocked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LLMReservation(Base):
    """Durable logical LLM request and conservative accounting. Never stores raw prompts."""
    __tablename__ = "llm_reservations"
    __table_args__ = (Index("ix_llm_reservations_state", "state"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    purpose: Mapped[str] = mapped_column(String(24))
    context_hash: Mapped[str] = mapped_column(String(64))
    context: Mapped[dict] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(24))
    calls: Mapped[int] = mapped_column(Integer, default=0)
    reserved_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    reserved_cost_microusd: Mapped[int] = mapped_column(BigInteger, default=0)
    response: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ResolutionRunSummary(Base):
    """Overview figures stored once per run instead of recomputed by every overview request.

    `source_counts` is written at commit and never changes. `payload` holds the review and
    pipeline figures, computed when the pipeline publishes and again after any write to the
    run's pairs, proposals or review cases. Those writes bump `version` in the same transaction.
    """
    __tablename__ = "mdm_resolution_run_summaries"

    run_id: Mapped[str] = mapped_column(ForeignKey("mdm_resolution_runs.id", ondelete="CASCADE"), primary_key=True)
    source_counts: Mapped[dict] = mapped_column(JSON, default=dict)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=0)
    payload_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)


_SUMMARY_SOURCES = (ResolutionPair, ResolutionProposal, ResolutionReviewCase)


@event.listens_for(Session, "before_flush")
def _note_summary_runs(session, flush_context, instances):
    # Every ORM write to the tables behind the overview counters marks its run as changed.
    touched = {obj.run_id for obj in (*session.new, *session.dirty, *session.deleted)
               if isinstance(obj, _SUMMARY_SOURCES)}
    if touched:
        session.info.setdefault("summary_runs", set()).update(touched)


@event.listens_for(Session, "after_flush")
def _bump_summary_versions(session, flush_context):
    touched = session.info.pop("summary_runs", None)
    if not touched:
        return
    connection = session.connection()
    # Schemas older than the summary table (migration tests, pre-0026 databases) have nothing to bump.
    if not inspect(connection).has_table(ResolutionRunSummary.__tablename__):
        return
    connection.execute(update(ResolutionRunSummary).where(ResolutionRunSummary.run_id.in_(touched))
                       .values(version=ResolutionRunSummary.version + 1))
