"""Regression tests: cluster guards must be order-independent, and the incident-edge
subset used by build_proposals must yield the same shared-value verdict as all pairs."""

import random

from masterone.mdm.pipeline.cluster_guards import (
    _records_in_clusters,
    _shared_value_guard,
    run_cluster_guards,
)


def _rec(rid, source):
    return {
        "id": rid, "source_system": source,
        "normalized_fields": {"_field_quality": {}},
    }


def test_records_in_clusters_are_sorted_by_id():
    by_id = {f"r{i:03d}": _rec(f"r{i:03d}", "BILL") for i in range(30)}
    ids = list(by_id)
    left = frozenset(ids[::3])
    right = frozenset(ids[1::3])
    recs = _records_in_clusters(left, right, by_id)
    assert [r["id"] for r in recs] == sorted(by_id[i]["id"] for i in left | right)


def test_same_source_reason_is_deterministic_when_two_sources_conflict():
    by_id = {
        "a1": _rec("a1", "ERP"), "a2": _rec("a2", "ERP"),
        "b1": _rec("b1", "CRM"), "b2": _rec("b2", "CRM"),
    }
    reasons = set()
    for _ in range(20):
        violations = run_cluster_guards(frozenset({"a1", "b1"}), frozenset({"a2", "b2"}), by_id)
        reasons.add(tuple(v.reason for v in violations if v.guard == "SAME_SOURCE_GUARD"))
    assert reasons == {("CLUSTER_SOURCE_UNIQUE_CONFLICT_CRM",)}


def test_incident_subset_gives_same_shared_value_verdict_as_all_pairs():
    rng = random.Random(7)
    ids = [f"r{i:03d}" for i in range(80)]
    pairs = []
    seen = set()
    while len(pairs) < 300:
        a, b = rng.sample(ids, 2)
        key = tuple(sorted((a, b)))
        if key in seen:
            continue
        seen.add(key)
        state = rng.choice(["SH", "DF", "MI", "AG"])
        pairs.append({"id": f"{key[0]}|{key[1]}", "left_id": key[0], "right_id": key[1],
                      "comparisons": {"phone": {"state": state}, "email": {"state": "MI"}}})

    for _ in range(50):
        members = rng.sample(ids, 12)
        left, right = frozenset(members[:6]), frozenset(members[6:])
        smaller = min((left, right), key=len)
        subset = [p for p in pairs if p["left_id"] in smaller or p["right_id"] in smaller]
        assert _shared_value_guard(left, right, subset) == _shared_value_guard(left, right, pairs)
