from hashlib import sha256

from masterone.mdm.pipeline.golden import build_golden

MAX_CLUSTER_MEMBERS = 25


def _has_source_unique_conflict(member_ids: set[str], by_id: dict) -> bool:
    """Guard mục 21.3: nguồn single-unique không thể có 2 record trong cùng entity.

    Mỗi nguồn (CRM/ERP/BILL) cam kết mỗi external_id là một thực thể riêng.
    Nếu cluster gộp 2 record cùng source_system → báo conflict.
    """
    seen_sources: dict[str, str] = {}
    for rid in member_ids:
        src = by_id[rid].get("source_system", "")
        if src and src in seen_sources:
            return True
        if src:
            seen_sources[src] = rid
    return False


def build_proposals(records: list[dict], pairs: list[dict], run_id: str, policy_version: str) -> dict:
    """Create disjoint safe components and separate overlapping review cases."""
    by_id = {record["id"]: record for record in records}
    parent = {record_id: record_id for record_id in by_id}
    members = {record_id: {record_id} for record_id in by_id}

    def find(record_id):
        while parent[record_id] != record_id:
            parent[record_id] = parent[parent[record_id]]
            record_id = parent[record_id]
        return record_id

    hard_links = set()
    for pair in pairs:
        if pair["decision"] == "NON_MATCH":
            hard_links.add(tuple(sorted((pair["left_id"], pair["right_id"]))))

    accepted = []
    risky = []
    match_edges = sorted((pair for pair in pairs if pair["decision"] == "MATCH_CANDIDATE"),
                         key=lambda item: (-item["score"], item["left_id"], item["right_id"]))
    for pair in match_edges:
        left_root, right_root = find(pair["left_id"]), find(pair["right_id"])
        if left_root == right_root:
            accepted.append(pair)
            pair["triage_status"] = "HIGH_CONFIDENCE_PROPOSAL"
            pair["triage_reason"] = "SAFE_COMPONENT_EDGE"
            continue
        combined = members[left_root] | members[right_root]
        has_conflict = any(tuple(sorted((left, right))) in hard_links
                           for left in members[left_root] for right in members[right_root])
        has_source_dup = _has_source_unique_conflict(combined, by_id)
        if has_conflict or has_source_dup or len(combined) > MAX_CLUSTER_MEMBERS:
            if has_conflict:
                triage_reason = "CLUSTER_RISK"
            elif has_source_dup:
                triage_reason = "SAME_SOURCE_UNIQUE_CONFLICT"
            else:
                triage_reason = "CLUSTER_NOT_CONFIRMED"
            pair["triage_status"] = "REVIEW"
            pair["triage_reason"] = triage_reason
            risky.append(pair)
            continue
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
        elif pair["decision"] == "NON_MATCH":
            pair["triage_status"] = "NON_MATCH_PROPOSAL"
            pair["triage_reason"] = pair["reason"]

    proposals = []
    for group in sorted(members.values(), key=lambda group: min(group)):
        if len(group) < 2:
            continue
        member_ids = sorted(group)
        proposal_id = sha256(f"{run_id}:{policy_version}:{','.join(member_ids)}".encode()).hexdigest()
        proposals.append({"id": proposal_id, "status": "AUTO_READY", "member_record_ids": member_ids,
                          "golden_fields": build_golden(member_ids, by_id), "revision": 1})

    cases = []
    for pair in [item for item in pairs if item["triage_status"] == "REVIEW"]:
        case_key = f"{run_id}:{pair['id']}:{pair['triage_reason']}"
        case_id = sha256(case_key.encode()).hexdigest()
        cases.append({"id": case_id, "pair_ids": [pair["id"]],
                      "member_record_ids": sorted((pair["left_id"], pair["right_id"])),
                      "triage_reason": pair["triage_reason"], "revision": 1})
    return {"proposals": proposals, "review_cases": cases, "risky_pairs": risky}
