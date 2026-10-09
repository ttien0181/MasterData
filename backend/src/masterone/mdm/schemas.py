from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Decision = Literal["pending", "accepted", "rejected", "needs_review"]
FIELD_NAMES = ("name", "phone", "email", "address", "tax_id")


class ResolutionTaskRead(BaseModel):
    task_id: str
    run_id: str
    status: str
    phase: str
    processed_count: int
    total_count: int
    error_code: str | None
    updated_at: datetime


class ResolutionCountsRead(BaseModel):
    # Keep the public keys; their units differ and must not be summed together.
    auto: int  # staged merge proposals, not already merged records
    rejected: int  # pairs
    review: int  # grouped review items + manual proposals
    review_cases: int
    review_pairs: int = 0
    manual: int
    hard_blocked: int  # pairs
    insufficient: int  # pairs
    group_a: int  # pairs
    group_c: int  # pairs
    group_d: int  # pairs
    separate: int  # estimated source records kept separate


class ResolutionOverviewRead(BaseModel):
    run_id: str
    status: str
    phase: str
    processed_count: int
    total_count: int
    record_count: int
    file_count: int
    source_counts: dict[str, int]
    history_count: int
    candidate_count: int
    estimated_entity_count: int
    duplicate_record_count: int
    eligible_merge_group_count: int
    counts: ResolutionCountsRead
    # Additive fields: restored runs can recover task/error without localStorage.
    task: ResolutionTaskRead | None = None


def task_read(task) -> ResolutionTaskRead:
    return ResolutionTaskRead(task_id=task.id, run_id=task.run_id, status=task.status,
                              phase=task.phase, processed_count=task.processed_count,
                              total_count=task.total_count, error_code=task.error_code,
                              updated_at=task.updated_at)


class FieldChoice(BaseModel):
    value: str
    source_record_id: str


class GoldenRecordRequest(BaseModel):
    fields: dict[str, FieldChoice]


class DecisionRequest(BaseModel):
    decision: Literal["accepted", "rejected", "needs_review"]
    note: str | None = Field(default=None, max_length=1000)


class ResolutionPairDecision(BaseModel):
    decision: Literal["ACCEPTED", "REJECTED", "DEFERRED"]
    expected_revision: int = Field(ge=1)


class ReviewCaseApproval(BaseModel):
    expected_revision: int = Field(ge=1)
    accepted_pair_ids: list[str] = Field(default_factory=list, max_length=1000)


class DeferredProposalApproval(BaseModel):
    expected_revision: int = Field(ge=1)


class ResolutionPreviewRequest(BaseModel):
    proposal_ids: list[str] | None = Field(default=None, min_length=1, max_length=100)
    selection_scope: Literal["all_eligible"] | None = None
    bucket: Literal["auto"] = "auto"
    filters: dict[str, str] = Field(default_factory=dict)


class ResolutionConfirmRequest(BaseModel):
    preview_token: str
    idempotency_key: str = Field(min_length=1, max_length=100)
    choices: dict[str, dict[str, str]] = Field(default_factory=dict)


class ResolutionCancelRequest(BaseModel):
    preview_token: str
