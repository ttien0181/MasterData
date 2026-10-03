from datetime import datetime
from uuid import uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, LargeBinary, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from masterone.db.base import Base, now_utc


class ImportBatch(Base):
    __tablename__ = "mdm_imports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    profile: Mapped[dict] = mapped_column(JSON)
    owner_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    records: Mapped[list["SourceRecord"]] = relationship(cascade="all, delete-orphan")
    candidates: Mapped[list["Candidate"]] = relationship(cascade="all, delete-orphan")


class SourceRecord(Base):
    __tablename__ = "mdm_source_records"
    __table_args__ = (UniqueConstraint("import_id", "source", "source_record_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    import_id: Mapped[str] = mapped_column(ForeignKey("mdm_imports.id"), index=True)
    source: Mapped[str] = mapped_column(String(8))
    source_record_id: Mapped[str] = mapped_column(String(200))
    raw_fields: Mapped[dict] = mapped_column(JSON)
    normalized_fields: Mapped[dict] = mapped_column(JSON)


class Candidate(Base):
    __tablename__ = "mdm_candidates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    import_id: Mapped[str] = mapped_column(ForeignKey("mdm_imports.id"), index=True)
    left_record_id: Mapped[str] = mapped_column(ForeignKey("mdm_source_records.id"))
    right_record_id: Mapped[str] = mapped_column(ForeignKey("mdm_source_records.id"))
    engine_id: Mapped[str] = mapped_column(String(80))
    evidence: Mapped[list[dict]] = mapped_column(JSON)
    decision: Mapped[str] = mapped_column(String(20), default="pending")
    drafts: Mapped[list["GoldenRecordDraft"]] = relationship(cascade="all, delete-orphan")


class GoldenRecordDraft(Base):
    __tablename__ = "mdm_golden_record_drafts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    candidate_id: Mapped[str] = mapped_column(ForeignKey("mdm_candidates.id"), unique=True)
    fields: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)


class AuditEvent(Base):
    __tablename__ = "mdm_audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    candidate_id: Mapped[str] = mapped_column(ForeignKey("mdm_candidates.id"), index=True)
    action: Mapped[str] = mapped_column(String(40))
    details: Mapped[dict] = mapped_column(JSON)
    actor: Mapped[str] = mapped_column(String(40), default="local_steward")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ResolutionRun(Base):
    __tablename__ = "mdm_resolution_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(24), default="UPLOADED")
    phase: Mapped[str] = mapped_column(String(40), default="upload")
    policy_version: Mapped[str] = mapped_column(String(40), default="person-v1")
    record_count: Mapped[int] = mapped_column(Integer, default=0)
    file_count: Mapped[int] = mapped_column(Integer, default=0)
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ResolutionRecord(Base):
    __tablename__ = "mdm_resolution_records"
    __table_args__ = (
        UniqueConstraint("run_id", "external_id", name="uq_mdm_resolution_record_external"),
        Index("ix_mdm_resolution_records_run_source", "run_id", "source_system"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    run_id: Mapped[str] = mapped_column(ForeignKey("mdm_resolution_runs.id", ondelete="CASCADE"), index=True)
    file_id: Mapped[str] = mapped_column(ForeignKey("mdm_resolution_files.id", ondelete="CASCADE"), index=True)
    external_id: Mapped[str] = mapped_column(String(200))
    source_system: Mapped[str] = mapped_column(String(32))
    raw_fields: Mapped[dict] = mapped_column(JSON)
    normalized_fields: Mapped[dict] = mapped_column(JSON)
    quality_flags: Mapped[list] = mapped_column(JSON, default=list)


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
    status: Mapped[str] = mapped_column(String(32), default="AUTO_READY")
    origin: Mapped[str] = mapped_column(String(16), default="AUTO")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    decision_reason: Mapped[str | None] = mapped_column(String(80), nullable=True)


class ResolutionEntity(Base):
    __tablename__ = "mdm_resolution_entities"
    __table_args__ = (UniqueConstraint("run_id", "proposal_id", name="uq_mdm_resolution_entity_proposal"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    run_id: Mapped[str] = mapped_column(ForeignKey("mdm_resolution_runs.id", ondelete="CASCADE"), index=True)
    proposal_id: Mapped[str] = mapped_column(ForeignKey("mdm_resolution_proposals.id"))
    member_record_ids: Mapped[list] = mapped_column(JSON)
    fields: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ResolutionHistory(Base):
    __tablename__ = "mdm_resolution_history"
    __table_args__ = (Index("ix_mdm_resolution_history_run_created", "run_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    run_id: Mapped[str] = mapped_column(ForeignKey("mdm_resolution_runs.id", ondelete="CASCADE"), index=True)
    proposal_id: Mapped[str] = mapped_column(ForeignKey("mdm_resolution_proposals.id"))
    entity_id: Mapped[str | None] = mapped_column(ForeignKey("mdm_resolution_entities.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(32))
    actor_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    before_fields: Mapped[dict] = mapped_column(JSON)
    after_fields: Mapped[dict] = mapped_column(JSON)
    member_record_ids: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


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


# ── LLM Audit tables (Phase L4) ───────────────────────────────────────────────

class LLMRequest(Base):
    """Audit record cho mỗi OpenRouter call."""
    __tablename__ = "llm_requests"
    __table_args__ = (
        Index("ix_llm_requests_request_type", "request_type"),
        Index("ix_llm_requests_created_at", "created_at"),
        Index("ix_llm_requests_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    request_type: Mapped[str] = mapped_column(String(50))
    pair_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    model: Mapped[str] = mapped_column(String(100))
    provider: Mapped[str] = mapped_column(String(50), default="openrouter")
    prompt_version: Mapped[str] = mapped_column(String(50))
    schema_version: Mapped[str] = mapped_column(String(50))
    request_hash: Mapped[str] = mapped_column(String(64))
    response_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(30))
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class LLMSemanticEvidence(Base):
    """LLM semantic fallback evidence — provenance record."""
    __tablename__ = "llm_semantic_evidence"
    __table_args__ = (Index("ix_llm_semantic_evidence_pair_id", "pair_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    pair_id: Mapped[str] = mapped_column(String(100))
    field_name: Mapped[str] = mapped_column(String(50))
    semantic_result: Mapped[str] = mapped_column(String(50))
    mapped_state: Mapped[str] = mapped_column(String(10))
    reason_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    short_reason: Mapped[str | None] = mapped_column(String(240), nullable=True)
    model: Mapped[str] = mapped_column(String(100))
    prompt_version: Mapped[str] = mapped_column(String(50))
    schema_version: Mapped[str] = mapped_column(String(50))
    map_version: Mapped[str] = mapped_column(String(50))
    request_id: Mapped[str] = mapped_column(String(36))
    request_hash: Mapped[str] = mapped_column(String(64))
    response_hash: Mapped[str] = mapped_column(String(64))
    eligible_for_auto_merge: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class LLMExplanationRecord(Base):
    """Persisted explanation cache — pair hoặc group scope."""
    __tablename__ = "llm_explanations"
    __table_args__ = (
        Index("ix_llm_explanations_scope", "scope_type", "scope_id"),
        Index("ix_llm_explanations_signature", "signature_hash"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    scope_type: Mapped[str] = mapped_column(String(30))   # "pair" | "group"
    scope_id: Mapped[str] = mapped_column(String(100))
    explanation_json: Mapped[dict] = mapped_column(JSON)
    signature_hash: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(100))
    prompt_version: Mapped[str] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ResolutionPreview(Base):
    __tablename__ = "mdm_resolution_previews"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    run_id: Mapped[str] = mapped_column(ForeignKey("mdm_resolution_runs.id", ondelete="CASCADE"), index=True)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    snapshot: Mapped[list] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(24), default="OPEN")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ResolutionRequestKey(Base):
    __tablename__ = "mdm_resolution_request_keys"
    __table_args__ = (UniqueConstraint("run_id", "owner_user_id", "key", name="uq_mdm_resolution_request_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    run_id: Mapped[str] = mapped_column(ForeignKey("mdm_resolution_runs.id", ondelete="CASCADE"), index=True)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    key: Mapped[str] = mapped_column(String(100))
    request_hash: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


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


class ResolutionChoiceDraft(Base):
    __tablename__ = "mdm_resolution_choice_drafts"
    __table_args__ = (UniqueConstraint("run_id", "item_kind", "item_id", name="uq_mdm_resolution_choice_item"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    run_id: Mapped[str] = mapped_column(ForeignKey("mdm_resolution_runs.id", ondelete="CASCADE"), index=True)
    item_kind: Mapped[str] = mapped_column(String(16))
    item_id: Mapped[str] = mapped_column(String(64))
    member_hash: Mapped[str] = mapped_column(String(64))
    choices: Mapped[dict] = mapped_column(JSON, default=dict)
    actor_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)


class ResolutionExport(Base):
    __tablename__ = "mdm_resolution_exports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    run_id: Mapped[str] = mapped_column(ForeignKey("mdm_resolution_runs.id", ondelete="CASCADE"), index=True)
    actor_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    csv_bytes: Mapped[bytes] = mapped_column(LargeBinary)
    row_count: Mapped[int] = mapped_column(Integer)
    merged_group_count: Mapped[int] = mapped_column(Integer)
    pending_review_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


