"""Auto Merge Policy Engine (architecture §17).

Each Semantic Subgroup has a named policy config.  score_pair calls
apply_policy() to decide MATCH_CANDIDATE vs REVIEW.

Policy config fields (§17):
  required       — {field: {states, quality?, allow_shared?}}
  forbidden      — {field: [states]}
  min_match_weight
  allow_llm_evidence  — always False for auto-merge (§25: LLM never enables auto-merge)
  approval_level — STEWARD | SENIOR_STEWARD
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ── Policy definitions ────────────────────────────────────────────────────────

@dataclass
class FieldRequirement:
    states: frozenset[str]
    quality: frozenset[str] = field(default_factory=lambda: frozenset())
    allow_shared: bool = False
    required_tags: frozenset[str] = field(default_factory=lambda: frozenset())


@dataclass
class SubgroupPolicy:
    policy_id: str
    version: int
    required: dict[str, FieldRequirement]
    forbidden: dict[str, frozenset[str]]
    min_match_weight: float
    allow_llm_evidence: bool = False          # §25: LLM never enables auto-merge
    approval_level: str = "STEWARD"


def _req(states: list[str], quality: list[str] | None = None,
         allow_shared: bool = False,
         required_tags: list[str] | None = None) -> FieldRequirement:
    return FieldRequirement(
        states=frozenset(states),
        quality=frozenset(quality or []),
        allow_shared=allow_shared,
        required_tags=frozenset(required_tags or []),
    )


# All policies ship with allow_llm_evidence=False as the safe default (§17, §25).
POLICIES: dict[str, SubgroupPolicy] = {
    # ── Group A ───────────────────────────────────────────────────────────────
    "A1": SubgroupPolicy(
        policy_id="A1", version=4,
        required={
            "phone": _req(["EX", "NX"], quality=["VALID"], allow_shared=False),
            "name":  _req(["EX", "NX", "NA", "HS"]),
        },
        forbidden={"government_id": frozenset(["CF"])},
        min_match_weight=10.5,   # §17 — precision over coverage
        approval_level="STEWARD",
    ),
    "A2": SubgroupPolicy(
        policy_id="A2", version=3,
        required={
            "email": _req(["EX", "NX"], quality=["VALID"], allow_shared=False),
            "name":  _req(["EX", "NX", "NA", "HS"]),
        },
        forbidden={"government_id": frozenset(["CF"])},
        min_match_weight=10.5,   # §17
        approval_level="STEWARD",
    ),
    "A3": SubgroupPolicy(
        # Phone + DOB + compatible name (§18: stricter — given_name must be compatible)
        policy_id="A3", version=5,
        required={
            "phone": _req(["EX", "NX"], quality=["VALID"], allow_shared=False),
            "dob":   _req(["EX"]),
            "name":  _req(["EX", "NX", "NA", "HS", "MS"], required_tags=["GIVEN_NAME_COMPATIBLE"]),
        },
        forbidden={
            "government_id": frozenset(["CF"]),
            "name": frozenset(["CF", "WS"]),     # §18: given_name must be compatible
        },
        min_match_weight=12.0,   # §17 — A3 requires highest confidence
        approval_level="STEWARD",
    ),
    # ── Group C ───────────────────────────────────────────────────────────────
    # C subgroups are always REVIEW — no auto-merge.
    "C1": SubgroupPolicy(
        policy_id="C1", version=1,
        required={"email": _req(["EX", "NX"]), "dob": _req(["EX", "CL"])},
        forbidden={},
        min_match_weight=999.0,   # effectively REVIEW always
    ),
    "C2": SubgroupPolicy(
        policy_id="C2", version=1,
        required={"phone": _req(["EX", "NX"]), "name": _req(["EX", "NX", "NA", "HS"])},
        forbidden={},
        min_match_weight=999.0,
    ),
    "C3": SubgroupPolicy(
        policy_id="C3", version=1,
        required={},
        forbidden={},
        min_match_weight=999.0,
    ),
    "C_MULTI": SubgroupPolicy(
        policy_id="C_MULTI", version=1,
        required={},
        forbidden={},
        min_match_weight=999.0,
    ),
    # ── Group D ───────────────────────────────────────────────────────────────
    # Group D is always REVIEW at MVP (§17.2).
    "D1": SubgroupPolicy(policy_id="D1", version=1, required={}, forbidden={},
                         min_match_weight=999.0, approval_level="SENIOR_STEWARD"),
    "D2": SubgroupPolicy(policy_id="D2", version=1, required={}, forbidden={},
                         min_match_weight=999.0, approval_level="SENIOR_STEWARD"),
    "D3": SubgroupPolicy(policy_id="D3", version=1, required={}, forbidden={},
                         min_match_weight=999.0, approval_level="SENIOR_STEWARD"),
    "D4": SubgroupPolicy(policy_id="D4", version=1, required={}, forbidden={},
                         min_match_weight=999.0, approval_level="SENIOR_STEWARD"),
    "D_MULTI": SubgroupPolicy(policy_id="D_MULTI", version=1, required={}, forbidden={},
                              min_match_weight=999.0, approval_level="SENIOR_STEWARD"),
}


@dataclass
class PolicyResult:
    eligible: bool
    policy_id: str
    policy_version: int
    reason: str         # why eligible or why blocked
    matched_subgroups: list[str]
    decision_subgroup: str


# Deterministic conservative priority per group (§15.3)
_A_PRIORITY = ("A3", "A2", "A1")
_C_PRIORITY = ("C_MULTI", "C1", "C2", "C3")
_D_PRIORITY = ("D_MULTI", "D1", "D2", "D3", "D4")


def _field_passes_requirement(
    field_name: str,
    req: FieldRequirement,
    comparisons: dict[str, dict],
    nf_left: dict,
    nf_right: dict,
) -> bool:
    state = comparisons.get(field_name, {}).get("state", "MI")
    if state not in req.states:
        return False
    if not req.allow_shared and state in {"SH"}:
        return False
    if req.quality:
        qa = nf_left.get("_field_quality", {}).get(field_name, "UNKNOWN")
        qb = nf_right.get("_field_quality", {}).get(field_name, "UNKNOWN")
        # Only enforce when quality metadata is present; UNKNOWN means "not checked"
        if qa not in req.quality and qa not in {"UNKNOWN"}:
            return False
        if qb not in req.quality and qb not in {"UNKNOWN"}:
            return False
    if req.required_tags:
        tags = set(comparisons.get(field_name, {}).get("normalization_tags", []))
        if not req.required_tags.issubset(tags):
            return False
    return True


def _policy_eligible(
    policy: SubgroupPolicy,
    comparisons: dict[str, dict],
    match_weight: float,
    nf_left: dict,
    nf_right: dict,
) -> tuple[bool, str]:
    """Returns (eligible, reason)."""
    if match_weight < policy.min_match_weight:
        return False, f"WEIGHT_BELOW_{policy.policy_id}_MIN"
    for field_name, req in policy.required.items():
        if not _field_passes_requirement(field_name, req, comparisons, nf_left, nf_right):
            return False, f"REQUIRED_FIELD_NOT_MET_{field_name.upper()}"
    for field_name, forbidden_states in policy.forbidden.items():
        state = comparisons.get(field_name, {}).get("state", "MI")
        if state in forbidden_states:
            return False, f"FORBIDDEN_STATE_{field_name.upper()}_{state}"
    return True, "ELIGIBLE"


def apply_policy(
    primary_group: str,
    comparisons: dict[str, dict],
    match_weight: float,
    nf_left: dict,
    nf_right: dict,
) -> PolicyResult:
    """Evaluate all subgroup policies for a pair and pick the most conservative eligible one."""
    if primary_group == "A":
        priority = _A_PRIORITY
    elif primary_group == "C":
        priority = _C_PRIORITY
    elif primary_group == "D":
        priority = _D_PRIORITY
    else:
        return PolicyResult(False, "", 0, "UNKNOWN_GROUP", [], "")

    matched: list[str] = []
    for sg in priority:
        pol = POLICIES.get(sg)
        if pol is None:
            continue
        eligible, _ = _policy_eligible(pol, comparisons, match_weight, nf_left, nf_right)
        if eligible:
            matched.append(sg)

    if not matched:
        return PolicyResult(False, "", 0, "NO_ELIGIBLE_SUBGROUP", [], "")

    # decision_subgroup = first in priority order (most conservative / most specific)
    decision_sg = matched[0]
    pol = POLICIES[decision_sg]
    return PolicyResult(
        eligible=True,
        policy_id=pol.policy_id,
        policy_version=pol.version,
        reason="ELIGIBLE",
        matched_subgroups=list(matched),
        decision_subgroup=decision_sg,
    )
