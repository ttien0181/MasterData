"""Gold labels are explicit and are never part of scorer-facing records."""
from __future__ import annotations

from enum import Enum


class PairLabel(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    UNKNOWN = "unknown"


def parse_pair_label(value: object) -> PairLabel:
    if value is None:
        return PairLabel.UNKNOWN
    text = str(value).strip().lower()
    if text in {"", "unknown", "unk", "null", "none", "na", "n/a"}:
        return PairLabel.UNKNOWN
    if text in {"1", "true", "positive", "match"}:
        return PairLabel.POSITIVE
    if text in {"0", "false", "negative", "non-match", "nonmatch"}:
        return PairLabel.NEGATIVE
    raise ValueError(f"invalid pair label {value!r}; expected 0, 1, or unknown")
