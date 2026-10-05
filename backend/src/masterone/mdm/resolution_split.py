"""Selectively separate one source record while retaining later valid merges."""


def split_proposal(all_members: list[str], to_split_out: list[str]) -> dict:
    """Pure utility: partition all_members into remaining and split sets (§24).

    Returns {"remaining": [...], "split": [...]} with both sorted.
    to_split_out members are removed from remaining; other members stay.
    """
    split_set = set(to_split_out)
    remaining = sorted(m for m in all_members if m not in split_set)
    split     = sorted(m for m in all_members if m in split_set)
    return {"remaining": remaining, "split": split}

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
    ResolutionReviewCase,
    ResolutionRun,
    ResolutionSplitEvent,
)
from masterone.mdm.pipeline.golden import build_golden
from masterone.mdm.resolution_items import ELIGIBLE, _options, create_export
from masterone.mdm.resolution_preview import _active_proposals, _guard, _pair_snapshot, record_merge_events


def list_merge_events(db: Session, run_id: str, export_id: str, offset: int, limit: int,
                      search: str = "") -> dict:
    snapshot = db.get(ResolutionExport, export_id)
    if snapshot is None or snapshot.run_id != run_id:
        raise HTTPException(404, "Không tìm thấy bản xuất")
    query = db.query(ResolutionMergeEvent).filter_by(run_id=run_id, export_id=export_id)
    if search.strip():
        term = search.strip().casefold()
        matching = [row for row in query.order_by(ResolutionMergeEvent.id).all()
                    if term in row.golden_fields.get("name", {}).get("value", "").casefold()]
        total, rows = len(matching), matching[offset:offset + limit]
    else:
        total = query.count()
        rows = query.order_by(ResolutionMergeEvent.id).offset(offset).limit(limit).all()
    record_ids = {record_id for row in rows for record_id in row.member_record_ids}
    records = {row.id: row for row in db.query(ResolutionRecord).filter(
        ResolutionRecord.run_id == run_id, ResolutionRecord.id.in_(record_ids)).all()} if record_ids else {}
    return {"total": total, "items": [{
        "event_id": event.id, "proposal_id": event.proposal_id,
        "member_count": len(event.member_record_ids),
        "name": event.golden_fields.get("name", {}).get("value", ""),
        "members": [{"id": record_id, "external_id": records[record_id].external_id,
                     "source_system": records[record_id].source_system,
                     "name": records[record_id].raw_fields.get("name", "")}
                    for record_id in event.member_record_ids],
    } for event in rows]}


def _current_split(db: Session, run_id: str, export_id: str, event_id: str, member_id: str):
    latest = db.query(ResolutionExport).filter_by(run_id=run_id).order_by(
        ResolutionExport.created_at.desc(), ResolutionExport.id.desc()).first()
    if latest is None or latest.id != export_id:
        raise HTTPException(409, "Bản xuất đã thay đổi; hãy mở bản mới nhất")
    event = db.get(ResolutionMergeEvent, event_id)
    if event is None or event.run_id != run_id or event.export_id != export_id:
        raise HTTPException(404, "Không tìm thấy nhóm gộp trong bản xuất")
    proposal = db.get(ResolutionProposal, event.proposal_id)
    if (proposal is None or proposal.status not in ELIGIBLE or proposal.run_id != run_id
            or set(proposal.member_record_ids) != set(event.member_record_ids)):
        raise HTTPException(409, "Nhóm gộp đã thay đổi")
    if member_id not in proposal.member_record_ids or len(proposal.member_record_ids) < 2:
        raise HTTPException(422, "Chọn một bản ghi trong nhóm có ít nhất hai bản ghi")
    proposals = _active_proposals(db, run_id)
    fingerprint, pairs = _pair_snapshot(db, run_id)
    version = sha256(dumps({"export": export_id, "event": event_id, "member": member_id,
                            "pair_hash": fingerprint,
                            "proposals": {row.id: row.revision for row in proposals}},
                           sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return latest, event, proposal, proposals, pairs, version


def prepare_split(db: Session, run_id: str, export_id: str, event_id: str, member_id: str) -> dict:
    snapshot, event, proposal, _, _, version = _current_split(db, run_id, export_id, event_id, member_id)
    records = {row.id: row for row in db.query(ResolutionRecord).filter(
        ResolutionRecord.run_id == run_id,
        ResolutionRecord.id.in_(proposal.member_record_ids)).all()}
    removed = records[member_id]
    retained = [records[record_id] for record_id in proposal.member_record_ids if record_id != member_id]
    return {"version": version, "source_export_id": snapshot.id, "event_id": event.id,
            "separated": {"id": removed.id, "external_id": removed.external_id,
                          "name": removed.raw_fields.get("name", "")},
            "retained": [{"id": row.id, "external_id": row.external_id,
                          "name": row.raw_fields.get("name", "")} for row in retained],
            "estimated_output_rows": snapshot.row_count + 1}


def _saved_choices(db: Session, export_id: str, proposals: list[ResolutionProposal],
                   residual: ResolutionProposal | None, split_event: ResolutionMergeEvent) -> dict:
    previous = {row.proposal_id: row for row in db.query(ResolutionMergeEvent).filter_by(export_id=export_id).all()}
    records = {row.id: row for row in db.query(ResolutionRecord).filter(
        ResolutionRecord.id.in_({record_id for proposal in proposals for record_id in proposal.member_record_ids})).all()}
    choices = {}
    for proposal in proposals:
        source = split_event if residual is not None and proposal.id == residual.id else previous.get(proposal.id)
        if source is None:
            raise HTTPException(409, "Thiếu dấu vết lựa chọn trường của bản xuất trước")
        ordered = sorted((records[record_id] for record_id in proposal.member_record_ids),
                         key=lambda row: (row.source_system, row.external_id, row.id))
        options = _options(ordered)
        selected = {}
        for field, value in source.golden_fields.items():
            source_id = value.get("source_record_id")
            if len({entry["value"] for entry in options[field]}) > 1 and source_id in proposal.member_record_ids:
                selected[field] = source_id
        choices[proposal.id] = selected
    return choices


def execute_split(db: Session, run_id: str, export_id: str, event_id: str,
                  member_id: str, version: str, request_id: str, actor_user_id: str) -> ResolutionExport:
    existing = db.query(ResolutionSplitEvent).filter_by(run_id=run_id, request_id=request_id).one_or_none()
    if existing is not None:
        requested_event = db.get(ResolutionMergeEvent, event_id)
        if (existing.before_export_id, existing.proposal_id, existing.separated_record_id) != (
                export_id, requested_event.proposal_id if requested_event else None, member_id):
            raise HTTPException(409, "Mã yêu cầu đã được sử dụng cho lần tách khác")
        return db.get(ResolutionExport, existing.after_export_id)
    snapshot, event, proposal, _, pairs, current_version = _current_split(
        db, run_id, export_id, event_id, member_id)
    if version != current_version:
        raise HTTPException(409, "Bản xem trước tách đã cũ; hãy xem lại")
    retained = sorted(record_id for record_id in proposal.member_record_ids if record_id != member_id)
    negative_pair_ids = []
    by_endpoints = {frozenset((pair.left_record_id, pair.right_record_id)): pair for pair in pairs}
    for other in retained:
        endpoints = frozenset((member_id, other))
        pair = by_endpoints.get(endpoints)
        if pair is None:
            left, right = sorted((member_id, other))
            pair = ResolutionPair(id=sha256(f"{run_id}:{left}:{right}:split".encode()).hexdigest(),
                                  run_id=run_id, left_record_id=left, right_record_id=right,
                                  blocking_reasons=["manual_split"], field_evidence=[], score=0.0,
                                  pair_decision="NOT_MERGEABLE", decision_reason="MANUAL_SPLIT",
                                  triage_status="NOT_MERGEABLE", triage_reason="MANUAL_SPLIT",
                                  primary_group="D", subgroup="D_MANUAL_SPLIT", signature="MANUAL_SPLIT",
                                  normalization_tags=[], policy_id=None,
                                  policy_version=db.get(ResolutionRun, run_id).policy_version)
            db.add(pair)
        else:
            pair.user_decision = "REJECTED"
            pair.revision += 1
        negative_pair_ids.append(pair.id)
    proposal.status = "SUPERSEDED"
    proposal.revision += 1
    for case in db.query(ResolutionReviewCase).filter_by(run_id=run_id, status="OPEN").all():
        if member_id in case.member_record_ids and any(other in case.member_record_ids for other in retained):
            case.status = "REJECTED"
            case.revision += 1
    residual = None
    if len(retained) > 1:
        records = {row.id: row for row in db.query(ResolutionRecord).filter(
            ResolutionRecord.id.in_(retained)).all()}
        payload = {row.id: {"source_system": row.source_system, "external_id": row.external_id,
                            "raw_fields": row.raw_fields} for row in records.values()}
        residual = ResolutionProposal(
            id=sha256(f"{run_id}:split:{proposal.id}:{member_id}".encode()).hexdigest(), run_id=run_id,
            member_record_ids=retained, golden_fields=build_golden(retained, payload),
            status="AUTO_READY", origin="AUTO", decision_reason="SELECTIVE_UNMERGE")
        db.add(residual)
    db.flush()
    proposals = _active_proposals(db, run_id)
    choices = _saved_choices(db, export_id, proposals, residual, event)
    _, updated_pairs = _pair_snapshot(db, run_id)
    if _guard(proposals, updated_pairs):
        raise HTTPException(409, "Có cặp không được gộp trong cụm còn lại")
    result = create_export(db, run_id, actor_user_id, True, choices, commit=False)
    result.parent_export_id = snapshot.id
    run = db.get(ResolutionRun, run_id)
    preview = ResolutionPreview(run_id=run_id, actor_user_id=actor_user_id,
                                policy_version=run.policy_version,
                                pair_set_hash=_pair_snapshot(db, run_id)[0],
                                proposal_revisions={row.id: row.revision for row in proposals},
                                choices=choices, acknowledge_unreviewed=True,
                                status="EXECUTED", execute_request_id=request_id,
                                export_id=result.id, created_at=now_utc(), expires_at=now_utc())
    db.add(preview)
    db.flush()
    record_merge_events(db, preview, result, proposals, updated_pairs)
    db.add(ResolutionSplitEvent(run_id=run_id, before_export_id=snapshot.id, after_export_id=result.id,
                                actor_user_id=actor_user_id, request_id=request_id,
                                proposal_id=proposal.id, separated_record_id=member_id,
                                retained_record_ids=retained, negative_pair_ids=negative_pair_ids))
    db.add(ResolutionAuditEvent(run_id=run_id, actor_user_id=actor_user_id, proposal_id=proposal.id,
                                action="ENTITY_SPLIT", details={"before_export_id": snapshot.id,
                                "after_export_id": result.id, "separated_record_id": member_id,
                                "negative_pair_ids": negative_pair_ids}))
    db.commit()
    return result
