"""Temporal evidence is unknown unless actual business timestamps are supplied."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class TemporalEvidence:
    status: str
    left_as_of: date | None
    right_as_of: date | None
    reason: str


def compare_temporal(left_as_of: date | None, right_as_of: date | None) -> TemporalEvidence:
    if left_as_of is not None and not isinstance(left_as_of, date):
        raise ValueError("left_as_of must be a date")
    if right_as_of is not None and not isinstance(right_as_of, date):
        raise ValueError("right_as_of must be a date")
    if left_as_of is None or right_as_of is None:
        return TemporalEvidence("unknown", left_as_of, right_as_of, "actual business timestamp unavailable")
    # Different observation dates alone are not evidence of an identity conflict.
    return TemporalEvidence("known_dates", left_as_of, right_as_of,
                            "timestamps recorded; compatibility requires a domain validity interval")
