"""Run-scoped, versioned dry run and idempotent merge execution."""

from datetime import timedelta
from hashlib import sha256
from json import dumps

from fastapi import HTTPException
from sqlalchemy.orm import Session

from masterone.db.base import now_utc
from masterone.mdm.models import (
    ResolutionAuditEvent,
    ResolutionExport,
    ResolutionMergeEvent,
    ResolutionPair,
    ResolutionPreview,
    ResolutionProposal,
    ResolutionRecord,
    ResolutionRun,
)
from masterone.mdm.resolution_items import ELIGIBLE, _choice_state, _options, create_export, export_readiness


def _active_proposals(db: Session, run_id: str) -> list[ResolutionProposal]:
    return db.query(ResolutionProposal).filter(
        ResolutionProposal.run_id == run_id,
        ResolutionProposal.status.in_(ELIGIBLE),
    ).order_by(ResolutionProposal.id).all()


def _pair_snapshot(db: Session, run_id: str) -> tuple[str, list[ResolutionPair]]:
    pairs = db.query(ResolutionPair).filter_by(run_id=run_id).order_by(ResolutionPair.id).all()
    fingerprint = sha256(dumps([(pair.id, pair.revision, pair.user_decision)
                                 for pair in pairs], separators=(",", ":")).encode()).hexdigest()
    return fingerprint, pairs


def _guard(proposals: list[ResolutionProposal], pairs: list[ResolutionPair]) -> dict[str, list[str]]:
    owner = {}
    for proposal in proposals:
        for record_id in proposal.member_record_ids:
            if record_id in owner:
                raise HTTPException(409, "Các cụm gộp có bản ghi giao nhau")
            owner[record_id] = proposal.id
    blocked = {}
    for pair in pairs:
        proposal_id = owner.get(pair.left_record_id)
        if proposal_id and proposal_id == owner.get(pair.right_record_id):
            if (pair.pair_decision in {"NON_MATCH", "NOT_MERGEABLE"}
                    or pair.user_decision == "REJECTED"):
                blocked.setdefault(proposal_id, []).append(pair.id)
    return blocked


def record_merge_events(db: Session, preview: ResolutionPreview, snapshot: ResolutionExport,
                        proposals: list[ResolutionProposal], pairs: list[ResolutionPair]) -> None:
    members_by_proposal = {row.id: set(row.member_record_ids) for row in proposals}
    edge_ids = {row.id: [] for row in proposals}
    record_owner = {record_id: row.id for row in proposals for record_id in row.member_record_ids}
    for pair in pairs:
        owner = record_owner.get(pair.left_record_id)
        if owner and owner == record_owner.get(pair.right_record_id):
            if (pair.pair_decision in {"MERGEABLE", "MATCH_CANDIDATE"} or pair.user_decision == "ACCEPTED") and pair.user_decision != "REJECTED":
                edge_ids[owner].append(pair.id)
    records = {row.id: row for row in db.query(ResolutionRecord).filter_by(run_id=preview.run_id).all()}
    for proposal in proposals:
        ordered = sorted((records[record_id] for record_id in members_by_proposal[proposal.id]),
                         key=lambda row: (row.source_system, row.external_id, row.id))
        options = _options(ordered)
        selected, _ = _choice_state(options, proposal.origin, preview.choices.get(proposal.id))
        golden = {}
        for field in ("name", "phone", "email", "address"):
            candidates = options[field]
            source_id = selected.get(field) or (candidates[0]["source_record_id"] if candidates else None)
            if source_id:
                record = records[source_id]
                golden[field] = {"value": record.raw_fields.get(field, ""),
                                 "source_record_id": source_id, "source_system": record.source_system}
        db.add(ResolutionMergeEvent(
            run_id=preview.run_id, preview_id=preview.id, export_id=snapshot.id,
            actor_user_id=preview.actor_user_id, proposal_id=proposal.id,
            member_record_ids=proposal.member_record_ids, pair_ids=edge_ids[proposal.id],
            golden_fields=golden, guard_results={"negative_edge": "PASS", "shared_value": "PASS"},
        ))


def prepare_preview(db: Session, run_id: str, actor_user_id: str,
                    choices: dict, acknowledge_unreviewed: bool) -> dict:
    run = db.get(ResolutionRun, run_id)
    if run is None or run.status != "COMPLETED":
        raise HTTPException(409, "Lần nạp chưa xử lý xong")
    readiness = export_readiness(db, run_id, choices)
    if not readiness["can_export"]:
        raise HTTPException(409, "Còn giá trị chưa chọn hoặc cụm cần tách trước khi xem trước")
    if readiness["pending_review_count"] and not acknowledge_unreviewed:
        raise HTTPException(409, "Còn item cần review; cần xác nhận xuất các bản ghi đó riêng lẻ")
    proposals = _active_proposals(db, run_id)
    fingerprint, pairs = _pair_snapshot(db, run_id)
    blocked = _guard(proposals, pairs)
    if blocked:
        raise HTTPException(409, {"reason": "CLUSTER_GUARD_FAILED", "blocked_proposal_ids": sorted(blocked)})
    created_at = now_utc()
    preview = ResolutionPreview(
        run_id=run_id, actor_user_id=actor_user_id, policy_version=run.policy_version,
        pair_set_hash=fingerprint,
        proposal_revisions={row.id: row.revision for row in proposals},
        choices=choices, acknowledge_unreviewed=acknowledge_unreviewed,
        created_at=created_at, expires_at=created_at + timedelta(minutes=15),
    )
    db.add(preview)
    db.commit()
    return {"preview_id": preview.id, "status": preview.status,
            "eligible_count": len(proposals), "review_required_count": readiness["pending_review_count"],
            "separate_record_count": run.record_count - len({record_id for proposal in proposals
                                                              for record_id in proposal.member_record_ids}),
            "estimated_output_rows": run.record_count - sum(len(row.member_record_ids) - 1 for row in proposals),
            "policy_version": run.policy_version, "expires_at": preview.expires_at}


def execute_preview(db: Session, run_id: str, preview_id: str, request_id: str,
                    actor_user_id: str) -> ResolutionExport:
    preview = db.get(ResolutionPreview, preview_id)
    if preview is None or preview.run_id != run_id or preview.actor_user_id != actor_user_id:
        raise HTTPException(404, "Không tìm thấy bản xem trước")
    if preview.status == "EXECUTED":
        if preview.execute_request_id != request_id:
            raise HTTPException(409, "Bản xem trước đã được thực hiện")
        return db.get(ResolutionExport, preview.export_id)
    run = db.get(ResolutionRun, run_id)
    if (preview.status != "OPEN" or preview.expires_at.replace(tzinfo=None) < now_utc().replace(tzinfo=None)
            or run.policy_version != preview.policy_version):
        raise HTTPException(409, "Bản xem trước đã hết hạn hoặc chính sách đã thay đổi")
    proposals = _active_proposals(db, run_id)
    revisions = {row.id: row.revision for row in proposals}
    fingerprint, pairs = _pair_snapshot(db, run_id)
    if revisions != preview.proposal_revisions or fingerprint != preview.pair_set_hash:
        raise HTTPException(409, "Dữ liệu đã thay đổi; hãy tạo bản xem trước mới")
    blocked = _guard(proposals, pairs)
    if blocked:
        raise HTTPException(409, {"reason": "CLUSTER_GUARD_FAILED", "blocked_proposal_ids": sorted(blocked)})
    parent = db.query(ResolutionExport).filter_by(run_id=run_id).order_by(
        ResolutionExport.created_at.desc(), ResolutionExport.id.desc()).first()
    snapshot = create_export(db, run_id, actor_user_id, preview.acknowledge_unreviewed,
                             preview.choices, commit=False)
    snapshot.parent_export_id = parent.id if parent else None
    record_merge_events(db, preview, snapshot, proposals, pairs)
    preview.status = "EXECUTED"
    preview.execute_request_id = request_id
    preview.export_id = snapshot.id
    db.add(ResolutionAuditEvent(run_id=run_id, actor_user_id=actor_user_id,
                                action="PREVIEW_EXECUTED",
                                details={"preview_id": preview.id, "export_id": snapshot.id,
                                         "merged_group_count": len(proposals)}))
    db.commit()
    return snapshot
