"""Additive session state; legacy engine serving rows remain untouched."""
from datetime import datetime
from uuid import uuid4

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, event, text
from sqlalchemy.orm import Mapped, mapped_column

from masterone.db.base import Base, now_utc


class WorkflowOperation(Base):
    __tablename__ = 'mdm_workflow_operations'
    __table_args__ = (UniqueConstraint('run_id', 'request_id', name='uq_workflow_request'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    run_id: Mapped[str] = mapped_column(ForeignKey('mdm_resolution_runs.id', ondelete='CASCADE'), index=True)
    request_id: Mapped[str] = mapped_column(String(128))
    operation_type: Mapped[str] = mapped_column(String(32))
    payload_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(24), default='QUEUED')
    stage: Mapped[str] = mapped_column(String(40), default='queued')
    progress: Mapped[dict] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    artifact_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    actor_user_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class WorkflowArtifact(Base):
    __tablename__ = 'mdm_workflow_artifacts'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    run_id: Mapped[str] = mapped_column(ForeignKey('mdm_resolution_runs.id', ondelete='CASCADE'), index=True)
    phase: Mapped[str] = mapped_column(String(24))
    content_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


@event.listens_for(WorkflowArtifact, 'before_update')
def immutable_artifact(*_):
    raise ValueError('WORKFLOW_ARTIFACT_IMMUTABLE')


class WorkflowPair(Base):
    __tablename__ = 'mdm_workflow_pairs'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey('mdm_resolution_runs.id', ondelete='CASCADE'), index=True)
    baseline: Mapped[dict] = mapped_column(JSON)
    assessment: Mapped[dict | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    assessment_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    advice: Mapped[dict | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    decision: Mapped[str] = mapped_column(String(24), default='UNDECIDED')
    review_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)


class WorkflowAssessment(Base):
    __tablename__ = 'mdm_workflow_assessments'
    __table_args__ = (Index('uq_workflow_active_assessment', 'run_id', unique=True,
                           postgresql_where=text('active'), sqlite_where=text('active = 1')),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    run_id: Mapped[str] = mapped_column(ForeignKey('mdm_resolution_runs.id', ondelete='CASCADE'), index=True)
    artifact_id: Mapped[str] = mapped_column(ForeignKey('mdm_workflow_artifacts.id'))
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    configuration: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class WorkflowGeneration(Base):
    __tablename__ = 'mdm_workflow_generations'
    __table_args__ = (UniqueConstraint('run_id', 'revision', name='uq_workflow_generation'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    run_id: Mapped[str] = mapped_column(ForeignKey('mdm_resolution_runs.id', ondelete='CASCADE'), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    artifact_id: Mapped[str] = mapped_column(ForeignKey('mdm_workflow_artifacts.id'))
    result_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    finalized_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class WorkflowCluster(Base):
    __tablename__ = 'mdm_workflow_clusters'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    generation_id: Mapped[str] = mapped_column(ForeignKey('mdm_workflow_generations.id', ondelete='CASCADE'), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    golden: Mapped[dict] = mapped_column(JSON)
    selections: Mapped[dict] = mapped_column(JSON, default=dict)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    confirmed: Mapped[bool] = mapped_column(default=False)


class WorkflowMember(Base):
    __tablename__ = 'mdm_workflow_members'
    __table_args__ = (UniqueConstraint('generation_id', 'record_id', name='uq_workflow_membership'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    generation_id: Mapped[str] = mapped_column(ForeignKey('mdm_workflow_generations.id', ondelete='CASCADE'), index=True)
    cluster_id: Mapped[str] = mapped_column(ForeignKey('mdm_workflow_clusters.id', ondelete='CASCADE'), index=True)
    record_id: Mapped[str] = mapped_column(ForeignKey('mdm_resolution_records.id'))


class WorkflowConflict(Base):
    __tablename__ = 'mdm_workflow_conflicts'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    run_id: Mapped[str] = mapped_column(ForeignKey('mdm_resolution_runs.id', ondelete='CASCADE'), index=True)
    operation_id: Mapped[str] = mapped_column(ForeignKey('mdm_workflow_operations.id'))
    pair_id: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)
    resolved: Mapped[bool] = mapped_column(default=False)
