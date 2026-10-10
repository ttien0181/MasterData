"""Conservative, PII-free workload gates; approval is bound to a snapshot plan."""

import json
import logging
from dataclasses import dataclass
from hashlib import sha256


@dataclass(frozen=True)
class WorkloadPolicy:
    warning_pairs: int = 20_000_000
    approval_pairs: int = 40_000_000
    hard_pairs: int = 50_000_000
    memory_budget_bytes: int = 512 * 1024 * 1024
    max_dot_products: int = 100_000_000
    max_similarity_cells: int = 100_000_000

    def __post_init__(self):
        values = (self.warning_pairs, self.approval_pairs, self.hard_pairs,
                  self.memory_budget_bytes, self.max_dot_products, self.max_similarity_cells)
        if any(type(v) is not int or v < 1 for v in values):
            raise ValueError("Workload limits must be positive integers")
        if not self.warning_pairs <= self.approval_pairs <= self.hard_pairs:
            raise ValueError("Workload thresholds must increase")


class WorkloadPlanError(RuntimeError):
    def __init__(self, plan):
        super().__init__(f"Candidate workload {plan['action']}: {plan['plan_id']}")
        self.plan = plan


def plan_workload(details, policy, *, snapshot_digest, approved_plan_id=None):
    plan = {**details, "snapshot_digest": snapshot_digest, "policy": vars(policy),
            "version": "p119_sparse_v1", "action": "ALLOW", "reasons": []}
    pairs = details.get("estimated_pairs", 0)
    if pairs >= policy.warning_pairs:
        plan.update(action="ALLOW_WITH_WARNING", reasons=["warning_pairs"])
    if pairs >= policy.approval_pairs:
        plan.update(action="REQUIRE_APPROVAL", reasons=["approval_pairs"])
    reasons = []
    for name, limit in (("estimated_pairs", policy.hard_pairs),
                        ("estimated_memory_bytes", policy.memory_budget_bytes),
                        ("estimated_dot_products", policy.max_dot_products),
                        ("estimated_similarity_cells", policy.max_similarity_cells)):
        if details.get(name, 0) > limit or (name == "estimated_pairs" and pairs == limit):
            reasons.append(name)
    if reasons:
        plan.update(action="REJECT", reasons=reasons)
    plan["plan_id"] = sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
    if plan["action"] != "ALLOW":
        logging.getLogger(__name__).warning("candidate_plan %s", json.dumps(plan, sort_keys=True))
    if plan["action"] == "REJECT" or (
        plan["action"] == "REQUIRE_APPROVAL" and approved_plan_id != plan["plan_id"]
    ):
        raise WorkloadPlanError(plan)
    return plan
