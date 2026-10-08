"""Revision-checked review decisions and merge preview construction."""

from hashlib import sha256
import json
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from masterone.db.base import now_utc
from masterone.mdm.models import (ResolutionPair, ResolutionProposal,
                                  ResolutionAuditEvent, ResolutionRecord, ResolutionReviewCase, ResolutionRun)
from masterone.mdm.pipeline.golden import build_golden

ELIGIBLE_PROPOSALS = {"AUTO_READY", "REVIEW_APPROVED"}


def decide_pair(db: Session, pair: ResolutionPair, decision: str, expected_revision: int,
                actor_user_id: str) -> dict:
    if pair.revision != expected_revision:
        raise HTTPException(409, "Cặp đã thay đổi; hãy tải lại trước khi quyết định")
    if pair.pair_decision in {"NOT_MERGEABLE", "NON_MATCH"} and decision == "ACCEPTED":
        raise HTTPException(409, "Quyết định NOT_MERGEABLE của thuật toán cần xử lý ngoài demo này")
    if pair.user_decision != "UNDECIDED" and pair.user_decision != "DEFERRED":
        raise HTTPException(409, "Quyết định cặp đã được chốt")
    previous = pair.user_decision
    pair.user_decision = decision
    pair.revision += 1
    if decision == "REJECTED":
        proposals = db.query(ResolutionProposal).filter_by(run_id=pair.run_id).all()
        for proposal in proposals:
            members = set(proposal.member_record_ids)
            if pair.left_record_id in members and pair.right_record_id in members and proposal.status in ELIGIBLE_PROPOSALS:
                proposal.status = "BLOCKED"
                proposal.decision_reason = "USER_CANNOT_LINK"
                proposal.revision += 1
        for case in db.query(ResolutionReviewCase).filter_by(run_id=pair.run_id, status="OPEN").all():
            if pair.id in case.pair_ids:
                case.status = "REJECTED"
                case.revision += 1
    db.add(ResolutionAuditEvent(run_id=pair.run_id, actor_user_id=actor_user_id, pair_id=pair.id,
        action="PAIR_" + decision, details={"previous": previous, "decision": decision,
        "expected_revision": expected_revision, "revision": pair.revision}))
    db.commit()
    return {"pair_id": pair.id, "user_decision": pair.user_decision, "revision": pair.revision}


def _requires_four_eyes(case: ResolutionReviewCase) -> bool:
    """Group D cases require two independent reviewers (§SVC-02-FR-006)."""
    return case.primary_group == "D"


def submit_first_review(db: Session, case: ResolutionReviewCase, decision: str,
                        accepted_pair_ids: list[str], actor_user_id: str,
                        expected_revision: int) -> dict:
    """Record first reviewer's decision for Group D; transitions to PENDING_SECOND_REVIEW."""
    if case.revision != expected_revision or case.status != "OPEN":
        raise HTTPException(409, "Nhóm review đã thay đổi; hãy tải lại")
    if not _requires_four_eyes(case):
        raise HTTPException(422, "submit_first_review chỉ dùng cho Group D")
    case.first_reviewer_id = actor_user_id
    case.first_decision = decision
    case.first_decided_at = now_utc()
    case.status = "PENDING_SECOND_REVIEW"
    case.revision += 1
    db.add(ResolutionAuditEvent(run_id=case.run_id, actor_user_id=actor_user_id,
        action="REVIEW_CASE_FIRST_DECISION",
        details={"case_id": case.id, "decision": decision,
                 "accepted_pair_ids": sorted(accepted_pair_ids), "revision": case.revision}))
    db.commit()
    return {"case_id": case.id, "status": case.status, "revision": case.revision,
            "message": "Đang chờ reviewer thứ 2 (four-eyes Group D)"}


def approve_case(db: Session, case: ResolutionReviewCase, expected_revision: int,
                 accepted_pair_ids: list[str], actor_user_id: str) -> dict:
    # Four-eyes guard: Group D first submission goes through submit_first_review instead
    if _requires_four_eyes(case) and case.status == "OPEN":
        return submit_first_review(db, case, "APPROVED", accepted_pair_ids,
                                   actor_user_id, expected_revision)

    # Second reviewer for Group D must be different from the first
    if _requires_four_eyes(case) and case.status == "PENDING_SECOND_REVIEW":
        if case.first_reviewer_id == actor_user_id:
            raise HTTPException(403, "Reviewer 2 phải khác Reviewer 1 (four-eyes Group D)")
        if case.first_decision != "APPROVED":
            raise HTTPException(409, "Reviewer 1 đã từ chối — không thể approve")
        case.second_reviewer_id = actor_user_id
        case.second_decision = "APPROVED"
        case.second_decided_at = now_utc()
        case.status = "OPEN"   # re-open to proceed with full approval below
        case.revision += 1
        db.add(ResolutionAuditEvent(run_id=case.run_id, actor_user_id=actor_user_id,
            action="REVIEW_CASE_SECOND_APPROVED",
            details={"case_id": case.id, "revision": case.revision}))
        db.commit()

    if case.revision != expected_revision or case.status != "OPEN":
        raise HTTPException(409, "Nhóm review đã thay đổi; hãy tải lại")
    pairs_by_id = {pair.id: pair for pair in db.query(ResolutionPair).filter_by(run_id=case.run_id).all()}
    if not set(case.pair_ids) <= set(accepted_pair_ids):
        raise HTTPException(422, "Cần xác nhận các cặp trong trường hợp review")
    member_ids = set(case.member_record_ids)
    proposals = db.query(ResolutionProposal).filter_by(run_id=case.run_id).all()
    changed = True
    while changed:
        changed = False
        for proposal in proposals:
            proposal_members = set(proposal.member_record_ids)
            if proposal.status in ELIGIBLE_PROPOSALS and member_ids & proposal_members and not proposal_members <= member_ids:
                member_ids.update(proposal_members)
                changed = True
    involved_pairs = [pair for pair in pairs_by_id.values()
                      if pair.left_record_id in member_ids and pair.right_record_id in member_ids]
    if any(pair.pair_decision in {"NOT_MERGEABLE", "NON_MATCH"} or pair.user_decision == "REJECTED" for pair in involved_pairs):
        raise HTTPException(409, "Có cặp xung đột hoặc đã bị từ chối trong cụm")
    review_pair_ids = {pair.id for pair in involved_pairs if pair.triage_status == "REVIEW"}
    if not review_pair_ids <= set(accepted_pair_ids):
        raise HTTPException(422, "Cần xác nhận tất cả cặp còn mơ hồ trong cụm")
    if set(accepted_pair_ids) - {pair.id for pair in involved_pairs}:
        raise HTTPException(422, "Có cặp được gửi nằm ngoài cụm review")
    records = db.query(ResolutionRecord).filter(ResolutionRecord.id.in_(member_ids)).all()
    if len(records) != len(member_ids):
        raise HTTPException(409, "Run thiếu bản ghi thành viên")
    merged_members = set()
    if member_ids.intersection(merged_members):
        raise HTTPException(409, "Một số bản ghi trong review đã được gộp")
    for proposal in proposals:
        proposal_members = set(proposal.member_record_ids)
        if proposal.status in ELIGIBLE_PROPOSALS and proposal_members & member_ids:
            proposal.status = "SUPERSEDED"
            proposal.revision += 1
    for pair in involved_pairs:
        if pair.id in accepted_pair_ids:
            pair.user_decision = "ACCEPTED"
            pair.revision += 1
    run = db.get(ResolutionRun, case.run_id)
    key = f"{case.run_id}:{run.policy_version}:{case.id}:{','.join(sorted(member_ids))}:{case.revision + 1}"
    proposal_id = sha256(key.encode()).hexdigest()
    record_data = {item.id: {"id": item.id, "external_id": item.external_id,
                             "source_system": item.source_system, "raw_fields": item.raw_fields}
                   for item in records}
    proposal = ResolutionProposal(id=proposal_id, run_id=case.run_id,
                                  member_record_ids=sorted(member_ids),
                                  golden_fields=build_golden(sorted(member_ids), record_data),
                                  status="REVIEW_APPROVED", revision=1,
                                  decision_reason="USER_REVIEW_APPROVED")
    db.add(proposal)
    case.status = "APPROVED"
    case.revision += 1
    for other in db.query(ResolutionReviewCase).filter_by(run_id=case.run_id, status="OPEN").all():
        if other.id != case.id and member_ids.intersection(other.member_record_ids):
            other.status = "STALE"
            other.revision += 1
    db.add(ResolutionAuditEvent(run_id=case.run_id, actor_user_id=actor_user_id,
        proposal_id=proposal_id, action="REVIEW_CASE_APPROVED",
        details={"case_id": case.id, "accepted_pair_ids": sorted(accepted_pair_ids),
                 "member_record_ids": sorted(member_ids), "revision": proposal.revision}))
    db.commit()
    return {"proposal_id": proposal.id, "status": proposal.status,
            "member_count": len(member_ids), "revision": proposal.revision}


def approve_deferred_proposal(db: Session, proposal: ResolutionProposal, expected_revision: int,
                              actor_user_id: str) -> dict:
    if proposal.status != "REVIEW_REQUIRED" or proposal.revision != expected_revision:
        raise HTTPException(409, "Đề xuất đã thay đổi; hãy tải lại")
    members = set(proposal.member_record_ids)
    pairs = db.query(ResolutionPair).filter(ResolutionPair.run_id == proposal.run_id,
        ResolutionPair.left_record_id.in_(members), ResolutionPair.right_record_id.in_(members)).all()
    if any(pair.pair_decision in {"NOT_MERGEABLE", "NON_MATCH"} or pair.user_decision == "REJECTED" for pair in pairs):
        raise HTTPException(409, "Cụm có cặp xung đột hoặc đã bị từ chối")
    if any(pair.pair_decision == "REVIEW" and pair.user_decision != "ACCEPTED" for pair in pairs):
        raise HTTPException(409, "Cần xử lý các cặp còn mơ hồ trước")
    records = db.query(ResolutionRecord).filter(ResolutionRecord.id.in_(members)).all()
    if len(records) != len(members):
        raise HTTPException(409, "Run thiếu bản ghi thành viên")
    merged_members = set()
    if members.intersection(merged_members):
        raise HTTPException(409, "Một số bản ghi trong cụm đã được gộp")
    for other in db.query(ResolutionProposal).filter_by(run_id=proposal.run_id).all():
        if other.id != proposal.id and other.status in ELIGIBLE_PROPOSALS and members.intersection(other.member_record_ids):
            raise HTTPException(409, "Cụm chồng lấn với một đề xuất khác")
    record_data = {item.id: {"id": item.id, "external_id": item.external_id,
                             "source_system": item.source_system, "raw_fields": item.raw_fields}
                   for item in records}
    proposal.golden_fields = build_golden(sorted(members), record_data)
    proposal.status = "REVIEW_APPROVED"
    proposal.decision_reason = "USER_REVIEW_APPROVED"
    proposal.revision += 1
    db.add(ResolutionAuditEvent(run_id=proposal.run_id, actor_user_id=actor_user_id,
        proposal_id=proposal.id, action="DEFERRED_PROPOSAL_APPROVED",
        details={"previous_status": "REVIEW_REQUIRED", "revision": proposal.revision,
                 "member_record_ids": sorted(members)}))
    db.commit()
    return {"proposal_id": proposal.id, "status": proposal.status,
            "member_count": len(members), "revision": proposal.revision}


def _preview_item(db: Session, proposal: ResolutionProposal) -> dict:
    records = db.query(ResolutionRecord).filter(ResolutionRecord.id.in_(proposal.member_record_ids)).all()
    by_id = {item.id: item for item in records}
    options = {}
    for field in ("name", "phone", "email", "address"):
        options[field] = [{"source_record_id": item.id, "source_system": item.source_system,
                           "value": item.raw_fields.get(field, "")} for item in records
                          if item.raw_fields.get(field, "").strip()]
    return {"id": proposal.id, "status": proposal.status, "revision": proposal.revision,
            "member_record_ids": proposal.member_record_ids,
            "records": [{"id": item.id, "external_id": item.external_id,
                         "source_system": item.source_system, "raw_fields": item.raw_fields}
                        for item in records],
            "golden_fields": proposal.golden_fields, "field_options": options}


def build_merge_preview(db: Session, run_id: str, owner_user_id: str,
                        proposal_ids: list[str] | None, selection_scope: str | None) -> dict:
    if bool(proposal_ids) == bool(selection_scope):
        raise HTTPException(422, "Gửi proposal_ids hoặc selection_scope=all_eligible")
    query = db.query(ResolutionProposal).filter(ResolutionProposal.run_id == run_id,
                                                ResolutionProposal.status.in_(ELIGIBLE_PROPOSALS))
    if proposal_ids:
        proposals = query.filter(ResolutionProposal.id.in_(proposal_ids)).all()
        if len(proposals) != len(set(proposal_ids)):
            raise HTTPException(409, "Một số đề xuất không còn đủ điều kiện")
    else:
        proposals = query.order_by(ResolutionProposal.id).all()
    if not proposals:
        raise HTTPException(422, "Không có cụm đủ điều kiện để xem trước")
    seen: set[str] = set()
    for proposal in proposals:
        members = set(proposal.member_record_ids)
        if seen & members:
            raise HTTPException(409, "Các cụm được chọn đang chồng lấn")
        seen.update(members)
    merged_members = set()
    if seen & merged_members:
        raise HTTPException(409, "Một số bản ghi đã được gộp trước đó")
    token = str(uuid4())
    snapshot = [{"proposal_id": item.id, "revision": item.revision} for item in proposals]
    return {"preview_token": token, "snapshot": snapshot, "proposals": [_preview_item(db, item) for item in proposals]}


def canonical_request_hash(preview_token: str, choices: dict) -> str:
    payload = json.dumps({"preview_token": preview_token, "choices": choices}, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode()).hexdigest()
