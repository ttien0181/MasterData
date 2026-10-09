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


# Sources whose external_id is a unique-per-entity key (configured statically for MVP)
_SOURCE_UNIQUE_KEYS: frozenset[str] = frozenset({"CRM", "ERP"})

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
    for rec in records:
        nf = rec.get("normalized_fields", {})
        gov_id   = nf.get("government_id", "")
        gov_type = nf.get("government_id_type", "UNKNOWN")
        quality  = nf.get("_field_quality", {}).get("government_id", "UNKNOWN")
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

def _same_source_guard(records: list[dict]) -> GuardViolation | None:
    """Block if a source_unique source would have 2+ records in the cluster."""
    source_counts: dict[str, int] = {}
    for rec in records:
        src = rec.get("source_system", "")
        if src in _SOURCE_UNIQUE_KEYS:
            source_counts[src] = source_counts.get(src, 0) + 1
    for src, count in sorted(source_counts.items()):
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
) -> list[GuardViolation]:
    """Run all guards on the proposed union of left_ids ∪ right_ids.

    Returns a (possibly empty) list of violations ordered by severity (BLOCK first).
    """
    records = _records_in_clusters(left_ids, right_ids, by_id)
    violations: list[GuardViolation] = []

    v = _unique_id_guard(records)
    if v:
        violations.append(v)

    if negative_pairs:
        v = _negative_edge_guard(left_ids, right_ids, negative_pairs)
        if v:
            violations.append(v)

    v = _same_source_guard(records)
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
