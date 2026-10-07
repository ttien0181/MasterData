"""Pair-level review using the existing proposals, constraints and audit trail."""

from hashlib import sha256

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from masterone.mdm.models import (
    ResolutionAuditEvent,
    ResolutionPair,
    ResolutionProposal,
    ResolutionReviewCase,
    ResolutionRun,
)
from masterone.mdm.pipeline.golden import build_golden
from masterone.mdm.resolution_items import ELIGIBLE, _internal_pairs, _is_blocked, _records, _split_blocked_proposal


def pending_pairs(db: Session, run_id: str):
    return db.query(ResolutionPair).filter(
        ResolutionPair.run_id == run_id, ResolutionPair.triage_status == "REVIEW",
        ResolutionPair.user_decision.in_(["UNDECIDED", "DEFERRED"]),
        ~ResolutionPair.pair_decision.in_(["NON_MATCH", "NOT_MERGEABLE"]),
    )


def pair_categories(db: Session, run_id: str):
    return [{"primary_group": group or "OTHER", "subgroup": subgroup or "OTHER", "pair_count": count}
            for group, subgroup, count in pending_pairs(db, run_id).with_entities(
                ResolutionPair.primary_group, ResolutionPair.subgroup, func.count(ResolutionPair.id)
            ).group_by(ResolutionPair.primary_group, ResolutionPair.subgroup)
            .order_by(ResolutionPair.primary_group, ResolutionPair.subgroup).all()]


def decide_review_pair(db: Session, run_id: str, pair_id: str, decision: str, revision: int, actor: str):
    # Serialize decisions within this run so two reviewers cannot create overlapping proposals.
    run = db.query(ResolutionRun).filter_by(id=run_id).with_for_update().one()
    pair = db.query(ResolutionPair).filter_by(run_id=run_id, id=pair_id).with_for_update().one_or_none()
    if pair is None:
        raise HTTPException(404, "Không tìm thấy cặp bản ghi")
    if (pair.revision != revision or pair.user_decision not in {"UNDECIDED", "DEFERRED"}
            or pair.triage_status != "REVIEW" or _is_blocked(pair)):
        raise HTTPException(409, "Cặp đã thay đổi hoặc đã có quyết định; hãy tải lại")
    if run.status != "COMPLETED":
        raise HTTPException(409, "Chỉ xác nhận khi phiên đã xử lý xong")
    proposals = db.query(ResolutionProposal).filter(
        ResolutionProposal.run_id == run_id, ResolutionProposal.status.in_(ELIGIBLE)).all()
    members = {pair.left_record_id, pair.right_record_id}
    proposal_id = None
    if decision == "merge":
        changed = True
        while changed:
            changed = False
            for proposal in proposals:
                group = set(proposal.member_record_ids)
                if group & members and not group <= members:
                    members.update(group)
                    changed = True
        if any(_is_blocked(other) for other in _internal_pairs(db, run_id, members)):
            raise HTTPException(409, "Không thể gộp: nhóm liên quan có cặp đã xác định Không gộp")
        staged = db.query(ResolutionProposal).filter_by(run_id=run_id, status="REVIEW_STAGED_CONFLICT").all()
        if any(members & set(proposal.member_record_ids) for proposal in staged):
            raise HTTPException(409, "Cần xử lý cụm xung đột liên quan trước khi xác nhận cặp")
        rows = _records(db, members)
        payload = {row.id: {"id": row.id, "external_id": row.external_id,
                            "source_system": row.source_system, "raw_fields": row.raw_fields} for row in rows}
        proposal_id = sha256(f"{run_id}:pair:{pair.id}:{revision}".encode()).hexdigest()
        for proposal in proposals:
            if members & set(proposal.member_record_ids):
                proposal.status = "SUPERSEDED"
                proposal.revision += 1
        db.add(ResolutionProposal(id=proposal_id, run_id=run_id, member_record_ids=sorted(members),
            golden_fields=build_golden(sorted(members), payload), status="REVIEW_APPROVED",
            origin="REVIEW", decision_reason="USER_PAIR_APPROVED"))
    pair.user_decision = "ACCEPTED" if decision == "merge" else "REJECTED"
    pair.revision += 1
    if decision == "no_merge":
        for proposal in proposals:
            if members <= set(proposal.member_record_ids):
                _split_blocked_proposal(db, proposal, [pair], actor)
    # Remove only this decided edge from each open case. Its other edges remain reviewable.
    for case in db.query(ResolutionReviewCase).filter_by(run_id=run_id, status="OPEN").all():
        if pair.id not in case.pair_ids:
            continue
        remaining = db.query(ResolutionPair).filter(
            ResolutionPair.run_id == run_id, ResolutionPair.id.in_(case.pair_ids),
            ResolutionPair.user_decision.in_(["UNDECIDED", "DEFERRED"])).all()
        if remaining:
            case.pair_ids = [other.id for other in remaining]
            case.member_record_ids = sorted({record_id for other in remaining
                                            for record_id in (other.left_record_id, other.right_record_id)})
        else:
            case.status = "APPROVED" if decision == "merge" else "REJECTED"
        case.revision += 1
    db.flush()
    db.add(ResolutionAuditEvent(run_id=run_id, actor_user_id=actor, pair_id=pair.id,
        proposal_id=proposal_id, action="PAIR_ACCEPTED" if decision == "merge" else "PAIR_REJECTED",
        details={"decision": decision, "revision": pair.revision, "member_record_ids": sorted(members)}))
    db.commit()
    return {"pair_id": pair.id, "decision": decision, "revision": pair.revision,
            "proposal_id": proposal_id, "affected_record_count": len(members)}
