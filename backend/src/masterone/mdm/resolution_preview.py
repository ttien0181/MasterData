"""Run-scoped, versioned dry run and idempotent merge execution."""

from collections.abc import Iterable
from datetime import timedelta
from hashlib import sha256
from json import dumps

from fastapi import HTTPException
from sqlalchemy.orm import Query, Session

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
from masterone.mdm.pipeline.decision_semantics import persisted_eligibility
from masterone.mdm.resolution_items import ELIGIBLE, _choice_state, _options, create_export, export_readiness


def _active_proposals(db: Session, run_id: str) -> list[ResolutionProposal]:
    return db.query(ResolutionProposal).filter(
        ResolutionProposal.run_id == run_id,
        ResolutionProposal.status.in_(ELIGIBLE),
    ).order_by(ResolutionProposal.id).all()


def _pair_snapshot(db: Session, run_id: str) -> tuple[str, Query[ResolutionPair]]:
    from masterone.config import get_settings
    from masterone.mdm.export_stream import batches
    query = db.query(ResolutionPair).filter_by(run_id=run_id)
    fingerprint = sha256()
    fingerprint.update(b"[")
    first = True
    for pair in batches(query, get_settings().export_batch_rows):
        if not first:
            fingerprint.update(b",")
        fingerprint.update(dumps((pair.id, pair.revision, pair.user_decision),
                                 separators=(",", ":")).encode())
        first = False
    fingerprint.update(b"]")
    # A Query is re-iterable for guards/events without retaining the complete pair list.
    return fingerprint.hexdigest(), query.order_by(ResolutionPair.id).yield_per(get_settings().export_batch_rows)


def _guard(proposals: list[ResolutionProposal], pairs: Iterable[ResolutionPair]) -> dict[str, list[str]]:
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
            if not persisted_eligibility(pair).manual_allowed:
                blocked.setdefault(proposal_id, []).append(pair.id)
    return blocked


def record_merge_events(db: Session, preview: ResolutionPreview, snapshot: ResolutionExport,
                        proposals: list[ResolutionProposal], pairs: Iterable[ResolutionPair]) -> None:
    from masterone.config import get_settings
    from masterone.mdm.export_stream import index
    with index(get_settings()) as (state, budget, _):
        state.execute("CREATE TABLE edges(proposal TEXT, pair TEXT)")
        state.execute("CREATE INDEX edges_proposal ON edges(proposal)")
        for proposal in proposals:
            for rid in proposal.member_record_ids:
                state.execute("INSERT INTO owner VALUES (?,?)", (rid, proposal.id))
        for n, pair in enumerate(pairs):
            left = state.execute("SELECT proposal FROM owner WHERE id=?", (pair.left_record_id,)).fetchone()
            right = state.execute("SELECT proposal FROM owner WHERE id=?", (pair.right_record_id,)).fetchone()
            if left and left == right:
                if (pair.pair_decision in {"MERGEABLE", "MATCH_CANDIDATE"} or pair.user_decision == "ACCEPTED") and persisted_eligibility(pair).manual_allowed:
                    state.execute("INSERT INTO edges VALUES (?,?)", (left[0], pair.id))
            if n % get_settings().export_batch_rows == 0:
                budget.check()
        for proposal in proposals:
            records = {row.id: row for row in db.query(ResolutionRecord).filter(
                ResolutionRecord.run_id == preview.run_id,
                ResolutionRecord.id.in_(proposal.member_record_ids)).all()}
            ordered = sorted((records[record_id] for record_id in proposal.member_record_ids),
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
            from masterone.mdm.engine_adapter import EngineResultAdapter
            effective = EngineResultAdapter.golden(proposal.golden_fields)
            for field, value in effective.get("_golden_override", {}).items():
                golden[field] = {"value": value, "source_record_id": None, "provenance": "STEWARD_OVERRIDE"}
            db.add(ResolutionMergeEvent(
                run_id=preview.run_id, preview_id=preview.id, export_id=snapshot.id,
                actor_user_id=preview.actor_user_id, proposal_id=proposal.id,
                member_record_ids=proposal.member_record_ids, pair_ids=[pid for (pid,) in state.execute("SELECT pair FROM edges WHERE proposal=? ORDER BY pair", (proposal.id,))],
                golden_fields=golden, guard_results={"negative_edge": "PASS", "shared_value": "PASS"},
            ))
            db.flush()
            budget.check()


def prepare_preview(db: Session, run_id: str, actor_user_id: str,
                    choices: dict, acknowledge_unreviewed: bool) -> dict:
    if db.get(ResolutionRun, run_id).workflow_version == 2:
        from masterone.mdm.workflow_exports import prepare_preview as workflow_preview
        return workflow_preview(db, run_id, actor_user_id, choices, acknowledge_unreviewed)
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
    if db.get(ResolutionRun, run_id).workflow_version == 2:
        from masterone.mdm.workflow_exports import execute_preview as workflow_execute
        return workflow_execute(db, run_id, preview_id, request_id, actor_user_id)
    db.query(ResolutionRun).filter_by(id=run_id).with_for_update().one()
    preview = db.query(ResolutionPreview).filter_by(id=preview_id).populate_existing().first()
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
                             preview.choices, commit=False, export_id=preview.id)
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
