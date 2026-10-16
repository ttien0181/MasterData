"""Strict M1 metadata contracts; no learned encoder, threshold or structure API."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class ProfileCreate(Contract):
    data_scope: Literal['owner-private'] = 'owner-private'
    version: int = Field(ge=1)
    config: dict | None = None
    advanced_agents: bool = False
    integration: bool = False
    global_lexical: bool = False
    person_recovery: bool = False
    integration_experiment: Literal['E0', 'E1', 'E2', 'E3'] = 'E3'
    fallback_mode: Literal['fail_operation', 'rule_only'] = 'fail_operation'

    @model_validator(mode='after')
    def exclusive_mode(self):
        if self.integration and self.advanced_agents:
            raise ValueError('Choose integration or evidence-only profile')
        if self.global_lexical and not self.integration:
            raise ValueError('Global lexical retrieval requires an integration profile')
        if (self.person_recovery or self.integration_experiment != 'E3') and not self.integration:
            raise ValueError('Retrieval experiments require an integration profile')
        if self.person_recovery and self.integration_experiment == 'E2':
            raise ValueError('PERSON recovery requires rule candidates')
        return self


class ProfileTransition(Contract):
    expected_revision: int = Field(ge=1)


class LearningRequest(Contract):
    dataset_id: str | None = Field(default=None, min_length=1, max_length=36)


class StructurePreviewRequest(Contract):
    request_id: str = Field(min_length=1, max_length=128)
    expected_workflow_revision: int = Field(ge=0)
    expected_decision_revision: int = Field(ge=0)
    expected_cluster_revision: int = Field(ge=1)
    expected_golden_revision: int = Field(ge=0)
    cluster_ids: list[str] = Field(min_length=1, max_length=2)
    partitions: list[list[str]] | None = None
    bridge_pair_ids: list[str] = Field(default_factory=list, max_length=100)
    constraint_id: str | None = Field(default=None, min_length=1, max_length=36)
    reason: str = Field(min_length=1, max_length=500)


class StructureExecuteRequest(Contract):
    request_id: str = Field(min_length=1, max_length=128)
    expected_workflow_revision: int = Field(ge=0)
    expected_decision_revision: int = Field(ge=0)
    expected_cluster_revision: int = Field(ge=1)
    expected_golden_revision: int = Field(ge=0)
    preview_operation_id: str = Field(min_length=1, max_length=36)
    preview_token: str = Field(pattern='^[0-9a-f]{64}$')


class RebuildRequest(Contract):
    request_id: str = Field(min_length=1, max_length=128)
    expected_workflow_revision: int = Field(ge=0)
    expected_decision_revision: int = Field(ge=0)
    profile_id: str = Field(min_length=1, max_length=36)
    workload_plan_hash: str = Field(pattern='^[0-9a-f]{64}$')


class DefaultApply(Contract):
    profile_id: str = Field(min_length=1, max_length=36)
    expected_revision: int = Field(ge=0)


class DatasetRegister(Contract):
    data_scope: Literal['owner-private'] = 'owner-private'
    name: str = Field(min_length=1, max_length=128)
    version: int = Field(ge=1)
    provenance_kind: Literal['SYNTHETIC', 'HUMAN_FEEDBACK', 'EXTERNAL_UNVERIFIED']
    label_schema_version: str = Field(min_length=1, max_length=40)
    manifest_uri: str = Field(min_length=1, max_length=512)
    manifest_hash: str = Field(pattern='^[0-9a-f]{64}$')
    label_snapshot_hash: str = Field(pattern='^[0-9a-f]{64}$')


class EvaluationRegister(Contract):
    dataset_id: str = Field(min_length=1, max_length=36)
    profile_id: str | None = Field(default=None, min_length=1, max_length=36)
    run_id: str | None = Field(default=None, min_length=1, max_length=36)
    protocol_version: str = Field(min_length=1, max_length=40)
    protocol_hash: str = Field(pattern='^[0-9a-f]{64}$')
    report_uri: str = Field(min_length=1, max_length=512)
    report_hash: str = Field(pattern='^[0-9a-f]{64}$')
    prediction_manifest_hash: str = Field(pattern='^[0-9a-f]{64}$')
    evaluated_scope: dict
    label_support: dict
    status: Literal['COMPLETE', 'BLOCKED', 'ERROR']


class ProfileRead(Contract):
    id: str
    owner_user_id: str
    data_scope: str
    version: int
    config: dict
    digest: str
    lifecycle: str
    lifecycle_revision: int
    score_unit: str


class BindingRead(Contract):
    id: str
    run_id: str
    revision: int
    profile_id: str
    profile_digest: str
    binding_kind: str
    parent_binding_id: str | None
    assessment_id: str | None


class FeedbackRead(Contract):
    id: str
    run_id: str
    pair_id: str
    pair_revision: int
    label: str | None
    origin: str
    supersedes_feedback_id: str | None
    feature_snapshot_hash: str
    assessment_id: str | None
    profile_id: str | None
    profile_digest: str | None
    trainability_status: str
    trainability_reason: str
