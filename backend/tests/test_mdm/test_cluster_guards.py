"""M6 tests: Cluster Guards (architecture §21)."""

import pytest

from masterone.mdm.pipeline.cluster_guards import (
    GuardViolation,
    has_blocking_violation,
    run_cluster_guards,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _rec(rid, source="CRM", gov_id=None, gov_type=None, gov_quality="VALID"):
    nf = {}
    if gov_id:
        nf["government_id"] = gov_id
        nf["government_id_type"] = gov_type or "CCCD_12"
        nf["_field_quality"] = {"government_id": gov_quality}
    return {"id": rid, "source_system": source, "normalized_fields": nf, "raw_fields": {}}


def _guards(left_ids, right_ids, records, negative_pairs=None, pairs=None):
    by_id = {r["id"]: r for r in records}
    return run_cluster_guards(
        frozenset(left_ids), frozenset(right_ids),
        by_id, negative_pairs=negative_pairs, pairs=pairs,
    )


# ── §21.1 Unique ID Guard ─────────────────────────────────────────────────────

def test_unique_id_guard_blocks_conflicting_gov_ids():
    r1 = _rec("a", gov_id="123456789012", gov_type="CCCD_12")
    r2 = _rec("b", gov_id="999888777666", gov_type="CCCD_12")
    violations = _guards(["a"], ["b"], [r1, r2])
    assert any(v.guard == "UNIQUE_ID_GUARD" for v in violations)
    assert has_blocking_violation(violations)


def test_unique_id_guard_allows_same_gov_id():
    r1 = _rec("a", gov_id="123456789012", gov_type="CCCD_12")
    r2 = _rec("b", gov_id="123456789012", gov_type="CCCD_12")
    violations = _guards(["a"], ["b"], [r1, r2])
    assert not any(v.guard == "UNIQUE_ID_GUARD" for v in violations)


def test_unique_id_guard_allows_different_types():
    r1 = _rec("a", gov_id="123456789", gov_type="CMND_9")
    r2 = _rec("b", gov_id="123456789012", gov_type="CCCD_12")
    violations = _guards(["a"], ["b"], [r1, r2])
    assert not any(v.guard == "UNIQUE_ID_GUARD" for v in violations)


def test_unique_id_guard_skips_unknown_type():
    r1 = _rec("a", gov_id="XXXXX", gov_type="UNKNOWN")
    r2 = _rec("b", gov_id="YYYYY", gov_type="UNKNOWN")
    violations = _guards(["a"], ["b"], [r1, r2])
    assert not any(v.guard == "UNIQUE_ID_GUARD" for v in violations)


# ── §21.2 Negative Edge Guard ─────────────────────────────────────────────────

def test_negative_edge_guard_blocks_known_non_match():
    r1 = _rec("x1")
    r2 = _rec("x2")
    neg = frozenset({tuple(sorted(("x1", "x2")))})
    violations = _guards(["x1"], ["x2"], [r1, r2], negative_pairs=neg)
    assert any(v.guard == "NEGATIVE_EDGE_GUARD" for v in violations)
    assert has_blocking_violation(violations)


def test_negative_edge_guard_blocks_cross_cluster():
    """Negative edge between a cluster member and the other cluster's member."""
    r1 = _rec("c1")
    r2 = _rec("c2")
    r3 = _rec("c3")
    neg = frozenset({tuple(sorted(("c1", "c3")))})
    violations = _guards(["c1", "c2"], ["c3"], [r1, r2, r3], negative_pairs=neg)
    assert any(v.guard == "NEGATIVE_EDGE_GUARD" for v in violations)


def test_negative_edge_guard_passes_unrelated_pairs():
    r1 = _rec("d1")
    r2 = _rec("d2")
    neg = frozenset({tuple(sorted(("d1", "d99")))})
    violations = _guards(["d1"], ["d2"], [r1, r2], negative_pairs=neg)
    assert not any(v.guard == "NEGATIVE_EDGE_GUARD" for v in violations)


def test_no_negative_pairs_no_guard():
    r1 = _rec("e1")
    r2 = _rec("e2")
    violations = _guards(["e1"], ["e2"], [r1, r2], negative_pairs=None)
    assert not any(v.guard == "NEGATIVE_EDGE_GUARD" for v in violations)


# ── §21.3 Same-source Guard ────────────────────────────────────────────────────

def test_same_source_guard_warns_on_two_crm_records():
    r1 = _rec("f1", source="CRM")
    r2 = _rec("f2", source="CRM")
    violations = _guards(["f1"], ["f2"], [r1, r2])
    assert any(v.guard == "SAME_SOURCE_GUARD" for v in violations)
    # WARN not BLOCK — steward may override
    same_src = [v for v in violations if v.guard == "SAME_SOURCE_GUARD"]
    assert all(v.severity == "WARN" for v in same_src)


def test_same_source_guard_passes_different_sources():
    r1 = _rec("g1", source="CRM")
    r2 = _rec("g2", source="ERP")
    violations = _guards(["g1"], ["g2"], [r1, r2])
    assert not any(v.guard == "SAME_SOURCE_GUARD" for v in violations)


def test_same_source_guard_passes_non_unique_source():
    r1 = _rec("h1", source="CSV_IMPORT")
    r2 = _rec("h2", source="CSV_IMPORT")
    violations = _guards(["h1"], ["h2"], [r1, r2])
    assert not any(v.guard == "SAME_SOURCE_GUARD" for v in violations)


# ── §21.4 Shared-value Guard ──────────────────────────────────────────────────

def test_shared_value_guard_warns_when_dominant():
    r1 = _rec("i1")
    r2 = _rec("i2")
    # All edges are SH phone
    pairs = [
        {"left_id": "i1", "right_id": "i2", "comparisons": {
            "phone": {"state": "SH"}, "email": {"state": "MI"}}}
    ]
    violations = _guards(["i1"], ["i2"], [r1, r2], pairs=pairs)
    assert any(v.guard == "SHARED_VALUE_GUARD" for v in violations)


def test_shared_value_guard_passes_when_not_dominant():
    r1 = _rec("j1")
    r2 = _rec("j2")
    pairs = [
        {"left_id": "j1", "right_id": "j2", "comparisons": {
            "phone": {"state": "EX"}, "email": {"state": "EX"}}}
    ]
    violations = _guards(["j1"], ["j2"], [r1, r2], pairs=pairs)
    assert not any(v.guard == "SHARED_VALUE_GUARD" for v in violations)


# ── §21.5 Anomaly Guard ────────────────────────────────────────────────────────

def test_anomaly_guard_blocks_oversized_cluster():
    records = [_rec(f"z{i}") for i in range(22)]
    left_ids  = [r["id"] for r in records[:11]]
    right_ids = [r["id"] for r in records[11:]]
    violations = _guards(left_ids, right_ids, records)
    assert any(v.guard == "ANOMALY_GUARD" for v in violations)
    assert has_blocking_violation(violations)


def test_anomaly_guard_passes_small_cluster():
    records = [_rec(f"y{i}") for i in range(4)]
    violations = _guards(["y0", "y1"], ["y2", "y3"], records)
    assert not any(v.guard == "ANOMALY_GUARD" for v in violations)


# ── has_blocking_violation ─────────────────────────────────────────────────────

def test_has_blocking_violation_true_for_block():
    v = GuardViolation("X", "REASON", "BLOCK")
    assert has_blocking_violation([v]) is True


def test_has_blocking_violation_false_for_warn_only():
    v = GuardViolation("X", "REASON", "WARN")
    assert has_blocking_violation([v]) is False


def test_has_blocking_violation_false_empty():
    assert has_blocking_violation([]) is False


# ── guard ordering (BLOCK before WARN) ───────────────────────────────────────

def test_violations_sorted_block_first():
    """BLOCK violations must precede WARN violations in the result list."""
    r1 = _rec("k1", source="CRM", gov_id="000000000000", gov_type="CCCD_12")
    r2 = _rec("k2", source="CRM", gov_id="999999999999", gov_type="CCCD_12")
    violations = _guards(["k1"], ["k2"], [r1, r2])
    sevs = [v.severity for v in violations]
    block_indices = [i for i, s in enumerate(sevs) if s == "BLOCK"]
    warn_indices  = [i for i, s in enumerate(sevs) if s == "WARN"]
    if block_indices and warn_indices:
        assert max(block_indices) < min(warn_indices)
