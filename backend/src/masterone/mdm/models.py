from datetime import datetime
from uuid import uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, LargeBinary, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from masterone.db.base import Base, now_utc


class ResolutionRun(Base):
    __tablename__ = "mdm_resolution_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(24), default="UPLOADED")
    phase: Mapped[str] = mapped_column(String(40), default="upload")
    policy_version: Mapped[str] = mapped_column(String(40), default="person-v1")
    record_count: Mapped[int] = mapped_column(Integer, default=0)
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
    row_count: Mapped[int] = mapped_column(Integer)
    # R2 key of the original uploaded CSV bytes (null when R2 is not configured)
    r2_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ResolutionRecord(Base):
    __tablename__ = "mdm_resolution_records"
    __table_args__ = (
        UniqueConstraint("run_id", "external_id", name="uq_mdm_resolution_record_external"),
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
    raw_fields: Mapped[dict] = mapped_column(JSON)
    normalized_fields: Mapped[dict] = mapped_column(JSON)
    quality_flags: Mapped[list] = mapped_column(JSON, default=list)
    # Extra CSV columns (noise_type, difficulty, temporal_case, source-specific IDs, etc.)
    extra_fields: Mapped[dict] = mapped_column(JSON, default=dict)


class ResolutionTask(Base):
    __tablename__ = "mdm_resolution_tasks"
    __table_args__ = (Index("ix_mdm_resolution_tasks_status_created", "status", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    run_id: Mapped[str] = mapped_column(ForeignKey("mdm_resolution_runs.id", ondelete="CASCADE"), unique=True)
    status: Mapped[str] = mapped_column(String(24), default="QUEUED")
    phase: Mapped[str] = mapped_column(String(40), default="queued")
    processed_count: Mapped[int] = mapped_column(Integer, default=0)
    total_count: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)


class ResolutionPair(Base):
    __tablename__ = "mdm_resolution_pairs"
    __table_args__ = (
        UniqueConstraint("run_id", "left_record_id", "right_record_id", name="uq_mdm_resolution_pair_records"),
        Index("ix_mdm_resolution_pairs_run_triage", "run_id", "triage_status"),
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


