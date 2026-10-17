"""Person decision contract; P-119 policies are an explicit compatibility adapter.

Pair preparation is local. Cluster closure must consume the complete prepared
generation in weight/ID order, never in batch completion order.
"""
from dataclasses import dataclass
from itertools import combinations


def enabled():
    from masterone.mdm.engine_adapter import feature
    return feature("decision_semantics_v2_enabled")


@dataclass(frozen=True)
class PairEvidence:
    fields: tuple[dict, ...]
    rule_score: float
    comparisons: dict


@dataclass(frozen=True)
class HardConstraints:
    violations: tuple[str, ...] = ()
    cannot_link: bool = False

    @property
    def forbids_merge(self):
        return self.cannot_link or bool(self.violations)


@dataclass(frozen=True)
class ReviewDecision:
    value: str = "UNDECIDED"

    def __post_init__(self):
        if self.value not in {"UNDECIDED", "ACCEPTED", "REJECTED", "DEFERRED", "NON_MATCH"}:
            raise ValueError("Unknown stored review decision")

    @property
    def vetoes_merge(self):
        # Historical explicit NON_MATCH is retained, not inferred from REJECTED.
        return self.value in {"REJECTED", "NON_MATCH"}


@dataclass(frozen=True)
class PairEvaluation:
    conclusion: str
    routing: str
    evidence: PairEvidence
    constraints: HardConstraints


@dataclass(frozen=True)
class MergeEligibility:
    manual_allowed: bool
    auto_allowed: bool
    reasons: tuple[str, ...]


def verified(nf, field):
    return (nf.get(f"_{field}_verified") is True
            or nf.get(f"{field}_verification_status") == "verified")


def identifier_key(nf):
    """No namespace or verification inferred from syntax/length/VALID quality."""
    kind, namespace = nf.get("government_id_type"), nf.get("government_id_namespace")
    if (not kind or kind == "UNKNOWN" or not namespace
            or not verified(nf, "government_id")
            or nf.get("_field_quality", {}).get("government_id") != "VALID"
            or not nf.get("government_id")):
        return None
    # Supported person identifier contract. Passport/tax/custom domains require
    # explicit identity uniqueness metadata; arbitrary namespaces are not equal.
    if ((namespace, kind) not in {("VN", "CCCD_12"), ("VN", "CMND_9")}
            and nf.get("government_id_identity_unique") is not True):
        return None
    return namespace, kind


def identifier_single_value(nf):
    return (identifier_key(nf) in {("VN", "CCCD_12"), ("VN", "CMND_9")}
            or nf.get("government_id_single_valued") is True)


def person_constraints(left, right, negative_pairs=()):
    from masterone.mdm.pipeline.hard_constraints import check_entity_type
    violations = []
    if check_entity_type(left, right):
        violations.append("PERSON_ORG_MISMATCH")
    for record in (left, right):
        kind = record.get("entity_type") or record.get("normalized_fields", {}).get("entity_type", "UNKNOWN")
        if kind not in {"PERSON", "UNKNOWN"}:
            violations.append("PERSON_POLICY_OUT_OF_SCOPE")
    a, b = left.get("normalized_fields", {}), right.get("normalized_fields", {})
    ka, kb = identifier_key(a), identifier_key(b)
    if (ka is not None and ka == kb and identifier_single_value(a) and identifier_single_value(b)
            and a["government_id"] != b["government_id"]):
        violations.append("VERIFIED_GOV_ID_CONFLICT")
    if (verified(a, "dob") and verified(b, "dob") and a.get("dob") and b.get("dob")
            and a["dob"] != b["dob"]):
        from masterone.mdm.pipeline.matching import _dob_state
        if _dob_state(a, b)[0] == "CF":
            violations.append("VERIFIED_DOB_CONFLICT")
    # Different phone/email values, even verified, permit change/multiple contacts.
    cannot_link = tuple(sorted((left["id"], right["id"]))) in negative_pairs
    return HardConstraints(tuple(dict.fromkeys(violations)), cannot_link)


def evaluate_prepared(result, left, right, blocking_reasons=(), policies=None,
                      review=ReviewDecision(), negative_pairs=()):
    """The sole evaluator for pipeline and manual review; no union side effects."""
    from masterone.mdm.pipeline.matching import _decide_legacy_rule_result, _non_match
    hard = person_constraints(left, right, negative_pairs) if enabled() else HardConstraints()
    cached = result.get("hard_violation")
    payload = _decide_legacy_rule_result(result, left, right, set(blocking_reasons), policies)
    if hard.forbids_merge:
        payload = _non_match("HARD_CONFLICT", "STEWARD_NEGATIVE_DECISION" if hard.cannot_link
                             else hard.violations[0], set(blocking_reasons))
    if cached:
        hard = HardConstraints(tuple(dict.fromkeys(hard.violations + (cached["constraint"],))),
                               hard.cannot_link or cached["constraint"] == "STEWARD_NEGATIVE_DECISION")
    return adapt_evaluation(payload, hard, review)


def adapt_evaluation(payload, hard=HardConstraints(), review=ReviewDecision()):
    """BLOCKED/NOT_MERGEABLE describe permission, never a negative identity proof."""
    decision = payload["decision"]
    blocked = hard.forbids_merge or decision in {"NOT_MERGEABLE", "BLOCKED", "NON_MATCH"}
    conclusion = "NON_MATCH" if decision == "NON_MATCH" or review.value == "NON_MATCH" else (
        "MATCH" if decision == "MERGEABLE" and not blocked else "UNDETERMINED")
    routing = "BLOCKED" if blocked else ("AUTO" if decision == "MERGEABLE" else
               "DEFERRED" if decision in {"DEFERRED", "INSUFFICIENT_EVIDENCE"} else "HUMAN_REVIEW")
    evidence = PairEvidence(tuple(payload.get("field_evidence", ())), payload.get("score", 0),
                            payload.get("comparisons", {}))
    evaluation = PairEvaluation(conclusion, routing, evidence, hard)
    allowed = not blocked and not review.vetoes_merge
    eligibility = MergeEligibility(allowed, allowed and decision == "MERGEABLE"
                                   and review.value == "UNDECIDED", hard.violations)
    return payload, evaluation, eligibility


def persisted_eligibility(pair):
    return adapt_evaluation({"decision": pair.pair_decision},
                            review=ReviewDecision(pair.user_decision))[2]


def load_cannot_links(db, run_id):
    from masterone.mdm.models import ResolutionNegativeEdge, ResolutionPair
    edges = db.query(ResolutionNegativeEdge).filter_by(run_id=run_id, active=True).all()
    # These legacy rows explicitly say NON_MATCH. REJECTED is deliberately absent.
    legacy = db.query(ResolutionPair).filter_by(run_id=run_id, user_decision="NON_MATCH").all()
    return frozenset(tuple(sorted((p.left_record_id, p.right_record_id))) for p in [*edges, *legacy])


def require_manual_merge(db, run_id, members):
    """Re-evaluate even absent candidate edges; reviewer may override soft policy."""
    require_manual_merges(db, run_id, [members])


def require_manual_merges(db, run_id, member_groups):
    """Share run-wide evidence when several groups are checked in one preview."""
    if not enabled():
        return
    from fastapi import HTTPException

    from masterone.mdm.models import ResolutionPair, ResolutionRecord
    from masterone.mdm.pipeline.matching import compare_and_score_rule, value_frequencies
    rows = db.query(ResolutionRecord).filter_by(run_id=run_id).all()
    records = {r.id: {"id": r.id, "entity_type": r.entity_type, "raw_fields": r.raw_fields,
                     "normalized_fields": r.normalized_fields, "quality_flags": r.quality_flags}
               for r in rows}
    frequencies = value_frequencies(list(records.values()))
    links = load_cannot_links(db, run_id)
    saved = {tuple(sorted((p.left_record_id, p.right_record_id))): p for p in
             db.query(ResolutionPair).filter_by(run_id=run_id).all()}
    for members in member_groups:
        if not set(members) <= records.keys():
            raise HTTPException(409, "Missing merge members")
        for a, b in combinations(sorted(members), 2):
            review = ReviewDecision(saved[(a, b)].user_decision) if (a, b) in saved else ReviewDecision()
            result = compare_and_score_rule(records[a], records[b], set(), frequencies, links)
            _, _, eligibility = evaluate_prepared(result, records[a], records[b], review=review,
                                                  negative_pairs=links)
            if not eligibility.manual_allowed:
                raise HTTPException(409, "Merge forbidden by current constraints or saved review decision")
