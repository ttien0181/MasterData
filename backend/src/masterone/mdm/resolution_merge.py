"""Atomic multi-cluster merge confirmation, cancellation and history reads."""

from datetime import UTC, timedelta
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from masterone.db.base import now_utc
from masterone.mdm.models import (ResolutionEntity, ResolutionHistory, ResolutionPair,
                                  ResolutionFile, ResolutionPreview, ResolutionProposal, ResolutionRecord,
                                  ResolutionRequestKey)
from masterone.mdm.resolution_review import ELIGIBLE_PROPOSALS, canonical_request_hash


def save_preview(db: Session, run_id: str, owner_user_id: str, result: dict) -> None:
    db.add(ResolutionPreview(id=result["preview_token"], run_id=run_id, owner_user_id=owner_user_id,
                             snapshot=result["snapshot"], expires_at=now_utc() + timedelta(minutes=20)))
    db.commit()


def _field_values(records: list[ResolutionRecord], field: str) -> list[dict]:
    return [{"source_record_id": record.id, "source_system": record.source_system,
             "value": record.raw_fields.get(field, "")} for record in records
            if record.raw_fields.get(field, "").strip()]


def _resolve_fields(proposal: ResolutionProposal, choices: dict[str, str], records: list[ResolutionRecord]) -> dict:
    fields = {field: dict(value) for field, value in proposal.golden_fields["fields"].items()}
    allowed_fields = {"name", "phone", "email", "address"}
    if set(choices) - allowed_fields:
        raise HTTPException(422, "Có trường không thuộc Golden Record")
    for field in allowed_fields:
        options = _field_values(records, field)
        distinct = {item["value"] for item in options}
        source_id = choices.get(field)
        if len(distinct) < 2:
            if source_id:
                raise HTTPException(422, f"Trường {field} không có giá trị khác nhau để chọn")
            continue
        selected = next((item for item in options if item["source_record_id"] == source_id), None)
        if selected is None:
            raise HTTPException(422, f"Chọn nguồn hợp lệ cho trường {field}")
        fields[field] = {"value": selected["value"], "source_record_id": selected["source_record_id"],
                         "source_system": selected["source_system"]}
    return fields


def confirm_merge(db: Session, *, run_id: str, owner_user_id: str, preview_token: str,
                  idempotency_key: str, choices: dict[str, dict[str, str]]) -> dict:
    request_hash = canonical_request_hash(preview_token, choices)
    prior = db.query(ResolutionRequestKey).filter_by(run_id=run_id, owner_user_id=owner_user_id,
                                                     key=idempotency_key).one_or_none()
    if prior:
        if prior.request_hash != request_hash:
            raise HTTPException(409, "Idempotency key đã được dùng cho yêu cầu khác")
        return prior.result
    preview = db.get(ResolutionPreview, preview_token)
    if preview is None or preview.run_id != run_id or preview.owner_user_id != owner_user_id:
        raise HTTPException(404, "Preview không tồn tại")
    expires = preview.expires_at.replace(tzinfo=UTC) if preview.expires_at.tzinfo is None else preview.expires_at
    if preview.status != "OPEN" or expires < now_utc():
        raise HTTPException(409, "Preview đã hết hạn hoặc đã được xử lý")
    snapshots = {item["proposal_id"]: item["revision"] for item in preview.snapshot}
    proposals = db.query(ResolutionProposal).filter(ResolutionProposal.id.in_(snapshots)).all()
    if len(proposals) != len(snapshots):
        raise HTTPException(409, "Một số đề xuất không còn tồn tại")
    for proposal in proposals:
        if proposal.revision != snapshots[proposal.id] or proposal.status not in ELIGIBLE_PROPOSALS:
            raise HTTPException(409, "Một số đề xuất đã thay đổi; hãy xem trước lại")
    if set(choices) - set(snapshots):
        raise HTTPException(422, "Có lựa chọn thuộc cụm ngoài preview")
    occupied: set[str] = set()
    result_entities = []
    all_members = [record_id for proposal in proposals for record_id in proposal.member_record_ids]
    if len(all_members) != len(set(all_members)):
        raise HTTPException(409, "Các cụm trong preview đang chồng lấn")
    merged = {record_id for entity in db.query(ResolutionEntity).filter_by(run_id=run_id).all()
              for record_id in entity.member_record_ids}
    if set(all_members) & merged:
        raise HTTPException(409, "Một số bản ghi đã được gộp trước đó")
    for proposal in proposals:
        member_ids = set(proposal.member_record_ids)
        if occupied & member_ids:
            raise HTTPException(409, "Các cụm trong preview đang chồng lấn")
        occupied.update(member_ids)
        if any(pair.user_decision == "REJECTED" for pair in db.query(ResolutionPair).filter(
                ResolutionPair.run_id == run_id,
                ResolutionPair.left_record_id.in_(member_ids),
                ResolutionPair.right_record_id.in_(member_ids)).all()):
            raise HTTPException(409, "Có cặp đã bị từ chối trong cụm")
        records = db.query(ResolutionRecord).filter(ResolutionRecord.id.in_(member_ids)).all()
        if len(records) != len(member_ids):
            raise HTTPException(409, "Run thiếu bản ghi thành viên")
        resolved = _resolve_fields(proposal, choices.get(proposal.id, {}), records)
        entity = ResolutionEntity(run_id=run_id, proposal_id=proposal.id,
                                  member_record_ids=proposal.member_record_ids, fields=resolved)
        db.add(entity)
        db.flush()
        db.add(ResolutionHistory(run_id=run_id, proposal_id=proposal.id, entity_id=entity.id,
                                 action="MERGED", actor_user_id=owner_user_id,
                                 before_fields=proposal.golden_fields["fields"], after_fields=resolved,
                                 member_record_ids=proposal.member_record_ids))
        proposal.status = "MERGED"
        proposal.revision += 1
        result_entities.append({"entity_id": entity.id, "proposal_id": proposal.id,
                                "member_count": len(member_ids)})
    preview.status = "CONFIRMED"
    result = {"run_id": run_id, "entities": result_entities, "merged_count": len(result_entities)}
    db.add(ResolutionRequestKey(id=str(uuid4()), run_id=run_id, owner_user_id=owner_user_id,
                                key=idempotency_key, request_hash=request_hash, result=result))
    db.commit()
    return result


def cancel_preview(db: Session, run_id: str, owner_user_id: str, preview_token: str) -> dict:
    preview = db.get(ResolutionPreview, preview_token)
    if preview is None or preview.run_id != run_id or preview.owner_user_id != owner_user_id:
        raise HTTPException(404, "Preview không tồn tại")
    if preview.status != "OPEN":
        raise HTTPException(409, "Preview đã được xử lý")
    proposals = db.query(ResolutionProposal).filter(
        ResolutionProposal.id.in_([item["proposal_id"] for item in preview.snapshot])).all()
    for proposal in proposals:
        expected = next(item["revision"] for item in preview.snapshot if item["proposal_id"] == proposal.id)
        if proposal.revision != expected or proposal.status not in ELIGIBLE_PROPOSALS:
            raise HTTPException(409, "Một số đề xuất đã thay đổi; hãy tải lại")
        proposal.status = "REVIEW_REQUIRED"
        proposal.decision_reason = "CANCELLED_BY_USER"
        proposal.revision += 1
        db.add(ResolutionHistory(run_id=run_id, proposal_id=proposal.id, action="CANCELLED",
                                 actor_user_id=owner_user_id, before_fields=proposal.golden_fields["fields"],
                                 after_fields=proposal.golden_fields["fields"],
                                 member_record_ids=proposal.member_record_ids))
    preview.status = "CANCELLED"
    db.commit()
    return {"run_id": run_id, "cancelled_count": len(proposals), "review_section": "USER_DEFERRED"}


def history_page(db: Session, run_id: str, limit: int, offset: int) -> dict:
    query = db.query(ResolutionHistory).filter_by(run_id=run_id)
    total = query.count()
    items = query.order_by(ResolutionHistory.created_at.desc(), ResolutionHistory.id).offset(offset).limit(limit).all()
    return {"total": total, "items": [{"id": item.id, "proposal_id": item.proposal_id,
                "entity_id": item.entity_id, "action": item.action, "actor_user_id": item.actor_user_id,
                "created_at": item.created_at, "member_count": len(item.member_record_ids)} for item in items]}


def history_detail(db: Session, event_id: str) -> dict:
    item = db.get(ResolutionHistory, event_id)
    if item is None:
        raise HTTPException(404, "History event not found")
    records = db.query(ResolutionRecord).filter(ResolutionRecord.id.in_(item.member_record_ids)).all()
    file_ids = {row.file_id for row in records}
    files = {row.id: row.filename for row in db.query(ResolutionFile).filter(ResolutionFile.id.in_(file_ids)).all()}
    return {"id": item.id, "run_id": item.run_id, "proposal_id": item.proposal_id,
            "entity_id": item.entity_id, "action": item.action, "actor_user_id": item.actor_user_id,
            "created_at": item.created_at, "before_fields": item.before_fields,
            "after_fields": item.after_fields, "records": [{"id": row.id,
            "external_id": row.external_id, "source_system": row.source_system,
            "file_name": files.get(row.file_id, ""), "raw_fields": row.raw_fields} for row in records]}
