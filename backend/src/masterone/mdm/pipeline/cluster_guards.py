"""Cluster Guards — checked before any auto-merge is committed (architecture §21).

Guards evaluate the *proposed union* of two clusters, not just the triggering pair.
Each guard returns a GuardViolation or None.

§21.1  Unique ID Guard   — two distinct gov_ids of the same type in one cluster
§21.2  Negative Edge Guard — any negative edge between the two clusters
§21.3  Same-source Guard   — source declared unique_per_entity cannot have 2+ records
§21.4  Shared-value Guard  — cluster formed primarily by a shared/high-freq value
§21.5  Anomaly Guard       — cluster too large, too many sources, or rapid growth
"""

from __future__ import annotations

from dataclasses import dataclass

# Shared-value: fraction of edges that are phone/email SH or DF across the cluster
_SHARED_VALUE_EDGE_RATIO = 0.5

# Anomaly thresholds
_MAX_CLUSTER_SIZE     = 20
_MAX_SAME_SOURCE_DUPLICATES = 2  # a source-unique source may contribute at most 1 record


@dataclass(frozen=True)
class GuardViolation:
    guard: str        # e.g. "UNIQUE_ID_GUARD"
    reason: str       # human-readable code stored on proposal/pair
    severity: str     # "BLOCK" | "WARN"


def _records_in_clusters(
    left_ids: frozenset[str],
    right_ids: frozenset[str],
    by_id: dict[str, dict],
) -> list[dict]:
    # Sorted: frozenset order depends on PYTHONHASHSEED and would make the
    # reported guard reason (first source over threshold) vary between runs.
    return [by_id[rid] for rid in sorted(left_ids | right_ids) if rid in by_id]


# ── §21.1 Unique ID Guard ─────────────────────────────────────────────────────

def _unique_id_guard(records: list[dict]) -> GuardViolation | None:
    """Block if the proposed cluster contains 2+ distinct gov_ids of the same type."""
    seen: dict[str, str] = {}  # type → value
    from masterone.mdm.pipeline.decision_semantics import enabled, identifier_key, identifier_single_value
    for rec in records:
        nf = rec.get("normalized_fields", {})
        gov_id   = nf.get("government_id", "")
        gov_type = nf.get("government_id_type", "UNKNOWN")
        quality  = nf.get("_field_quality", {}).get("government_id", "UNKNOWN")
        if enabled():
            gov_type = identifier_key(nf)
            if gov_type is None or not identifier_single_value(nf):
                continue
        if not gov_id or gov_type == "UNKNOWN" or quality not in {"VALID", "UNKNOWN"}:
            continue
        if gov_type in seen:
            if seen[gov_type] != gov_id:
                return GuardViolation(
                    guard="UNIQUE_ID_GUARD",
                    reason="CLUSTER_GOV_ID_CONFLICT",
                    severity="BLOCK",
                )
        else:
            seen[gov_type] = gov_id
    return None


# ── §21.2 Negative Edge Guard ─────────────────────────────────────────────────

def _negative_edge_guard(
    left_ids: frozenset[str],
    right_ids: frozenset[str],
    negative_pairs: frozenset[tuple[str, str]],
) -> GuardViolation | None:
    """Block if any negative edge connects a member of the left cluster to the right."""
    for lid in left_ids:
        for rid in right_ids:
            if tuple(sorted((lid, rid))) in negative_pairs:
                return GuardViolation(
                    guard="NEGATIVE_EDGE_GUARD",
                    reason="CLUSTER_NEGATIVE_EDGE",
                    severity="BLOCK",
                )
    return None


# ── §21.3 Same-source Unique Key Guard ───────────────────────────────────────

def _same_source_guard(records: list[dict], policy=None) -> GuardViolation | None:
    """Block if a source_unique source would have 2+ records in the cluster."""
    from masterone.mdm.pipeline.cluster_policy import enabled, load_policy
    policy = policy or load_policy(legacy=not enabled())
    source_counts: dict[tuple[str, str], int] = {}
    for rec in records:
        src = rec.get("source_system", "")
        domain = rec.get("entity_type") or "UNKNOWN"
        if policy.unique(src, domain):
            source_counts[(src, domain)] = source_counts.get((src, domain), 0) + 1
    for (src, _), count in sorted(source_counts.items()):
        if count >= _MAX_SAME_SOURCE_DUPLICATES:
            return GuardViolation(
                guard="SAME_SOURCE_GUARD",
                reason=f"CLUSTER_SOURCE_UNIQUE_CONFLICT_{src}",
                severity="BLOCK",
            )
    return None


# ── §21.4 Shared-value Guard ─────────────────────────────────────────────────

def _shared_value_guard(
    left_ids: frozenset[str],
    right_ids: frozenset[str],
    pairs: list[dict],
) -> GuardViolation | None:
    """Warn if most edges linking the two clusters are driven by SH/DF contact values."""
    inter_pairs = [
        p for p in pairs
        if (p.get("left_id") in left_ids and p.get("right_id") in right_ids)
        or (p.get("left_id") in right_ids and p.get("right_id") in left_ids)
    ]
    if not inter_pairs:
        return None
    shared_count = 0
    for pair in inter_pairs:
        comps = pair.get("comparisons", {})
        phone_state = comps.get("phone", {}).get("state", "MI")
        email_state = comps.get("email", {}).get("state", "MI")
        if phone_state in {"SH", "DF"} or email_state in {"SH", "DF"}:
            shared_count += 1
    if shared_count / len(inter_pairs) >= _SHARED_VALUE_EDGE_RATIO:
        return GuardViolation(
            guard="SHARED_VALUE_GUARD",
            reason="CLUSTER_SHARED_VALUE_DOMINANT",
            severity="WARN",
        )
    return None


# ── §21.5 Anomaly Guard ───────────────────────────────────────────────────────

def _anomaly_guard(records: list[dict]) -> GuardViolation | None:
    """Block clusters that exceed safe size or show anomalous structure."""
    if len(records) > _MAX_CLUSTER_SIZE:
        return GuardViolation(
            guard="ANOMALY_GUARD",
            reason="CLUSTER_SIZE_EXCEEDED",
            severity="BLOCK",
        )
    source_counts: dict[str, int] = {}
    for rec in records:
        src = rec.get("source_system", "")
        source_counts[src] = source_counts.get(src, 0) + 1
    # Anomaly: single source contributes > 80% of the cluster → suspicious
    total = len(records)
    for src, count in source_counts.items():
        if total >= 5 and count / total >= 0.8:
            return GuardViolation(
                guard="ANOMALY_GUARD",
                reason=f"CLUSTER_SOURCE_DOMINANCE_{src}",
                severity="WARN",
            )
    return None


# ── Public API ────────────────────────────────────────────────────────────────

def run_cluster_guards(
    left_ids: frozenset[str],
    right_ids: frozenset[str],
    by_id: dict[str, dict],
    negative_pairs: frozenset[tuple[str, str]] | None = None,
    pairs: list[dict] | None = None,
    policy=None,
) -> list[GuardViolation]:
    """Run all guards on the proposed union of left_ids ∪ right_ids.

    Returns a (possibly empty) list of violations ordered by severity (BLOCK first).
    """
    records = _records_in_clusters(left_ids, right_ids, by_id)
    violations: list[GuardViolation] = []

    from masterone.mdm.pipeline.decision_semantics import enabled, person_constraints
    if enabled():
        from itertools import combinations
        for left, right in combinations(records, 2):
            hard = person_constraints(left, right, negative_pairs or ())
            if hard.forbids_merge:
                violations.append(GuardViolation("PERSON_CONSTRAINT_GUARD",
                    hard.violations[0] if hard.violations else "CLUSTER_NEGATIVE_EDGE", "BLOCK"))
                break

    v = _unique_id_guard(records)
    if v:
        violations.append(v)

    if negative_pairs:
        v = _negative_edge_guard(left_ids, right_ids, negative_pairs)
        if v:
            violations.append(v)

    v = _same_source_guard(records, policy)
    if v:
        violations.append(v)

    if pairs:
        v = _shared_value_guard(left_ids, right_ids, pairs)
        if v:
            violations.append(v)

    v = _anomaly_guard(records)
    if v:
        violations.append(v)

    # Sort BLOCK before WARN
    violations.sort(key=lambda x: 0 if x.severity == "BLOCK" else 1)
    return violations


def has_blocking_violation(violations: list[GuardViolation]) -> bool:
    return any(v.severity == "BLOCK" for v in violations)


def check_component(member_ids, by_id, store, policy, budget, frequencies,
                    cannot_links=frozenset(), review_vetoes=frozenset(), *, allow_missing_evidence=False):
    """Person consistency on every member pair; bounded, cached, and no edge scan."""
    from itertools import combinations

    from masterone.mdm.pipeline.decision_semantics import person_constraints
    from masterone.mdm.pipeline.matching import compare_and_score_rule

    ids = sorted(member_ids)
    if len(ids) > policy.max_cluster_size:
        return "CLUSTER_SIZE_LIMIT"
    if len(ids) * (len(ids) - 1) // 2 > policy.max_pair_checks_per_union:
        return "CLUSTER_GUARD_UNION_BUDGET"
    records = [by_id[rid] for rid in ids]
    source_conflict = _same_source_guard(records, policy)
    if source_conflict:
        return source_conflict.reason
    for a, b in combinations(ids, 2):
        if (a, b) in cannot_links:
            return "CLUSTER_CONFIRMED_CANNOT_LINK"
        if (a, b) in review_vetoes:
            return "CLUSTER_REVIEW_VETO"
        if budget.visited >= policy.max_pair_checks_per_run:
            budget.blocked += 1
            return "CLUSTER_GUARD_RUN_BUDGET"
        budget.visited += 1
        cached = store.check(a, b)
        if cached is not None:
            budget.cache_hits += 1
            if cached:
                return cached
            continue
        budget.checked += 1
        hard = person_constraints(by_id[a], by_id[b])
        pair = store.get(a, b)
        reason = ""
        if hard.forbids_merge:
            reason = hard.violations[0]
        elif pair and pair.get("user_decision") in {"REJECTED", "NON_MATCH"}:
            reason = "CLUSTER_REVIEW_VETO" if pair["user_decision"] == "REJECTED" else "CLUSTER_CONFIRMED_CANNOT_LINK"
        elif pair and pair["decision"] in {"NOT_MERGEABLE", "NON_MATCH", "BLOCKED"}:
            reason = "CLUSTER_PAIR_PERMISSION_CONFLICT"
        elif pair and pair.get("primary_group") == "D" and not (
                pair.get("user_decision") == "ACCEPTED" and (pair.get("review_reason") or '').strip()):
            reason = "SOFT_ACCEPT_REASON_REQUIRED" if pair.get("user_decision") == "ACCEPTED" else "CLUSTER_PAIR_PERMISSION_CONFLICT"
        else:
            observed = pair or compare_and_score_rule(by_id[a], by_id[b], set(), frequencies)
            comparisons = observed.get("comparisons", {})
            states = {f: v.get("state") for f, v in comparisons.items()}
            id_anchor = states.get("government_id") == "EX"
            contact = any(states.get(f) in {"EX", "NX"} for f in ("phone", "email"))
            name = states.get("name") in {"EX", "NX", "NA", "HS"}
            if not allow_missing_evidence and not id_anchor and not (contact and name):
                reason = "CLUSTER_BRIDGE_MISSING_EVIDENCE"
        store.save_check(a, b, reason)
        if reason:
            return reason
    return ""
