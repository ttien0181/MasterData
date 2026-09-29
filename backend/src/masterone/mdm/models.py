from datetime import datetime
from uuid import uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, String, UniqueConstraint
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


