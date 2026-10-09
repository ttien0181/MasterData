"""Auto Merge Policy Engine (architecture §17).

Each Semantic Subgroup has a named policy config.  score_pair calls
apply_policy() to decide MERGEABLE vs REVIEW.

Policy config fields (§17):
  required       — {field: {states, quality?, allow_shared?}}
  forbidden      — {field: [states]}
  min_match_weight
  allow_llm_evidence  — always False for auto-merge (§25: LLM never enables auto-merge)
  approval_level — STEWARD | SENIOR_STEWARD

YAML loading (TASK-4.1):
  Policies can be loaded from a YAML file via load_policy_yaml(path).
  get_policies() returns cached YAML-loaded policies, falling back to
  the hardcoded POLICIES dict when the YAML file is unavailable.
"""

from __future__ import annotations

import functools
import pathlib
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


_DEFAULT_YAML_PATH = (
    pathlib.Path(__file__).parent.parent / "config" / "policy_person_v3.yaml"
)

# policy_version string included in every pair output for audit traceability
POLICY_FILE_VERSION: str = "person-v3"


def load_policy_yaml(path: pathlib.Path | str) -> dict[str, SubgroupPolicy]:
    """Parse a YAML policy file into a SubgroupPolicy dict.

    Returns an empty dict (not raises) if PyYAML is missing or the file
    cannot be read — callers fall back to the hardcoded POLICIES.
    """
    try:
        import yaml  # type: ignore[import]
    except ImportError:
        return {}

    path = pathlib.Path(path)
    if not path.exists():
        return {}

    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)

    if not isinstance(raw, dict) or "subgroups" not in raw:
        return {}

    result: dict[str, SubgroupPolicy] = {}
    for sg_id, cfg in raw["subgroups"].items():
        required: dict[str, FieldRequirement] = {}
        for fname, fcfg in (cfg.get("required") or {}).items():
            if not isinstance(fcfg, dict):
                fcfg = {}
            required[fname] = FieldRequirement(
                states=frozenset(fcfg.get("states") or []),
                quality=frozenset(fcfg.get("quality") or []),
                allow_shared=bool(fcfg.get("allow_shared", False)),
                required_tags=frozenset(fcfg.get("required_tags") or []),
            )
        forbidden: dict[str, frozenset[str]] = {
            fname: frozenset(fstates)
            for fname, fstates in (cfg.get("forbidden") or {}).items()
        }
        result[sg_id] = SubgroupPolicy(
            policy_id=sg_id,
            version=int(cfg.get("version", 1)),
            required=required,
            forbidden=forbidden,
            min_match_weight=float(cfg.get("min_match_weight", 999.0)),
            allow_llm_evidence=bool(cfg.get("allow_llm_evidence", False)),
            approval_level=str(cfg.get("approval_level", "STEWARD")),
        )
    return result


@functools.lru_cache(maxsize=1)
def _load_yaml_cached() -> dict[str, SubgroupPolicy]:
    return load_policy_yaml(_DEFAULT_YAML_PATH)


def get_policies() -> dict[str, SubgroupPolicy]:
    """Return policies: YAML-loaded (cached) if available, else hardcoded POLICIES."""
    yaml_policies = _load_yaml_cached()
    return yaml_policies if yaml_policies else POLICIES


@dataclass
class PolicyResult:
    eligible: bool
    policy_id: str
    policy_version: int
    reason: str         # why eligible or why blocked
    matched_subgroups: list[str]
    decision_subgroup: str
    policy_file_version: str = POLICY_FILE_VERSION


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


@dataclass
class SimulateResult:
    total_pairs: int
    changed: int
    review_to_merge: int          # REVIEW → MERGEABLE
    merge_to_review: int          # MERGEABLE → REVIEW
    unchanged_merge: int
    unchanged_review: int
    details: list[dict]           # per-pair delta records


def simulate_policy(
    pairs: list[dict],
    new_yaml: str,
) -> SimulateResult:
    """Re-apply *new_yaml* policy against *pairs* and return change summary.

    Pairs must each have keys: primary_group, comparisons, match_weight,
    nf_left, nf_right, pair_id, current_decision.
    This is a read-only simulation — no DB writes happen.
    """
    try:
        import yaml  # type: ignore[import]
        raw = yaml.safe_load(new_yaml)
        if not isinstance(raw, dict) or "subgroups" not in raw:
            raise ValueError("invalid policy YAML")
        import tempfile, pathlib
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False, encoding="utf-8") as f:
            yaml.dump(raw, f)
            tmp_path = pathlib.Path(f.name)
        new_policies = load_policy_yaml(tmp_path)
        tmp_path.unlink(missing_ok=True)
    except Exception:
        new_policies = {}

    if not new_policies:
        new_policies = POLICIES

    details = []
    review_to_merge = merge_to_review = unchanged_merge = unchanged_review = 0

    for pair in pairs:
        group = pair.get("primary_group") or ""
        if group not in ("A", "C", "D"):
            continue
        comparisons = pair.get("comparisons", {})
        weight = float(pair.get("match_weight", 0.0))
        nf_left = pair.get("nf_left", {})
        nf_right = pair.get("nf_right", {})
        current = pair.get("current_decision", "")

        if group == "A":
            priority = _A_PRIORITY
        elif group == "C":
            priority = _C_PRIORITY
        else:
            priority = _D_PRIORITY

        matched: list[str] = []
        for sg in priority:
            pol = new_policies.get(sg)
            if pol is None:
                continue
            eligible, _ = _policy_eligible(pol, comparisons, weight, nf_left, nf_right)
            if eligible:
                matched.append(sg)

        new_decision = "MERGEABLE" if matched else "REVIEW"

        if current == new_decision:
            if new_decision == "MERGEABLE":
                unchanged_merge += 1
            else:
                unchanged_review += 1
        elif current != "MERGEABLE" and new_decision == "MERGEABLE":
            review_to_merge += 1
            details.append({"pair_id": pair.get("pair_id"), "from": current, "to": new_decision})
        else:
            merge_to_review += 1
            details.append({"pair_id": pair.get("pair_id"), "from": current, "to": new_decision})

    changed = review_to_merge + merge_to_review
    return SimulateResult(
        total_pairs=len(pairs),
        changed=changed,
        review_to_merge=review_to_merge,
        merge_to_review=merge_to_review,
        unchanged_merge=unchanged_merge,
        unchanged_review=unchanged_review,
        details=details,
    )


def apply_policy(
    primary_group: str,
    comparisons: dict[str, dict],
    match_weight: float,
    nf_left: dict,
    nf_right: dict,
    *, policies: dict[str, SubgroupPolicy] | None = None,
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

    policies = get_policies() if policies is None else policies
    matched: list[str] = []
    for sg in priority:
        pol = policies.get(sg)
        if pol is None:
            continue
        eligible, _ = _policy_eligible(pol, comparisons, match_weight, nf_left, nf_right)
        if eligible:
            matched.append(sg)

    if not matched:
        return PolicyResult(False, "", 0, "NO_ELIGIBLE_SUBGROUP", [], "")

    # decision_subgroup = first in priority order (most conservative / most specific)
    decision_sg = matched[0]
    pol = policies[decision_sg]
    return PolicyResult(
        eligible=True,
        policy_id=pol.policy_id,
        policy_version=pol.version,
        reason="ELIGIBLE",
        matched_subgroups=list(matched),
        decision_subgroup=decision_sg,
        policy_file_version=POLICY_FILE_VERSION,
    )
