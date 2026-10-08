"""Evidence Floor — pair must clear this before grouping (architecture §12).

A pair that fails the floor gets decision=INSUFFICIENT_EVIDENCE and is not
assigned to any group. It is not a NOT_MERGEABLE — it just lacks enough signal
to classify confidently.

§12.1 Strong anchors (configurable):
  - phone EX/NX + VALID + not SHARED
  - email EX/NX + VALID + not SHARED
  - same-type government_id EX + VALID

Supporting evidence (never alone sufficient):
  - DOB EX
  - name HS
  - address HS

W_MIN and min_valid_anchors are configurable per policy domain.
"""

from __future__ import annotations

from dataclasses import dataclass

# Default thresholds — can be overridden by policy config
W_MIN: float = 0.0
MIN_VALID_ANCHORS: int = 1

_STRONG_ANCHOR_STATES = frozenset({"EX", "NX"})
_ANCHOR_FIELDS = ("phone", "email", "government_id")


@dataclass(frozen=True)
class FloorResult:
    passes: bool
    reason: str        # reason code if failed; "" if passed
    valid_anchors: int


def evaluate(comparisons: dict[str, dict], match_weight: float,
             w_min: float = W_MIN,
             min_valid_anchors: int = MIN_VALID_ANCHORS) -> FloorResult:
    """Evaluate whether a pair clears the evidence floor.

    comparisons: {field_name: {"state": str, "provenance": str, ...}}
    """
    valid_anchors = 0

    for field in _ANCHOR_FIELDS:
        state = comparisons.get(field, {}).get("state", "MI")
        if state in _STRONG_ANCHOR_STATES:
            valid_anchors += 1

    if valid_anchors < min_valid_anchors:
        return FloorResult(False, "NO_VALID_IDENTITY_ANCHOR", valid_anchors)

    if match_weight < w_min:
        return FloorResult(False, "WEIGHT_BELOW_FLOOR", valid_anchors)

    return FloorResult(True, "", valid_anchors)
