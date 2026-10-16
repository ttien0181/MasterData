"""M1 owner-scoped registries; no matching implementation or legacy backfill."""
from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from masterone.db.base import Base, now_utc


class OwnedIdentity:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    owner_user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='RESTRICT'))
    data_scope: Mapped[str] = mapped_column(String(128))
    creator_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='RESTRICT'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


def scoped_identity(name):
    return (UniqueConstraint('id', 'owner_user_id', 'data_scope', name='uq_' + name + '_scope'),
            CheckConstraint("length(data_scope) > 0", name='ck_' + name + '_scope'))


class MatchingProfile(OwnedIdentity, Base):
    __tablename__ = 'mdm_matching_profiles'
    __table_args__ = (*scoped_identity('matching_profile'),
        UniqueConstraint('owner_user_id', 'data_scope', 'version', name='uq_matching_profile_version'),
        CheckConstraint('version >= 1 AND lifecycle_revision >= 1', name='ck_matching_profile_revision'),
        CheckConstraint("lifecycle IN ('CANDIDATE','VALIDATED','ACTIVE','RETIRED','REJECTED')", name='ck_matching_profile_lifecycle'),
        CheckConstraint('length(config_digest) = 64', name='ck_matching_profile_digest'),
        ForeignKeyConstraint(['training_dataset_id', 'owner_user_id', 'data_scope'],
            ['mdm_labeled_datasets.id', 'mdm_labeled_datasets.owner_user_id', 'mdm_labeled_datasets.data_scope'],
            name='fk_matching_profile_dataset', ondelete='RESTRICT'),
        ForeignKeyConstraint(['evaluation_report_id', 'owner_user_id', 'data_scope'],
            ['mdm_evaluation_reports.id', 'mdm_evaluation_reports.owner_user_id', 'mdm_evaluation_reports.data_scope'],
            name='fk_matching_profile_report', ondelete='RESTRICT', use_alter=True),
        Index('ix_matching_profile_owner_lifecycle', 'owner_user_id', 'data_scope', 'lifecycle'),
        Index('ix_matching_profile_digest', 'config_digest'))
    version: Mapped[int] = mapped_column(Integer)
    config_schema_version: Mapped[str] = mapped_column(String(40))
    config_json: Mapped[dict] = mapped_column(JSON)
    config_digest: Mapped[str] = mapped_column(String(64))
    score_unit: Mapped[str] = mapped_column(String(40))
    encoder_identity: Mapped[dict | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    feature_schema_identity: Mapped[str] = mapped_column(String(64))
    training_dataset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    evaluation_report_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lifecycle: Mapped[str] = mapped_column(String(24), default='CANDIDATE')
    lifecycle_revision: Mapped[int] = mapped_column(Integer, default=1)


class MatchingProfileDefault(Base):
    __tablename__ = 'mdm_matching_profile_defaults'
    __table_args__ = (ForeignKeyConstraint(['profile_id', 'owner_user_id', 'data_scope'],
        ['mdm_matching_profiles.id', 'mdm_matching_profiles.owner_user_id', 'mdm_matching_profiles.data_scope'],
        name='fk_matching_default_profile', ondelete='RESTRICT'),
        CheckConstraint('revision >= 1', name='ck_matching_default_revision'))
    owner_user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='RESTRICT'), primary_key=True)
    data_scope: Mapped[str] = mapped_column(String(128), primary_key=True)
    profile_id: Mapped[str] = mapped_column(String(36))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    changed_by: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='RESTRICT'))
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class MatchingBinding(OwnedIdentity, Base):
    __tablename__ = 'mdm_matching_bindings'
    __table_args__ = (*scoped_identity('matching_binding'),
        UniqueConstraint('run_id', 'revision', name='uq_matching_binding_revision'),
        UniqueConstraint('id', 'run_id', name='uq_matching_binding_run'),
        CheckConstraint('revision >= 1', name='ck_matching_binding_revision'),
        CheckConstraint("binding_kind IN ('CONFIG_PINNED','PREPARED','ASSESSED')", name='ck_matching_binding_kind'),
        CheckConstraint('length(profile_digest) = 64', name='ck_matching_binding_digest'),
        ForeignKeyConstraint(['run_id', 'owner_user_id'], ['mdm_resolution_runs.id', 'mdm_resolution_runs.owner_user_id'], name='fk_matching_binding_owner', ondelete='RESTRICT'),
        ForeignKeyConstraint(['profile_id', 'owner_user_id', 'data_scope'], ['mdm_matching_profiles.id', 'mdm_matching_profiles.owner_user_id', 'mdm_matching_profiles.data_scope'], name='fk_matching_binding_profile', ondelete='RESTRICT'),
        ForeignKeyConstraint(['parent_binding_id', 'run_id'], ['mdm_matching_bindings.id', 'mdm_matching_bindings.run_id'], name='fk_matching_binding_parent', ondelete='RESTRICT'),
        ForeignKeyConstraint(['originating_operation_id', 'run_id'], ['mdm_workflow_operations.id', 'mdm_workflow_operations.run_id'], name='fk_matching_binding_operation', ondelete='RESTRICT'),
        ForeignKeyConstraint(['prepare_artifact_id', 'run_id'], ['mdm_workflow_artifacts.id', 'mdm_workflow_artifacts.run_id'], name='fk_matching_binding_prepare', ondelete='RESTRICT'),
        ForeignKeyConstraint(['candidate_artifact_id', 'run_id'], ['mdm_workflow_artifacts.id', 'mdm_workflow_artifacts.run_id'], name='fk_matching_binding_candidates', ondelete='RESTRICT'),
        ForeignKeyConstraint(['assessment_id', 'run_id'], ['mdm_workflow_assessments.id', 'mdm_workflow_assessments.run_id'], name='fk_matching_binding_assessment', ondelete='RESTRICT'),
        Index('ix_matching_binding_profile', 'profile_id'))
    run_id: Mapped[str] = mapped_column(String(36))
    revision: Mapped[int] = mapped_column(Integer)
    profile_id: Mapped[str] = mapped_column(String(36))
    profile_digest: Mapped[str] = mapped_column(String(64))
    binding_kind: Mapped[str] = mapped_column(String(24))
    parent_binding_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    originating_operation_id: Mapped[str] = mapped_column(String(36))
    prepare_artifact_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    candidate_artifact_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    assessment_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    input_snapshot_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    config_identity: Mapped[str] = mapped_column(String(64))


class StewardFeedback(OwnedIdentity, Base):
    __tablename__ = 'mdm_steward_feedback'
    __table_args__ = (*scoped_identity('steward_feedback'),
        UniqueConstraint('workflow_pair_id', 'pair_revision', name='uq_steward_feedback_pair_revision'),
        UniqueConstraint('operation_id', 'workflow_pair_id', 'pair_revision', name='uq_steward_feedback_operation'),
        UniqueConstraint('supersedes_feedback_id', name='uq_steward_feedback_successor'),
        UniqueConstraint('id', 'workflow_pair_id', name='uq_steward_feedback_pair'),
        CheckConstraint('pair_revision >= 1', name='ck_steward_feedback_revision'),
        CheckConstraint("origin IN ('HUMAN_DIRECT','HUMAN_BULK_DIRECT')", name='ck_steward_feedback_origin'),
        CheckConstraint("(human_direct_label IS NOT NULL AND human_decision = 'ACCEPTED' AND human_direct_label = 'MATCH') OR (human_direct_label IS NOT NULL AND human_decision = 'NON_MATCH' AND human_direct_label = 'NON_MATCH') OR (human_decision IN ('REJECTED','DEFERRED') AND human_direct_label IS NULL)", name='ck_steward_feedback_label'),
        CheckConstraint("trainability_status IN ('TRAINABLE','NOT_TRAINABLE')", name='ck_steward_feedback_trainability'),
        CheckConstraint("trainability_status != 'TRAINABLE' OR human_direct_label IS NOT NULL", name='ck_steward_feedback_trainable_label'),
        CheckConstraint('supersedes_feedback_id IS NULL OR supersedes_feedback_id != id', name='ck_steward_feedback_not_self'),
        ForeignKeyConstraint(['run_id', 'owner_user_id'], ['mdm_resolution_runs.id', 'mdm_resolution_runs.owner_user_id'], name='fk_steward_feedback_owner', ondelete='RESTRICT'),
        ForeignKeyConstraint(['workflow_pair_id', 'run_id'], ['mdm_workflow_pairs.id', 'mdm_workflow_pairs.run_id'], name='fk_steward_feedback_pair', ondelete='RESTRICT'),
        ForeignKeyConstraint(['operation_id', 'run_id'], ['mdm_workflow_operations.id', 'mdm_workflow_operations.run_id'], name='fk_steward_feedback_operation', ondelete='RESTRICT'),
        ForeignKeyConstraint(['assessment_id', 'run_id'], ['mdm_workflow_assessments.id', 'mdm_workflow_assessments.run_id'], name='fk_steward_feedback_assessment', ondelete='RESTRICT'),
        ForeignKeyConstraint(['binding_id', 'run_id'], ['mdm_matching_bindings.id', 'mdm_matching_bindings.run_id'], name='fk_steward_feedback_binding', ondelete='RESTRICT'),
        ForeignKeyConstraint(['profile_id', 'owner_user_id', 'data_scope'], ['mdm_matching_profiles.id', 'mdm_matching_profiles.owner_user_id', 'mdm_matching_profiles.data_scope'], name='fk_steward_feedback_profile', ondelete='RESTRICT'),
        ForeignKeyConstraint(['supersedes_feedback_id', 'workflow_pair_id'], ['mdm_steward_feedback.id', 'mdm_steward_feedback.workflow_pair_id'], name='fk_steward_feedback_supersedes', ondelete='RESTRICT'),
        Index('ix_steward_feedback_owner_run_time', 'owner_user_id', 'run_id', 'reviewed_at'),
        Index('ix_steward_feedback_trainability', 'owner_user_id', 'trainability_status'))
    run_id: Mapped[str] = mapped_column(String(36))
    workflow_pair_id: Mapped[str] = mapped_column(String(64))
    pair_revision: Mapped[int] = mapped_column(Integer)
    operation_id: Mapped[str] = mapped_column(String(36))
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='RESTRICT'))
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    origin: Mapped[str] = mapped_column(String(24))
    human_decision: Mapped[str] = mapped_column(String(24))
    human_direct_label: Mapped[str | None] = mapped_column(String(16), nullable=True)
    supersedes_feedback_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    feature_snapshot_json: Mapped[dict] = mapped_column(JSON)
    feature_snapshot_hash: Mapped[str] = mapped_column(String(64))
    evidence_references: Mapped[dict] = mapped_column(JSON)
    assessment_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    binding_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    profile_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    profile_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    feature_schema_identity: Mapped[str] = mapped_column(String(64))
    trainability_status: Mapped[str] = mapped_column(String(24))
    trainability_reason: Mapped[str] = mapped_column(String(128))


class LabeledDataset(OwnedIdentity, Base):
    __tablename__ = 'mdm_labeled_datasets'
    __table_args__ = (*scoped_identity('labeled_dataset'),
        UniqueConstraint('owner_user_id', 'data_scope', 'name', 'version', name='uq_labeled_dataset_version'),
        CheckConstraint('version >= 1', name='ck_labeled_dataset_version'))
    name: Mapped[str] = mapped_column(String(128))
    version: Mapped[int] = mapped_column(Integer)
    provenance_kind: Mapped[str] = mapped_column(String(40))
    label_schema_version: Mapped[str] = mapped_column(String(40))
    manifest_uri: Mapped[str] = mapped_column(String(512))
    manifest_hash: Mapped[str] = mapped_column(String(64))
    label_snapshot_hash: Mapped[str] = mapped_column(String(64))
    split_manifest_uri: Mapped[str | None] = mapped_column(String(512), nullable=True)
    split_manifest_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    truth_verification_status: Mapped[str] = mapped_column(String(32))
    trainability_status: Mapped[str] = mapped_column(String(24))
    trainability_reasons: Mapped[list] = mapped_column(JSON)


class EvaluationReport(OwnedIdentity, Base):
    __tablename__ = 'mdm_evaluation_reports'
    __table_args__ = (*scoped_identity('evaluation_report'),
        ForeignKeyConstraint(['dataset_id', 'owner_user_id', 'data_scope'], ['mdm_labeled_datasets.id', 'mdm_labeled_datasets.owner_user_id', 'mdm_labeled_datasets.data_scope'], name='fk_evaluation_dataset', ondelete='RESTRICT'),
        ForeignKeyConstraint(['profile_id', 'owner_user_id', 'data_scope'], ['mdm_matching_profiles.id', 'mdm_matching_profiles.owner_user_id', 'mdm_matching_profiles.data_scope'], name='fk_evaluation_profile', ondelete='RESTRICT'),
        ForeignKeyConstraint(['run_id', 'owner_user_id'], ['mdm_resolution_runs.id', 'mdm_resolution_runs.owner_user_id'], name='fk_evaluation_owner', ondelete='RESTRICT'),
        ForeignKeyConstraint(['binding_id', 'run_id'], ['mdm_matching_bindings.id', 'mdm_matching_bindings.run_id'], name='fk_evaluation_binding', ondelete='RESTRICT'),
        ForeignKeyConstraint(['operation_id', 'run_id'], ['mdm_workflow_operations.id', 'mdm_workflow_operations.run_id'], name='fk_evaluation_operation', ondelete='RESTRICT'),
        Index('ix_evaluation_owner_time', 'owner_user_id', 'data_scope', 'created_at'),
        CheckConstraint("status IN ('COMPLETE','BLOCKED','ERROR')", name='ck_evaluation_status'))
    dataset_id: Mapped[str] = mapped_column(String(36))
    profile_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    profile_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    binding_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    operation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    protocol_version: Mapped[str] = mapped_column(String(40))
    protocol_hash: Mapped[str] = mapped_column(String(64))
    report_uri: Mapped[str] = mapped_column(String(512))
    report_hash: Mapped[str] = mapped_column(String(64))
    prediction_manifest_hash: Mapped[str] = mapped_column(String(64))
    evaluated_scope: Mapped[dict] = mapped_column(JSON)
    label_support: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(24))


class StructureConstraint(OwnedIdentity, Base):
    __tablename__ = 'mdm_structure_constraints'
    __table_args__ = (*scoped_identity('structure_constraint'),
        ForeignKeyConstraint(['run_id', 'owner_user_id'], ['mdm_resolution_runs.id', 'mdm_resolution_runs.owner_user_id'], name='fk_structure_owner', ondelete='RESTRICT'),
        ForeignKeyConstraint(['originating_operation_id', 'run_id'], ['mdm_workflow_operations.id', 'mdm_workflow_operations.run_id'], name='fk_structure_operation', ondelete='RESTRICT'),
        CheckConstraint('effective_revision >= 1', name='ck_structure_revision'),
        CheckConstraint("kind IN ('SEPARATION','REMERGE_RELEASE')", name='ck_structure_kind'),
        CheckConstraint("status IN ('PREPARED','RELEASED')", name='ck_structure_status'),
        Index('ix_structure_run_status', 'run_id', 'data_scope', 'status'))
    run_id: Mapped[str] = mapped_column(String(36))
    originating_operation_id: Mapped[str] = mapped_column(String(36))
    kind: Mapped[str] = mapped_column(String(24))
    partition_manifest_uri: Mapped[str] = mapped_column(String(512))
    partition_manifest_hash: Mapped[str] = mapped_column(String(64))
    evidence_ref: Mapped[dict] = mapped_column(JSON)
    effective_revision: Mapped[int] = mapped_column(Integer)
    supersedes_id: Mapped[str | None] = mapped_column(ForeignKey('mdm_structure_constraints.id', ondelete='RESTRICT'), nullable=True)
    status: Mapped[str] = mapped_column(String(24))


FOUNDATION_MODELS = (LabeledDataset, MatchingProfile, MatchingProfileDefault,
                     MatchingBinding, StewardFeedback, EvaluationReport, StructureConstraint)

from masterone.mdm.matching_foundation_guards_v1 import register_guards  # noqa: E402

register_guards([model.__table__ for model in FOUNDATION_MODELS])
