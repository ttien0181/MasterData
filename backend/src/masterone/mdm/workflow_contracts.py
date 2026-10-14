from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

TRANSITIONS = {('DATA_PREPARING', 'PREPARE'): 'DATA_PREPARING',
               ('DATA_READY', 'DECIDE'): 'DECIDE_RUNNING',
               ('DECIDE_REVIEW', 'BULK_DECISIONS'): 'DECIDE_REVIEW',
               ('DECIDE_REVIEW', 'CLUSTER'): 'CLUSTER_QUEUED',
               ('DECIDE_REVIEW', 'ASSESS_PAIRS'): 'DECIDE_REVIEW',
               ('DECIDE_REVIEW', 'PAIR_ADVICE'): 'DECIDE_REVIEW'}
TRANSITIONS[('DECIDE_REVIEW', 'CLUSTER_PREVIEW')] = 'DECIDE_REVIEW'
TRANSITIONS[('DECIDE_REVIEW', 'AUTOMATION_SHADOW')] = 'DECIDE_REVIEW'
TRANSITIONS[('DECIDE_REVIEW', 'AUTOMATION_APPLY')] = 'DECIDE_REVIEW'
for _state in ('DECIDE_REVIEW', 'GOLDEN_REVIEW'):
    TRANSITIONS[(_state, 'GROUP_ANNOTATIONS')] = _state


def transition(state, operation_type):
    try:
        return TRANSITIONS[state, operation_type]
    except KeyError:
        raise ValueError('WORKFLOW_INVALID_STATE') from None


class Mutation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str = Field(min_length=1, max_length=128)
    expected_workflow_revision: int = Field(ge=0)
    expected_assessment_id: str | None = None


class DecisionRequest(Mutation):
    expected_pair_revision: int = Field(ge=0)
    decision: Literal['merge', 'no_merge', 'deny', 'defer']
    reason: str | None = Field(default=None, max_length=500)


class AmendDecisionRequest(DecisionRequest):
    reason: str = Field(min_length=1, max_length=500)


class AssessmentRequest(Mutation):
    model_scope: Literal['synthetic-benchmark'] | None = None


class PairAdviceRequest(Mutation):
    pair_ids: list[str] | None = Field(default=None, min_length=1, max_length=100)
    limit: int = Field(default=100, ge=1, le=100)


class GroupAnnotationRequest(Mutation):
    expected_cluster_revision: int = Field(ge=1)


class PairFilter(BaseModel):
    model_config = ConfigDict(extra='forbid')
    group: str | None = None
    subgroup: str | None = None
    resolution: str | None = None


class BulkScope(BaseModel):
    model_config = ConfigDict(extra='forbid')
    pair_ids: list[str] | None = Field(default=None, min_length=1, max_length=1000)
    filter: PairFilter | None = None
    excluded_pair_ids: list[str] = Field(default_factory=list, max_length=1000)


class BulkRequest(Mutation):
    expected_decision_revision: int = Field(ge=0)
    decision: Literal['merge', 'no_merge', 'deny', 'defer']
    scope: BulkScope
    auto_only: bool = False
    preview_token: str | None = Field(default=None, min_length=64, max_length=64)
    reason: str | None = Field(default=None, max_length=500)
    review_group_record_ids: list[str] | None = Field(default=None, min_length=2, max_length=20)
    review_group_token: str | None = Field(default=None, min_length=64, max_length=64)


class FinalizeRequest(Mutation):
    expected_decision_revision: int = Field(ge=0)
    allow_partial: bool = False
    preview_operation_id: str | None = Field(default=None, min_length=1, max_length=36)
    preview_token: str | None = Field(default=None, min_length=64, max_length=64)


class AutomationShadowRequest(Mutation):
    expected_decision_revision: int = Field(ge=0)


class AutomationApplyRequest(AutomationShadowRequest):
    preview_operation_id: str = Field(min_length=1,max_length=36)
    preview_token: str = Field(min_length=64,max_length=64)


class GroupApprovalRequest(Mutation):
    expected_decision_revision: int = Field(ge=0)
    record_ids: list[str] = Field(min_length=2, max_length=100)
    reason: str = Field(min_length=1, max_length=500)
    fingerprint: str | None = Field(default=None, min_length=64, max_length=64)
    preview_operation_id: str | None = Field(default=None, min_length=1, max_length=36)


class Selection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    selection_mode: Literal['SOURCE_CHOICE', 'MANUAL_OVERRIDE', 'DISCARD_PENDING']
    source_record_id: str | None = None
    value: Any = None
    reason: str | None = Field(default=None, max_length=500)


class GoldenRevisionRequest(Mutation):
    expected_cluster_revision: int = Field(ge=0)
    expected_golden_revision: int = Field(ge=0)


class GoldenRequest(GoldenRevisionRequest):
    selections: dict[str, Selection]


class AutoRequest(GoldenRevisionRequest):
    cluster_ids: list[str] = Field(min_length=1, max_length=100)
    reset_manual: bool = False


class ClusterScopeRequest(GoldenRevisionRequest):
    cluster_ids: list[str] | None = Field(default=None, min_length=1, max_length=1000)
    excluded_cluster_ids: list[str] = Field(default_factory=list, max_length=1000)


class ResolveRequest(ClusterScopeRequest):
    expected_decision_revision: int = Field(ge=0)


class RepairRequest(ResolveRequest):
    operation_id: str = Field(min_length=1, max_length=36)


class FinalizeGoldenRequest(Mutation):
    expected_golden_revision: int = Field(ge=0)


class ReopenRequest(Mutation):
    reason: str = Field(min_length=1, max_length=500)


class OperationRead(BaseModel):
    operation_id: str
    run_id: str
    operation_type: str
    status: str
    stage: str
    progress: dict
    error_code: str | None
    result: dict


class WorkflowRead(BaseModel):
    run_id: str
    workflow_version: int
    workflow_state: str
    workflow_revision: int
    decision_revision: int
    cluster_revision: int
    golden_revision: int
    assessment_id: str | None = None
    advice_revision: str | None = None
    annotation_revision: str | None = None
    counts: dict
    allowed_actions: list[str]
    blockers: list[dict]
    active_operation: OperationRead | None
    retryable_operation: OperationRead | None = None
    cluster_result: dict | None = None
    latest_cluster_operation: OperationRead | None = None
    latest_automation_operation: OperationRead | None = None
    automation_stopped: bool = False
