from typing import Literal

from pydantic import BaseModel, Field

Decision = Literal["pending", "accepted", "rejected", "needs_review"]
FIELD_NAMES = ("name", "phone", "email", "address", "tax_id")


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
