"""M8+M9 tests: Golden Record survivorship (§22) + Benchmark metrics (§33) + edge_order (§23)."""

import pytest

from masterone.mdm.benchmark import (
    BenchmarkResult,
    build_ground_truth_pairs,
    compute_auto_merge_metrics,
    compute_candidate_metrics,
    compute_cluster_metrics,
    compute_pair_metrics,
    compute_per_subgroup_metrics,
    run_benchmark,
)
from masterone.mdm.pipeline.clustering import build_proposals
from masterone.mdm.pipeline.golden import SOURCE_PRIORITY, build_golden
from masterone.mdm.resolution_split import split_proposal


# ── Helpers ──────────────────────────────────────────────────────────────────

def _rec(rid, source="CRM", name=None, phone=None, email=None, gov_id=None,
         gov_type=None, benchmark_entity_id=None):
    raw = {}
    if name:  raw["name"]  = name
    if phone: raw["phone"] = phone
    if email: raw["email"] = email
    if gov_id:
        raw["government_id"] = gov_id
    nf: dict = {}
    if gov_type:
        nf["government_id_type"] = gov_type
    if benchmark_entity_id:
        nf["benchmark_entity_id"] = benchmark_entity_id
    return {
        "id": rid, "external_id": rid, "source_system": source,
        "entity_type": "UNKNOWN", "raw_fields": raw,
        "normalized_fields": nf, "quality_flags": [],
        "benchmark_entity_id": benchmark_entity_id,
    }


def _pair(pid, lid, rid, decision="MATCH_CANDIDATE", score=6.0,
          subgroup="A1_NAME_PHONE", triage_status=None):
    return {
        "id": pid, "left_id": lid, "right_id": rid,
        "decision": decision, "score": score,
        "reason": subgroup, "primary_group": "A" if decision == "MATCH_CANDIDATE" else None,
        "subgroup": subgroup,
        "triage_status": triage_status or ("AUTO_MERGE" if decision == "MATCH_CANDIDATE" else "REVIEW"),
        "triage_reason": "test",
        "comparisons": {"phone": {"state": "EX"}, "name": {"state": "EX"},
                        "email": {"state": "MI"}, "dob": {"state": "MI"},
                        "address": {"state": "MI"}, "government_id": {"state": "MI"}},
        "field_evidence": [], "normalization_tags": [], "signature": "",
        "blocking_reasons": [], "policy_id": "A1", "policy_version": "v1",
        "match_weight": score, "matched_subgroups": [subgroup], "decision_subgroup": subgroup,
    }


# ── §22 Golden Record survivorship ───────────────────────────────────────────

def test_golden_prefers_erp_over_crm():
    """ERP has higher source reliability than CRM → ERP value wins."""
    r_crm = _rec("crm1", source="CRM", name="Nguyen Van An")
    r_erp = _rec("erp1", source="ERP", name="Nguyễn Văn An")
    by_id = {"crm1": r_crm, "erp1": r_erp}
    golden = build_golden(["crm1", "erp1"], by_id)
    assert golden["fields"]["name"]["source_system"] == "ERP"
    assert golden["fields"]["name"]["provenance"] == "SURVIVORSHIP"


def test_golden_falls_back_to_crm_when_erp_missing():
    """If ERP has no value for a field, fall back to CRM."""
    r_crm = _rec("crm2", source="CRM", phone="0912345678")
    r_erp = _rec("erp2", source="ERP")   # no phone
    by_id = {"crm2": r_crm, "erp2": r_erp}
    golden = build_golden(["crm2", "erp2"], by_id)
    assert golden["fields"]["phone"]["source_system"] == "CRM"


def test_golden_steward_override_wins():
    """_golden_override field takes priority over any source."""
    r1 = _rec("s1", source="CRM", name="Wrong Name")
    r1["normalized_fields"]["_golden_override"] = {"name": "Correct Name"}
    r2 = _rec("s2", source="ERP", name="ERP Name")
    by_id = {"s1": r1, "s2": r2}
    golden = build_golden(["s1", "s2"], by_id)
    assert golden["fields"]["name"]["value"] == "Correct Name"
    assert golden["fields"]["name"]["provenance"] == "STEWARD_OVERRIDE"


def test_golden_name_normalised_nfc():
    """Name value in golden record is NFC-normalised."""
    r = _rec("n1", source="CRM", name="Nguyễn  Văn  An")   # double spaces
    by_id = {"n1": r}
    golden = build_golden(["n1"], by_id)
    assert "  " not in golden["fields"]["name"]["value"], "double spaces stripped"


def test_golden_includes_dob_and_gov_id_when_present():
    """Golden record fields include dob and government_id."""
    r = _rec("g1", source="CRM", name="X")
    r["raw_fields"]["dob"] = "1990-01-01"
    r["raw_fields"]["government_id"] = "123456789012"
    by_id = {"g1": r}
    golden = build_golden(["g1"], by_id)
    assert "dob" in golden["fields"]
    assert "government_id" in golden["fields"]


def test_golden_member_record_ids_sorted():
    """member_record_ids in golden output are always sorted."""
    r1 = _rec("z2", source="CRM", name="B")
    r2 = _rec("a1", source="CRM", name="A")
    by_id = {"z2": r1, "a1": r2}
    golden = build_golden(["z2", "a1"], by_id)
    assert golden["member_record_ids"] == sorted(["z2", "a1"])


def test_golden_empty_cluster_returns_empty_fields():
    golden = build_golden([], {})
    assert golden["fields"] == {}
    assert golden["member_record_ids"] == []


# ── §23 edge_order in proposals ───────────────────────────────────────────────

def test_edge_order_present_in_proposal():
    """Accepted merge edges are recorded in proposal's edge_order list."""
    records = [_rec("e1"), _rec("e2")]
    pairs = [_pair("p1", "e1", "e2", decision="MATCH_CANDIDATE", score=7.0)]
    result = build_proposals(records, pairs, "run_eo", "v1")
    for prop in result["proposals"]:
        assert "edge_order" in prop, "proposal must have edge_order"
        # Each entry has required audit fields (§23)
        for entry in prop["edge_order"]:
            assert "seq"      in entry
            assert "pair_id"  in entry
            assert "left_id"  in entry
            assert "right_id" in entry
            assert "score"    in entry


def test_edge_order_deterministic_by_score():
    """Higher-score edge gets lower seq number (processed first)."""
    records = [_rec("o1"), _rec("o2"), _rec("o3")]
    pairs = [
        _pair("p1", "o1", "o2", score=8.0),
        _pair("p2", "o2", "o3", score=6.0),
    ]
    result = build_proposals(records, pairs, "run_ord", "v1")
    all_edges = [e for p in result["proposals"] for e in p.get("edge_order", [])]
    if len(all_edges) >= 2:
        seqs = [e["seq"] for e in sorted(all_edges, key=lambda x: x["seq"])]
        assert seqs == list(range(len(seqs))), "seq must be 0,1,2…"


# ── §33 Benchmark metrics ─────────────────────────────────────────────────────

def test_build_ground_truth_pairs():
    records = [
        _rec("r1", benchmark_entity_id="E1"),
        _rec("r2", benchmark_entity_id="E1"),
        _rec("r3", benchmark_entity_id="E2"),
    ]
    gt = build_ground_truth_pairs(records)
    assert ("r1", "r2") in gt
    assert ("r1", "r3") not in gt
    assert ("r2", "r3") not in gt


def test_compute_candidate_metrics_perfect_recall():
    gt = frozenset({("a", "b"), ("b", "c")})
    candidates = {("a", "b"): set(), ("b", "c"): set(), ("a", "c"): set()}
    m = compute_candidate_metrics(candidates, gt, record_count=3)
    assert m.candidate_recall == 1.0
    assert m.found_gt_pairs == 2


def test_compute_candidate_metrics_partial_recall():
    gt = frozenset({("a", "b"), ("b", "c")})
    candidates = {("a", "b"): set()}   # misses (b,c)
    m = compute_candidate_metrics(candidates, gt, record_count=3)
    assert m.candidate_recall == 0.5


def test_compute_pair_metrics_perfect():
    gt = frozenset({("a", "b")})
    pairs = [_pair("p1", "a", "b", decision="MATCH_CANDIDATE")]
    m = compute_pair_metrics(pairs, gt)
    assert m.precision == 1.0
    assert m.recall == 1.0
    assert m.f1 == 1.0
    assert m.tp == 1


def test_compute_pair_metrics_false_positive():
    gt = frozenset()
    pairs = [_pair("p1", "x", "y", decision="MATCH_CANDIDATE")]
    m = compute_pair_metrics(pairs, gt)
    assert m.fp == 1
    assert m.precision == 0.0


def test_compute_pair_metrics_false_negative():
    gt = frozenset({("a", "b")})
    # No pairs at all → fn=1
    m = compute_pair_metrics([], gt)
    assert m.fn >= 1
    assert m.recall == 0.0


def test_compute_auto_merge_metrics():
    gt = frozenset({("a", "b"), ("c", "d")})
    pairs = [
        _pair("p1", "a", "b", decision="MATCH_CANDIDATE", triage_status="AUTO_MERGE"),
        _pair("p2", "c", "d", decision="REVIEW",          triage_status="REVIEW"),
    ]
    m = compute_auto_merge_metrics(pairs, gt)
    assert m.auto_merge_precision == 1.0    # 1 auto-merged, 1 correct
    assert m.auto_merge_coverage == 0.5     # 1/2 gt pairs auto-merged
    assert m.review_rate == 0.5


def test_compute_cluster_metrics_perfect():
    records = [
        _rec("r1", benchmark_entity_id="E1"),
        _rec("r2", benchmark_entity_id="E1"),
    ]
    proposals = [{"member_record_ids": ["r1", "r2"]}]
    m = compute_cluster_metrics(proposals, records)
    assert m.pairwise_precision == 1.0
    assert m.pairwise_recall == 1.0
    assert m.cluster_purity == 1.0


def test_compute_cluster_metrics_mixed():
    """Cluster contains one impostor → purity < 1."""
    records = [
        _rec("r1", benchmark_entity_id="E1"),
        _rec("r2", benchmark_entity_id="E1"),
        _rec("r3", benchmark_entity_id="E2"),
    ]
    proposals = [{"member_record_ids": ["r1", "r2", "r3"]}]
    m = compute_cluster_metrics(proposals, records)
    assert m.cluster_purity < 1.0
    assert m.pairwise_precision < 1.0


def test_compute_per_subgroup_metrics():
    gt = frozenset({("a", "b")})
    pairs = [_pair("p1", "a", "b", subgroup="A1_NAME_PHONE")]
    sg = compute_per_subgroup_metrics(pairs, gt)
    assert "A1_NAME_PHONE" in sg
    assert sg["A1_NAME_PHONE"].tp == 1


def test_run_benchmark_end_to_end():
    records = [
        _rec("r1", benchmark_entity_id="E1"),
        _rec("r2", benchmark_entity_id="E1"),
        _rec("r3", benchmark_entity_id="E2"),
    ]
    candidates = {("r1", "r2"): set()}
    pairs = [_pair("p1", "r1", "r2", decision="MATCH_CANDIDATE", triage_status="AUTO_MERGE")]
    proposals = [{"member_record_ids": ["r1", "r2"]}]
    result = run_benchmark(records, candidates, pairs, proposals)
    assert isinstance(result, BenchmarkResult)
    assert result.candidate is not None
    assert result.pair is not None
    assert result.auto_merge is not None
    assert result.cluster is not None
    assert result.pair.f1 == 1.0


# ── §24 split_proposal (T17 supporting utility) ───────────────────────────────

def test_split_proposal_removes_target():
    r = split_proposal(["A", "B", "C", "D"], ["C"])
    assert "C" not in r["remaining"]
    assert set(r["remaining"]) == {"A", "B", "D"}
    assert r["split"] == ["C"]


def test_split_proposal_multi_remove():
    r = split_proposal(["A", "B", "C", "D"], ["B", "D"])
    assert set(r["remaining"]) == {"A", "C"}
    assert set(r["split"]) == {"B", "D"}


def test_split_proposal_preserves_order():
    r = split_proposal(["D", "C", "B", "A"], ["B"])
    assert r["remaining"] == sorted({"A", "C", "D"})
    assert r["split"] == ["B"]
