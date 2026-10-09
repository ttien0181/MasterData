"""Retry-safe publication: existing serving identities and human state are retained.

This is conservative insert-only retry, not a new generation/recompute protocol.
Conflicting proposals require an explicit future reconciliation workflow.
"""
from masterone.mdm.models import (
    ResolutionPair,
    ResolutionProposal,
    ResolutionPublication,
    ResolutionReviewCase,
    ResolutionRun,
)


def persist_snapshot(db, run_id, computed, *, generation_id=None):
    from masterone.mdm.job_queue import guard
    guard(db, run_id)
    from masterone.mdm.engine_adapter import EngineResultAdapter
    adapter = EngineResultAdapter()
    # Same lock as active pair review: do not publish over a concurrent reviewer.
    db.query(ResolutionRun).filter_by(id=run_id).with_for_update().one()
    publication = db.get(ResolutionPublication, run_id)
    if publication is not None and generation_id is None:
        raise RuntimeError("Generation publication gate required for this run")
    pairs = db.query(ResolutionPair).filter_by(run_id=run_id).all()
    existing = {tuple(sorted((p.left_record_id, p.right_record_id))): p for p in pairs}
    id_map = {}
    for pair in computed["pairs"]:
        pair = adapter.pair(pair)
        key = tuple(sorted((pair["left_id"], pair["right_id"])))
        if key in existing:
            id_map[pair["id"]] = existing[key].id
            continue
        row = ResolutionPair(id=pair["id"], run_id=run_id,
            left_record_id=pair["left_id"], right_record_id=pair["right_id"],
            blocking_reasons=pair["blocking_reasons"], field_evidence=pair["field_evidence"],
            score=pair["score"], pair_decision=pair["decision"], decision_reason=pair["reason"],
            triage_status=pair["triage_status"], triage_reason=pair["triage_reason"],
            primary_group=pair["primary_group"], subgroup=pair["subgroup"],
            signature=pair["signature"], normalization_tags=pair["normalization_tags"],
            policy_id=pair["policy_id"], policy_version=pair["policy_version"])
        db.add(row)
        existing[key] = row
        id_map[pair["id"]] = row.id
    saved_proposals = db.query(ResolutionProposal).filter_by(run_id=run_id).all()
    proposal_ids = {p.id for p in saved_proposals}
    # Historical/rejected proposals also reserve their members during retry.
    reserved = {rid for p in saved_proposals for rid in p.member_record_ids}
    saved_cases = db.query(ResolutionReviewCase).filter_by(run_id=run_id).all()
    reserved.update(rid for c in saved_cases if c.status != "OPEN" or c.revision > 1
                    for rid in c.member_record_ids)
    reserved.update(rid for p in pairs if p.user_decision != "UNDECIDED"
                    for rid in (p.left_record_id, p.right_record_id))
    for proposal in computed["proposals"]:
        if proposal["id"] in proposal_ids or reserved.intersection(proposal["member_record_ids"]):
            continue
        db.add(ResolutionProposal(id=proposal["id"], run_id=run_id,
            member_record_ids=proposal["member_record_ids"], golden_fields=proposal["golden_fields"],
            status=proposal["status"], revision=proposal["revision"]))
        reserved.update(proposal["member_record_ids"])
    cases_by_id = {c.id: c for c in saved_cases}
    pairs_by_id = {p.id: p for p in existing.values()}
    for case in computed["review_cases"]:
        if case["id"] in cases_by_id:
            continue
        ids = [id_map[p] for p in case["pair_ids"] if p in id_map
               and pairs_by_id[id_map[p]].user_decision in {None, "UNDECIDED", "DEFERRED"}]
        if not ids:
            continue
        db.add(ResolutionReviewCase(id=case["id"], run_id=run_id, pair_ids=ids,
            member_record_ids=sorted({rid for p in ids for rid in
                (pairs_by_id[p].left_record_id, pairs_by_id[p].right_record_id)}),
            primary_group=case["primary_group"], subgroup=case["subgroup"],
            signature_hash=case["signature_hash"], triage_reason=case["triage_reason"]))

    guard(db, run_id)
