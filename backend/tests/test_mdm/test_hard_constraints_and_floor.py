"""M4 tests: Hard Constraints (§11) and Evidence Floor (§12)."""

import pytest

from masterone.mdm.pipeline.evidence_floor import evaluate as floor_evaluate
from masterone.mdm.pipeline.hard_constraints import (
    HardConstraintViolation,
    check_all,
    check_entity_type,
    check_gov_id,
    check_negative_edge,
)
from masterone.mdm.pipeline.matching import score_pair
from masterone.mdm.pipeline.standardization import standardize


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make(rid, source="CRM", entity_type="UNKNOWN", **raw):
    nf, flags = standardize(raw)
    return {"id": rid, "external_id": rid, "source_system": source,
            "entity_type": entity_type, "raw_fields": raw,
            "normalized_fields": nf, "quality_flags": flags}


def _result(r1, r2, reasons=None, negative_pairs=None):
    return score_pair(r1, r2, reasons or {"name_gram"},
                      negative_pairs=negative_pairs)


# ── §11.1 Person vs Organisation ─────────────────────────────────────────────

def test_person_vs_org_is_hard_conflict():
    person = _make("p1", entity_type="PERSON", name="Nguyen Van An", phone="0912345678")
    org    = _make("o1", entity_type="ORGANIZATION", name="Nguyen Van An", phone="0912345678")
    r = _result(person, org)
    assert r["decision"] == "NON_MATCH"
    assert r["reason"] == "PERSON_ORG_MISMATCH"


def test_same_entity_type_not_blocked():
    p1 = _make("p1", entity_type="PERSON", name="Nguyen Van An", phone="0912345678")
    p2 = _make("p2", entity_type="PERSON", name="Nguyen Van An", phone="0912345678")
    r = _result(p1, p2)
    assert r["decision"] != "NON_MATCH"


def test_unknown_entity_type_not_blocked():
    r1 = _make("r1", entity_type="UNKNOWN", name="Nguyen Van An", phone="0912345678")
    r2 = _make("r2", entity_type="UNKNOWN", name="Nguyen Van An", phone="0912345678")
    r = _result(r1, r2)
    assert r["decision"] != "NON_MATCH"


def test_one_unknown_entity_type_not_blocked():
    p = _make("p1", entity_type="PERSON", name="Nguyen Van An", phone="0912345678")
    u = _make("u1", entity_type="UNKNOWN", name="Nguyen Van An", phone="0912345678")
    r = _result(p, u)
    assert r["decision"] != "NON_MATCH"


# ── §11.2 Same-type gov_id conflict ──────────────────────────────────────────

def test_same_type_gov_id_different_is_hard_conflict():
    nf1, _ = standardize({"name": "X", "government_id": "123456789012"})
    nf1["government_id_type"] = "CCCD_12"
    nf2, _ = standardize({"name": "X", "government_id": "999888777666"})
    nf2["government_id_type"] = "CCCD_12"
    r1 = {"id": "h1", "external_id": "h1", "entity_type": "UNKNOWN",
          "source_system": "CRM", "raw_fields": {}, "normalized_fields": nf1, "quality_flags": []}
    r2 = {"id": "h2", "external_id": "h2", "entity_type": "UNKNOWN",
          "source_system": "ERP", "raw_fields": {}, "normalized_fields": nf2, "quality_flags": []}
    r = _result(r1, r2)
    assert r["decision"] == "NON_MATCH"
    assert r["reason"] == "SAME_TYPE_GOV_ID_CONFLICT"


def test_different_gov_id_types_is_nc_not_hard_conflict():
    nf1, _ = standardize({"name": "X", "government_id": "123456789"})
    nf1["government_id_type"] = "CMND_9"
    nf2, _ = standardize({"name": "X", "government_id": "123456789012"})
    nf2["government_id_type"] = "CCCD_12"
    r1 = {"id": "i1", "entity_type": "UNKNOWN", "external_id": "i1",
          "source_system": "CRM", "raw_fields": {}, "normalized_fields": nf1, "quality_flags": []}
    r2 = {"id": "i2", "entity_type": "UNKNOWN", "external_id": "i2",
          "source_system": "ERP", "raw_fields": {}, "normalized_fields": nf2, "quality_flags": []}
    r = _result(r1, r2)
    assert r["decision"] != "NON_MATCH", "Different gov_id types → NC, not hard conflict"


# ── §11.3 Negative edge ───────────────────────────────────────────────────────

def test_negative_edge_blocks_merge():
    r1 = _make("n1", name="Nguyen Van An", phone="0912345678")
    r2 = _make("n2", name="Nguyen Van An", phone="0912345678")
    neg = frozenset({tuple(sorted(("n1", "n2")))})
    r = _result(r1, r2, negative_pairs=neg)
    assert r["decision"] == "NON_MATCH"
    assert r["reason"] == "STEWARD_NEGATIVE_DECISION"


def test_negative_edge_not_applied_to_other_pair():
    r1 = _make("n1", name="Nguyen Van An", phone="0912345678")
    r2 = _make("n2", name="Nguyen Van An", phone="0912345678")
    r3 = _make("n3", name="Nguyen Van An", phone="0912345678")
    neg = frozenset({tuple(sorted(("n1", "n3")))})
    r = _result(r1, r2, negative_pairs=neg)
    assert r["decision"] != "NON_MATCH"


def test_negative_edge_takes_priority_over_score():
    """Negative edge must block even a perfect score pair."""
    r1 = _make("x1", name="Nguyen Van An", phone="0912345678", email="an@test.com")
    r2 = _make("x2", name="Nguyen Van An", phone="0912345678", email="an@test.com")
    neg = frozenset({tuple(sorted(("x1", "x2")))})
    r = _result(r1, r2, negative_pairs=neg)
    assert r["decision"] == "NON_MATCH"
    assert r["score"] == 0.0


# ── §11 check_all ordering ────────────────────────────────────────────────────

def test_negative_edge_checked_before_entity_type():
    """Steward decision (§11.3) is highest priority."""
    person = _make("pe1", entity_type="PERSON", name="A", phone="0912345678")
    org = _make("or1", entity_type="ORGANIZATION", name="A", phone="0912345678")
    neg = frozenset({tuple(sorted(("pe1", "or1")))})
    v = check_all(person, org, negative_pairs=neg)
    assert v is not None
    assert v.constraint == "STEWARD_NEGATIVE_DECISION"


# ── §12 Evidence Floor ────────────────────────────────────────────────────────

def test_floor_passes_with_phone_anchor():
    comps = {"phone": {"state": "EX"}, "email": {"state": "MI"},
             "government_id": {"state": "MI"}}
    result = floor_evaluate(comps, match_weight=2.0)
    assert result.passes is True
    assert result.valid_anchors == 1


def test_floor_fails_no_anchor():
    comps = {"phone": {"state": "DF"}, "email": {"state": "MI"},
             "government_id": {"state": "MI"}}
    result = floor_evaluate(comps, match_weight=2.0)
    assert result.passes is False
    assert result.reason == "NO_VALID_IDENTITY_ANCHOR"


def test_floor_fails_weight_below_min():
    comps = {"phone": {"state": "EX"}, "email": {"state": "MI"},
             "government_id": {"state": "MI"}}
    result = floor_evaluate(comps, match_weight=0.5, w_min=1.0)
    assert result.passes is False
    assert result.reason == "WEIGHT_BELOW_FLOOR"


def test_floor_passes_gov_id_anchor():
    comps = {"phone": {"state": "MI"}, "email": {"state": "MI"},
             "government_id": {"state": "EX"}}
    result = floor_evaluate(comps, match_weight=3.0)
    assert result.passes is True
    assert result.valid_anchors == 1


def test_score_pair_insufficient_evidence_no_contact_match():
    """Name-only match with no EX/NX contact → INSUFFICIENT_EVIDENCE after floor."""
    r1 = _make("f1", name="Nguyen Van An")
    r2 = _make("f2", name="Nguyen Van An")
    r = _result(r1, r2)
    assert r["decision"] == "INSUFFICIENT_EVIDENCE"
    # field_evidence still populated for inspection
    assert len(r["field_evidence"]) == 6


def test_score_pair_with_phone_match_passes_floor():
    """Phone EX/NX anchor → passes floor, continues to grouping."""
    r1 = _make("g1", name="Nguyen Van An", phone="0912345678")
    r2 = _make("g2", name="Nguyen Van An", phone="0912345678")
    r = _result(r1, r2)
    assert r["decision"] != "INSUFFICIENT_EVIDENCE"
    assert r["primary_group"] in ("A", "C", "D")


# ── hard_constraints module unit tests ───────────────────────────────────────

def test_check_entity_type_person_vs_org():
    person = {"id": "p", "entity_type": "PERSON", "normalized_fields": {}}
    org = {"id": "o", "entity_type": "ORGANIZATION", "normalized_fields": {}}
    v = check_entity_type(person, org)
    assert v is not None
    assert v.constraint == "PERSON_ORG_MISMATCH"


def test_check_entity_type_same_returns_none():
    p1 = {"id": "p1", "entity_type": "PERSON", "normalized_fields": {}}
    p2 = {"id": "p2", "entity_type": "PERSON", "normalized_fields": {}}
    assert check_entity_type(p1, p2) is None


def test_check_gov_id_same_type_conflict():
    nf_a = {"government_id": "123456789012", "government_id_type": "CCCD_12",
            "_field_quality": {"government_id": "VALID"}}
    nf_b = {"government_id": "999888777666", "government_id_type": "CCCD_12",
            "_field_quality": {"government_id": "VALID"}}
    v = check_gov_id(nf_a, nf_b)
    assert v is not None
    assert v.constraint == "SAME_TYPE_GOV_ID_CONFLICT"


def test_check_gov_id_same_value_is_none():
    nf_a = {"government_id": "123456789012", "government_id_type": "CCCD_12",
            "_field_quality": {"government_id": "VALID"}}
    nf_b = {"government_id": "123456789012", "government_id_type": "CCCD_12",
            "_field_quality": {"government_id": "VALID"}}
    assert check_gov_id(nf_a, nf_b) is None


def test_check_negative_edge_match():
    neg = frozenset({("a", "b")})
    v = check_negative_edge("a", "b", neg)
    assert v is not None
    assert v.constraint == "STEWARD_NEGATIVE_DECISION"


def test_check_negative_edge_order_independent():
    neg = frozenset({("a", "b")})
    v = check_negative_edge("b", "a", neg)
    assert v is not None


def test_check_negative_edge_no_match():
    neg = frozenset({("a", "b")})
    assert check_negative_edge("a", "c", neg) is None
