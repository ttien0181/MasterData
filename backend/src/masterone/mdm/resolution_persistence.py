"""Retry-safe publication: existing serving identities and human state are retained.

This is conservative insert-only retry, not a new generation/recompute protocol.
Conflicting proposals require an explicit future reconciliation workflow.
"""
from sqlalchemy import insert

from masterone.mdm.models import (
    ResolutionPair,
    ResolutionProposal,
    ResolutionPublication,
    ResolutionReviewCase,
    ResolutionRun,
)

BULK_ROWS = 5000


def _bulk_insert(db, model, rows):
    # Core executemany in batches. The ORM path built one identity-mapped object per
    # row and flushed them all at once; Core applies the same column defaults without that cost.
    for start in range(0, len(rows), BULK_ROWS):
        db.execute(insert(model), rows[start:start + BULK_ROWS])


def persist_snapshot(db, run_id, computed, *, generation_id=None):
    from masterone.mdm.job_queue import guard
    guard(db, run_id)
    from masterone.mdm.engine_adapter import EngineResultAdapter
    adapter = EngineResultAdapter()
    # Same lock as active pair review: do not publish over a concurrent reviewer.
    run = db.query(ResolutionRun).filter_by(id=run_id).with_for_update().one()
    publication = db.get(ResolutionPublication, run_id)
    if publication is not None and generation_id is None:
        raise RuntimeError("Generation publication gate required for this run")
    # Plain tuples rather than ORM objects: a large run would otherwise keep every stored pair in the session.
    stored = db.query(ResolutionPair.id, ResolutionPair.left_record_id, ResolutionPair.right_record_id,
                      ResolutionPair.user_decision).filter_by(run_id=run_id).all()
    id_by_key = {tuple(sorted((left, right))): pid for pid, left, right, _ in stored}
    # id -> (left_record_id, right_record_id, user_decision); user_decision is None for rows not yet flushed.
    pair_info = {pid: (left, right, decision) for pid, left, right, decision in stored}
    id_map = {}
    new_pairs = []
    for pair in computed["pairs"]:
        pair = adapter.pair(pair)
        key = tuple(sorted((pair["left_id"], pair["right_id"])))
        if key in id_by_key:
            id_map[pair["id"]] = id_by_key[key]
            continue
        new_pairs.append({
            "id": pair["id"], "run_id": run_id,
            "left_record_id": pair["left_id"], "right_record_id": pair["right_id"],
            "blocking_reasons": pair["blocking_reasons"], "field_evidence": pair["field_evidence"],
            "score": pair["score"], "pair_decision": pair["decision"], "decision_reason": pair["reason"],
            "triage_status": pair["triage_status"], "triage_reason": pair["triage_reason"],
            "primary_group": pair["primary_group"], "subgroup": pair["subgroup"],
            "signature": pair["signature"], "normalization_tags": pair["normalization_tags"],
            "policy_id": pair["policy_id"], "policy_version": pair["policy_version"],
        })
        id_by_key[key] = pair["id"]
        pair_info[pair["id"]] = (pair["left_id"], pair["right_id"], None)
        id_map[pair["id"]] = pair["id"]
    _bulk_insert(db, ResolutionPair, new_pairs)

    saved_proposals = db.query(ResolutionProposal.id, ResolutionProposal.member_record_ids).filter_by(run_id=run_id).all()
    proposal_ids = {pid for pid, _ in saved_proposals}
    # Historical/rejected proposals also reserve their members during retry.
    reserved = {rid for _, members in saved_proposals for rid in members}
    saved_cases = db.query(ResolutionReviewCase.id, ResolutionReviewCase.status, ResolutionReviewCase.revision,
                           ResolutionReviewCase.member_record_ids).filter_by(run_id=run_id).all()
    reserved.update(rid for _, status, revision, members in saved_cases
                    if status != "OPEN" or revision > 1 for rid in members)
    reserved.update(rid for _, left, right, decision in stored
                    if decision != "UNDECIDED" for rid in (left, right))
    new_proposals = []
    for proposal in computed["proposals"]:
        if proposal["id"] in proposal_ids or reserved.intersection(proposal["member_record_ids"]):
            continue
        new_proposals.append({
            "id": proposal["id"], "run_id": run_id, "member_record_ids": proposal["member_record_ids"],
            "golden_fields": proposal["golden_fields"], "status": proposal["status"],
            "revision": proposal["revision"],
        })
        reserved.update(proposal["member_record_ids"])
    _bulk_insert(db, ResolutionProposal, new_proposals)

    case_ids = {cid for cid, *_ in saved_cases}
    new_cases = []
    for case in computed["review_cases"]:
        if case["id"] in case_ids:
            continue
        ids = [id_map[p] for p in case["pair_ids"] if p in id_map
               and pair_info[id_map[p]][2] in {None, "UNDECIDED", "DEFERRED"}]
        if not ids:
            continue
        new_cases.append({
            "id": case["id"], "run_id": run_id, "pair_ids": ids,
            "member_record_ids": sorted({rid for p in ids for rid in (pair_info[p][0], pair_info[p][1])}),
            "primary_group": case["primary_group"], "subgroup": case["subgroup"],
            "signature_hash": case["signature_hash"], "triage_reason": case["triage_reason"],
        })
    _bulk_insert(db, ResolutionReviewCase, new_cases)

    from masterone.mdm.run_summary import refresh_payload
    refresh_payload(db, run)  # overview figures describe exactly the pairs written above
    guard(db, run_id)
