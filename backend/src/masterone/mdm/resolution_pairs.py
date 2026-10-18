"""Pair-level review using the existing proposals, constraints and audit trail."""

from hashlib import sha256

from fastapi import HTTPException
from sqlalchemy import func, update
from sqlalchemy.orm import Session

from masterone.mdm.models import (
    ResolutionAuditEvent,
    ResolutionPair,
    ResolutionProposal,
    ResolutionReviewCase,
    ResolutionRun,
    ResolutionRunSummary,
)
from masterone.mdm.pipeline.decision_semantics import require_manual_merge
from masterone.mdm.pipeline.golden import build_golden
from masterone.mdm.pipeline.golden_v2 import golden_payload, merge_existing_golden
from masterone.mdm.resolution_items import ELIGIBLE, _internal_pairs, _is_blocked, _records, _split_blocked_proposal


def pending_pairs(db: Session, run_id: str):
    return db.query(ResolutionPair).filter(
        ResolutionPair.run_id == run_id, ResolutionPair.triage_status == "REVIEW",
        ResolutionPair.user_decision.in_(["UNDECIDED", "DEFERRED"]),
        ResolutionPair.staged_for_merge.is_(False),
        ~ResolutionPair.pair_decision.in_(["NON_MATCH", "NOT_MERGEABLE"]),
    )


def _sync_staged_groups(db: Session, run_id: str,
                        submitted_choices: dict[str, dict[str, str]] | None = None) -> None:
    """Materialize connected staged pairs without running export guards."""
    from masterone.mdm.resolution_preview import _plan_staged_pairs

    plan = _plan_staged_pairs(db, run_id, validate=False)
    previous = db.query(ResolutionProposal).filter_by(
        run_id=run_id, status="STAGED_FOR_MERGE").all()
    covered = []
    for start in range(0, len(plan["superseded_ids"]), 500):
        covered.extend(db.query(ResolutionProposal).filter(
            ResolutionProposal.id.in_(plan["superseded_ids"][start:start + 500])).all())
    by_id = {row.id: row for row in previous}
    desired = {entry["id"] for entry in plan["proposals"]}
    for entry in plan["proposals"]:
        members = set(entry["member_record_ids"])
        selected: dict[str, str] = {}
        conflicts: set[str] = set()
        for old in previous:
            if not members.intersection(old.member_record_ids):
                continue
            for field, source_id in old.golden_fields.get("_selected_sources", {}).items():
                if source_id not in members:
                    continue
                if field in selected and selected[field] != source_id:
                    conflicts.add(field)
                else:
                    selected[field] = source_id
            conflicts.update(old.golden_fields.get("_choice_conflicts", []))
        if submitted_choices:
            from masterone.mdm.resolution_items import _options, _records, _valid_choices
            options = _options(_records(db, members))
            for old in covered:
                if not members.intersection(old.member_record_ids):
                    continue
                valid = _valid_choices(submitted_choices.get(old.id, {}), options)
                for field, source_id in valid.items():
                    if field in selected and selected[field] != source_id:
                        conflicts.add(field)
                    else:
                        selected[field] = source_id
        for field in conflicts:
            selected.pop(field, None)
        golden = entry["golden_fields"]
        golden["_covered_proposal_ids"] = [row.id for row in covered
                                            if members.intersection(row.member_record_ids)]
        golden["_selected_sources"] = selected
        if conflicts:
            golden["_choice_conflicts"] = sorted(conflicts)
        row = by_id.get(entry["id"])
        if row is None:
            db.add(ResolutionProposal(id=entry["id"], run_id=run_id,
                member_record_ids=entry["member_record_ids"], golden_fields=golden,
                status="STAGED_FOR_MERGE", origin="REVIEW",
                decision_reason="STAGED_PAIRS_PREVIEWED"))
        else:
            row.golden_fields = golden
            row.revision += 1
    for row in previous:
        if row.id not in desired:
            db.delete(row)
    db.flush()


def stage_review_pairs(db: Session, run_id: str, *, all_matching: bool,
                       pair_ids: list[str], excluded_ids: list[str],
                       primary_group: str | None, subgroup: str | None, actor: str,
                       choices: dict[str, dict[str, str]] | None = None):
    """Mark a filtered set without loading each page or building merge groups."""
    if all_matching == bool(pair_ids) or len(pair_ids) != len(set(pair_ids)):
        raise HTTPException(422, "Danh sách cặp được chọn không hợp lệ")
    query = pending_pairs(db, run_id)
    if all_matching:
        if primary_group:
            query = query.filter(ResolutionPair.primary_group.is_(None) if primary_group == "OTHER"
                                 else ResolutionPair.primary_group == primary_group)
        if subgroup:
            query = query.filter(ResolutionPair.subgroup.is_(None) if subgroup == "OTHER"
                                 else ResolutionPair.subgroup == subgroup)
        if excluded_ids:
            query = query.filter(~ResolutionPair.id.in_(excluded_ids))
    else:
        query = query.filter(ResolutionPair.id.in_(pair_ids))
    changed = query.update({ResolutionPair.staged_for_merge: True}, synchronize_session=False)
    if changed:
        _sync_staged_groups(db, run_id, choices)
        db.execute(update(ResolutionRunSummary).where(ResolutionRunSummary.run_id == run_id)
                   .values(version=ResolutionRunSummary.version + 1))
        db.add(ResolutionAuditEvent(run_id=run_id, actor_user_id=actor,
            action="PAIRS_STAGED_FOR_MERGE", details={"count": changed}))
    db.commit()
    return {"staged_count": changed}


def unstage_review_pair(db: Session, run_id: str, pair_id: str, actor: str):
    changed = db.query(ResolutionPair).filter_by(run_id=run_id, id=pair_id,
        staged_for_merge=True).update({ResolutionPair.staged_for_merge: False}, synchronize_session=False)
    if not changed:
        raise HTTPException(404, "Cặp không còn chờ kiểm tra")
    _sync_staged_groups(db, run_id)
    db.execute(update(ResolutionRunSummary).where(ResolutionRunSummary.run_id == run_id)
               .values(version=ResolutionRunSummary.version + 1))
    db.add(ResolutionAuditEvent(run_id=run_id, actor_user_id=actor,
        pair_id=pair_id, action="PAIR_UNSTAGED_FOR_MERGE", details={}))
    db.commit()
    return {"pair_id": pair_id}


def unstage_group(db: Session, run_id: str, proposal_id: str, actor: str):
    group = db.get(ResolutionProposal, proposal_id)
    if group is None or group.run_id != run_id or group.status != "STAGED_FOR_MERGE":
        raise HTTPException(404, "Nhóm không còn ở Chờ gộp")
    members = set(group.member_record_ids)
    changed = db.query(ResolutionPair).filter(
        ResolutionPair.run_id == run_id, ResolutionPair.staged_for_merge.is_(True),
        ResolutionPair.left_record_id.in_(members),
        ResolutionPair.right_record_id.in_(members)).update(
            {ResolutionPair.staged_for_merge: False}, synchronize_session=False)
    _sync_staged_groups(db, run_id)
    if changed:
        db.execute(update(ResolutionRunSummary).where(ResolutionRunSummary.run_id == run_id)
                   .values(version=ResolutionRunSummary.version + 1))
        db.add(ResolutionAuditEvent(run_id=run_id, actor_user_id=actor,
            proposal_id=proposal_id, action="PAIR_UNSTAGED_FOR_MERGE", details={"count": changed}))
    db.commit()
    return {"unstaged_count": changed}


def save_staged_group_choices(db: Session, run_id: str, proposal_id: str,
                              choices: dict[str, str], revision: int, actor: str):
    from masterone.mdm.resolution_items import _options, _records, _valid_choices

    group = db.get(ResolutionProposal, proposal_id)
    if group is None or group.run_id != run_id or group.status != "STAGED_FOR_MERGE":
        raise HTTPException(404, "Nhóm không còn ở Chờ gộp")
    if group.revision != revision:
        raise HTTPException(409, "Nhóm đã thay đổi; hãy tải lại")
    options = _options(_records(db, group.member_record_ids))
    if _valid_choices(choices, options) != choices:
        raise HTTPException(422, "Lựa chọn nguồn của nhóm không hợp lệ")
    golden = {**group.golden_fields, "_selected_sources": choices}
    golden.pop("_choice_conflicts", None)
    group.golden_fields = golden
    group.revision += 1
    db.execute(update(ResolutionRunSummary).where(ResolutionRunSummary.run_id == run_id)
               .values(version=ResolutionRunSummary.version + 1))
    db.add(ResolutionAuditEvent(run_id=run_id, actor_user_id=actor,
        proposal_id=proposal_id, action="STAGED_GROUP_CHOICES_SAVED", details={"fields": sorted(choices)}))
    db.commit()
    return {"revision": group.revision, "choices": choices}


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
        require_manual_merge(db, run_id, members)
        staged = db.query(ResolutionProposal).filter_by(run_id=run_id, status="REVIEW_STAGED_CONFLICT").all()
        if any(members & set(proposal.member_record_ids) for proposal in staged):
            raise HTTPException(409, "Cần xử lý cụm xung đột liên quan trước khi xác nhận cặp")
        rows = _records(db, members)
        payload = {row.id: golden_payload(row) for row in rows}
        proposal_id = sha256(f"{run_id}:pair:{pair.id}:{revision}".encode()).hexdigest()
        for proposal in proposals:
            if members & set(proposal.member_record_ids):
                proposal.status = "SUPERSEDED"
                proposal.revision += 1
        db.add(ResolutionProposal(id=proposal_id, run_id=run_id, member_record_ids=sorted(members),
            golden_fields=build_golden(sorted(members), payload,
                existing_golden=merge_existing_golden(proposals, members)), status="REVIEW_APPROVED",
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
