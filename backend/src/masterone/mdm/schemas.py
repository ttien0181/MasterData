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
