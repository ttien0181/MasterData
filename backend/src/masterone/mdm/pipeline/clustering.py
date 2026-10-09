from collections import defaultdict
from hashlib import sha256

from masterone.mdm.pipeline.cluster_guards import has_blocking_violation, run_cluster_guards
from masterone.mdm.pipeline.golden import build_golden

MAX_CLUSTER_MEMBERS = 20


def build_proposals(records: list[dict], pairs: list[dict], run_id: str, policy_version: str,
                    negative_pairs: frozenset | None = None,
                    execution_blocked_pairs: frozenset = frozenset(), cluster_policy=None) -> dict:
    """Create disjoint safe components and separate overlapping review cases."""
    from masterone.mdm.pipeline.cluster_policy import enabled, load_policy
    if enabled():
        return _build_safe_proposals(records, pairs, run_id, policy_version,
                                     negative_pairs or frozenset(), execution_blocked_pairs, cluster_policy)
    by_id = {record["id"]: record for record in records}
    parent = {record_id: record_id for record_id in by_id}
    members = {record_id: {record_id} for record_id in by_id}
    policy = cluster_policy or load_policy(legacy=True)
    # Shared-value guards need only incident edges, not an entire snapshot
    # rescan for every union. Keep original positions, including duplicates.
    incident_pairs = defaultdict(list)
    for position, pair in enumerate(pairs):
        incident_pairs[pair["left_id"]].append((position, pair))
        incident_pairs[pair["right_id"]].append((position, pair))

    def find(record_id):
        while parent[record_id] != record_id:
            parent[record_id] = parent[parent[record_id]]
            record_id = parent[record_id]
        return record_id

    # An execution veto is intentionally separate from a person cannot-link.
    blocked_links = set(execution_blocked_pairs)
    for pair in pairs:
        # A Group D name conflict pauses automatic closure; a steward may still
        # approve that review pair. Only explicit negative decisions block export.
        if pair["decision"] in {"NOT_MERGEABLE"} or pair.get("primary_group") == "D":
            blocked_links.add(tuple(sorted((pair["left_id"], pair["right_id"]))))

    accepted = []
    risky = []
    # edge_order tracks the deterministic sequence of accepted merges (§23 audit)
    edge_order: list[dict] = []
    edge_seq: int = 0

    # §23: sort by match_weight DESC, pair_id ASC
    match_edges = sorted((pair for pair in pairs if pair["decision"] == "MERGEABLE"),
                         key=lambda item: (-item["match_weight"], item["id"]))
    for pair in match_edges:
        left_root, right_root = find(pair["left_id"]), find(pair["right_id"])
        if left_root == right_root:
            accepted.append(pair)
            pair["triage_status"] = "HIGH_CONFIDENCE_PROPOSAL"
            pair["triage_reason"] = "SAFE_COMPONENT_EDGE"
            continue
        combined = members[left_root] | members[right_root]
        has_conflict = any(tuple(sorted((left, right))) in blocked_links
                           for left in members[left_root] for right in members[right_root])
        # Run cluster guards (§21) on the proposed union
        smaller = min((members[left_root], members[right_root]), key=len)
        relevant = {position: item for rid in smaller for position, item in incident_pairs[rid]}
        guard_violations = run_cluster_guards(
            frozenset(members[left_root]), frozenset(members[right_root]),
            by_id, negative_pairs=negative_pairs,
            pairs=[relevant[position] for position in sorted(relevant)], policy=policy,
        )
        guard_blocked = has_blocking_violation(guard_violations)
        if has_conflict or guard_blocked or len(combined) > MAX_CLUSTER_MEMBERS:
            reason = "CLUSTER_RISK" if has_conflict else (
                guard_violations[0].reason if guard_blocked else "CLUSTER_NOT_CONFIRMED"
            )
            pair["triage_status"] = "REVIEW"
            pair["triage_reason"] = reason
            pair["guard_violations"] = [{"guard": v.guard, "reason": v.reason, "severity": v.severity}
                                         for v in guard_violations]
            risky.append(pair)
            continue
        # Accept the merge — record in edge_order for audit (§23)
        edge_order.append({
            "seq": edge_seq,
            "pair_id": pair["id"],
            "left_id": pair["left_id"],
            "right_id": pair["right_id"],
            "score": pair["score"],
            "policy_id": pair.get("policy_id"),
            "decision_subgroup": pair.get("decision_subgroup"),
        })
        edge_seq += 1
        parent[right_root] = left_root
        members[left_root] = combined
        del members[right_root]
        pair["triage_status"] = "HIGH_CONFIDENCE_PROPOSAL"
        pair["triage_reason"] = "SAFE_COMPONENT_EDGE"
        accepted.append(pair)

    for pair in pairs:
        if pair["decision"] == "REVIEW":
            pair["triage_status"] = "REVIEW"
            pair["triage_reason"] = pair["reason"]
        elif pair["decision"] in {"NOT_MERGEABLE", "INSUFFICIENT_EVIDENCE"}:
            pair["triage_status"] = pair["decision"]
            pair["triage_reason"] = pair["reason"]

    proposals = []
    for group in sorted(members.values(), key=lambda group: min(group)):
        if len(group) < 2:
            continue
        member_ids = sorted(group)
        proposal_id = sha256(f"{run_id}:{policy_version}:{','.join(member_ids)}".encode()).hexdigest()
        # Collect edge_order entries and guard_violations relevant to this proposal
        proposal_edges = [e for e in edge_order
                          if e["left_id"] in member_ids and e["right_id"] in member_ids]
        proposals.append({
            "id": proposal_id, "status": "AUTO_READY", "member_record_ids": member_ids,
            "golden_fields": build_golden(member_ids, by_id), "revision": 1,
            "edge_order": proposal_edges,
        })

    cases = []
    for pair in [item for item in pairs if item["triage_status"] == "REVIEW"]:
        case_key = f"{run_id}:{pair['id']}:{pair['triage_reason']}"
        case_id = sha256(case_key.encode()).hexdigest()
        cases.append({"id": case_id, "pair_ids": [pair["id"]],
                      "member_record_ids": sorted((pair["left_id"], pair["right_id"])),
                      "triage_reason": pair["triage_reason"], "revision": 1})
    return {"proposals": proposals, "review_cases": cases, "risky_pairs": risky}


def _build_safe_proposals(records, pairs, run_id, policy_version, cannot_links, review_vetoes, policy):
    from dataclasses import asdict

    from masterone.mdm.pipeline.cluster_guards import check_component
    from masterone.mdm.pipeline.cluster_policy import GuardBudget, load_policy
    from masterone.mdm.pipeline.decision_semantics import enabled as decision_enabled
    from masterone.mdm.pipeline.decision_store import DecisionStore
    from masterone.mdm.pipeline.matching import value_frequencies

    if not decision_enabled():
        raise ValueError("Cluster safety v2 requires decision semantics v2")
    policy = policy or load_policy()
    by_id = {r["id"]: r for r in records}
    if len(by_id) != len(records):
        raise ValueError("Duplicate cluster record IDs")
    owned = not isinstance(pairs, DecisionStore)
    store = DecisionStore() if owned else pairs
    budget = GuardBudget()
    try:
        if owned:
            for pair in pairs:
                store.add(pair)
            store.seal()
        # Preserve constrained union-find; each component is consistent before union.
        parent = {rid: rid for rid in by_id}
        members = {rid: {rid} for rid in by_id}
        edges = {rid: [] for rid in by_id}

        def find(rid):
            while parent[rid] != rid:
                parent[rid] = parent[parent[rid]]
                rid = parent[rid]
            return rid

        frequencies = value_frequencies(records)
        sequence = 0
        for pair in store.ordered(matches=True):
            a, b = pair["left_id"], pair["right_id"]
            if a not in parent or b not in parent:
                raise ValueError("Decision endpoint absent from cluster snapshot")
            ra, rb = find(a), find(b)
            combined = members[ra] | members[rb]
            reason = check_component(combined, by_id, store, policy, budget, frequencies,
                                     cannot_links, review_vetoes)
            if reason:
                pair.update(triage_status="REVIEW", triage_reason=reason,
                    guard_violations=[{"guard": "COMPONENT_CONSISTENCY", "reason": reason, "severity": "BLOCK"}])
            else:
                pair.update(triage_status="HIGH_CONFIDENCE_PROPOSAL", triage_reason="SAFE_COMPONENT_EDGE")
                if ra != rb:
                    edges[ra] += edges.pop(rb) + [{"seq": sequence, "pair_id": pair["id"],
                        "left_id": a, "right_id": b, "score": pair.get("score"),
                        "policy_id": pair.get("policy_id"), "decision_subgroup": pair.get("decision_subgroup")}]
                    sequence += 1
                    parent[rb] = ra
                    members[ra] = combined
                    del members[rb]
            store.update(pair)
        proposals = []
        for root, group in sorted(members.items(), key=lambda item: min(item[1])):
            if len(group) < 2:
                continue
            ids = sorted(group)
            proposals.append({"id": sha256(f"{run_id}:{policy_version}:{','.join(ids)}".encode()).hexdigest(),
                "status": "AUTO_READY", "member_record_ids": ids, "revision": 1,
                "golden_fields": build_golden(ids, by_id, policy=policy), "edge_order": sorted(edges[root], key=lambda e: e["seq"])})
        cases, risky = [], []
        for pair in store:
            if pair["decision"] == "REVIEW":
                pair.update(triage_status="REVIEW", triage_reason=pair["reason"])
                store.update(pair)
            if pair.get("triage_status") != "REVIEW":
                continue
            cases.append({"id": sha256(f"{run_id}:{pair['id']}:{pair['triage_reason']}".encode()).hexdigest(),
                "pair_ids": [pair["id"]], "member_record_ids": sorted((pair["left_id"], pair["right_id"])),
                "triage_reason": pair["triage_reason"], "revision": 1})
            if owned and pair["decision"] == "MERGEABLE":
                risky.append(pair)
        if owned:
            for pair in pairs:
                pair.update(store.get_id(pair["id"]))
        return {"proposals": proposals, "review_cases": cases, "risky_pairs": risky if owned else
                (p for p in store.ordered(review=True) if p["decision"] == "MERGEABLE"),
                "cluster_metrics": {**asdict(budget), "policy": asdict(policy), "edge_order": "match_weight DESC NULLS LAST, left_id ASC, right_id ASC"}}
    finally:
        if owned:
            store.close()
