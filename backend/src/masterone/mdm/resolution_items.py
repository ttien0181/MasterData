"""Steward item workflow and complete, reproducible CSV snapshots."""

import csv
from hashlib import sha256
from io import StringIO

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from masterone.db.base import now_utc
from masterone.mdm.models import (
    ResolutionAuditEvent,
    ResolutionChoiceDraft,
    ResolutionEntity,
    ResolutionExport,
    ResolutionPair,
    ResolutionProposal,
    ResolutionRecord,
    ResolutionReviewCase,
    ResolutionRun,
)
from masterone.mdm.pipeline.golden import build_golden

FIELDS = ("name", "phone", "email", "address")
ELIGIBLE = ("AUTO_READY", "REVIEW_APPROVED")


def _hash_members(member_ids):
    return sha256("|".join(sorted(member_ids)).encode()).hexdigest()


def _records(db, member_ids):
    ids = list(member_ids)
    rows = []
    for start in range(0, len(ids), 500):
        rows.extend(db.query(ResolutionRecord).filter(ResolutionRecord.id.in_(ids[start:start + 500])).all())
    if len(rows) != len(set(ids)):
        raise HTTPException(409, "Thiếu bản ghi nguồn của item")
    return sorted(rows, key=lambda row: (row.source_system, row.external_id, row.id))


def _options(records):
    return {field: [{"source_record_id": row.id, "source_system": row.source_system,
                     "external_id": row.external_id, "value": str(row.raw_fields.get(field) or "").strip()}
                    for row in records if str(row.raw_fields.get(field) or "").strip()]
            for field in FIELDS}


def _valid_choices(choices, options):
    return {field: source_id for field, source_id in choices.items()
            if field in FIELDS and len({entry["value"] for entry in options[field]}) > 1
            and any(entry["source_record_id"] == source_id for entry in options[field])}


def _draft(db, run_id, kind, item_id):
    return db.query(ResolutionChoiceDraft).filter_by(run_id=run_id, item_kind=kind, item_id=item_id).one_or_none()


def _choice_state(db, run_id, kind, item_id, member_ids, options, origin):
    saved = _draft(db, run_id, kind, item_id)
    choices = _valid_choices(saved.choices, options) if saved and saved.member_hash == _hash_members(member_ids) else {}
    if origin == "AUTO":
        for field in FIELDS:
            if len({entry["value"] for entry in options[field]}) > 1 and field not in choices:
                choices[field] = options[field][0]["source_record_id"]
    missing = [field for field in FIELDS if len({entry["value"] for entry in options[field]}) > 1 and field not in choices]
    return choices, missing


def _review_groups(db, run_id):
    proposals = db.query(ResolutionProposal).filter(ResolutionProposal.run_id == run_id,
                                                    ResolutionProposal.status.in_(ELIGIBLE)).all()
    cases = db.query(ResolutionReviewCase).filter_by(run_id=run_id, status="OPEN").order_by(ResolutionReviewCase.id).all()
    expanded = []
    for case in cases:
        members = set(case.member_record_ids)
        changed = True
        while changed:
            changed = False
            for proposal in proposals:
                linked = set(proposal.member_record_ids)
                if linked & members and not linked <= members:
                    members.update(linked)
                    changed = True
        expanded.append((members, case))
    groups: list[tuple[set[str], list[ResolutionReviewCase]]] = []
    for members, case in expanded:
        touching = [index for index, (existing, _) in enumerate(groups) if existing & members]
        combined_members, combined_cases = set(members), [case]
        for index in reversed(touching):
            old_members, old_cases = groups.pop(index)
            combined_members.update(old_members)
            combined_cases.extend(old_cases)
        groups.append((combined_members, combined_cases))
    # The union can connect groups that were separate before the last insertion.
    changed = True
    while changed:
        changed = False
        for left in range(len(groups)):
            for right in range(left + 1, len(groups)):
                if groups[left][0] & groups[right][0]:
                    groups[left][0].update(groups[right][0])
                    groups[left][1].extend(groups[right][1])
                    groups.pop(right)
                    changed = True
                    break
            if changed:
                break
    return [(tuple(sorted(members)), sorted(grouped, key=lambda row: row.id)) for members, grouped in groups]


def _review_entry(members, cases):
    first = cases[0]
    return {"id": first.id, "bucket": "review", "kind": "review", "origin": "REVIEW",
            "member_record_ids": list(members), "pair_count": len({pid for case in cases for pid in case.pair_ids}),
            "primary_group": first.primary_group, "subgroup": first.subgroup,
            "signature_hash": first.signature_hash, "reason": first.triage_reason}


def list_items(db: Session, run_id: str, bucket: str, offset: int, limit: int,
               origin: str | None = None, primary_group: str | None = None,
               subgroup: str | None = None):
    if bucket == "merge":
        query = db.query(ResolutionProposal).filter(ResolutionProposal.run_id == run_id,
                                                     ResolutionProposal.status.in_(ELIGIBLE))
        if origin:
            if origin not in {"AUTO", "REVIEW"}:
                raise HTTPException(422, "Nguồn item không hợp lệ")
            query = query.filter_by(origin=origin)
        total = query.count()
        rows = query.order_by(ResolutionProposal.id).offset(offset).limit(limit).all()
        entries = [{"id": row.id, "bucket": bucket, "kind": "proposal", "origin": row.origin,
                    "member_record_ids": row.member_record_ids, "pair_count": 0,
                    "label": row.golden_fields.get("fields", {}).get("name", {}).get("value", "")}
                   for row in rows]
    elif bucket == "no_merge":
        query = db.query(ResolutionPair).filter(ResolutionPair.run_id == run_id,
            or_(ResolutionPair.pair_decision == "NON_MATCH", ResolutionPair.user_decision == "REJECTED"))
        total = query.count()
        rows = query.order_by(ResolutionPair.id).offset(offset).limit(limit).all()
        entries = [{"id": row.id, "bucket": bucket, "kind": "pair",
                    "origin": "REVIEW" if row.user_decision == "REJECTED" else "AUTO",
                    "member_record_ids": [row.left_record_id, row.right_record_id],
                    "pair_count": 1, "score": row.score, "reason": row.triage_reason} for row in rows]
    elif bucket == "review":
        entries = [_review_entry(members, cases) for members, cases in _review_groups(db, run_id)
                   if (not primary_group or cases[0].primary_group == primary_group)
                   and (not subgroup or cases[0].subgroup == subgroup)]
        deferred = db.query(ResolutionProposal).filter_by(run_id=run_id, status="REVIEW_REQUIRED").all()
        if not primary_group and not subgroup:
            entries += [{"id": row.id, "bucket": bucket, "kind": "deferred", "origin": "REVIEW",
                         "member_record_ids": row.member_record_ids, "pair_count": 0,
                         "reason": row.decision_reason} for row in deferred]
        total = len(entries)
        entries = entries[offset:offset + limit]
    else:
        raise HTTPException(422, "Tab không hợp lệ")
    ids = {record_id for entry in entries for record_id in entry["member_record_ids"]}
    names = {row.id: row.raw_fields.get("name", "") for row in _records(db, ids)} if ids else {}
    for entry in entries:
        entry["label"] = entry.get("label") or names.get(entry["member_record_ids"][0], "Khách hàng")
        entry["member_count"] = len(entry["member_record_ids"])
        if bucket == "review":
            pairs = _internal_pairs(db, run_id, entry["member_record_ids"])
            entry["can_merge"] = not any(pair.pair_decision == "NON_MATCH" or pair.user_decision == "REJECTED"
                                         for pair in pairs)
    return {"total": total, "items": entries}


def get_item(db: Session, run_id: str, bucket: str, item_id: str):
    if bucket == "merge":
        row = db.get(ResolutionProposal, item_id)
        if row is None or row.run_id != run_id or row.status not in ELIGIBLE:
            raise HTTPException(404, "Item không còn ở tab Gộp")
        members, kind, origin = row.member_record_ids, "proposal", row.origin
        meta = {"reason": row.decision_reason, "status": row.status}
    elif bucket == "no_merge":
        row = db.get(ResolutionPair, item_id)
        if row is None or row.run_id != run_id or not (row.pair_decision == "NON_MATCH" or row.user_decision == "REJECTED"):
            raise HTTPException(404, "Item không còn ở tab Không gộp")
        members, kind, origin = [row.left_record_id, row.right_record_id], "pair", "REVIEW" if row.user_decision == "REJECTED" else "AUTO"
        meta = {"reason": row.triage_reason, "score": row.score, "field_evidence": row.field_evidence}
    elif bucket == "review":
        found = next(((members, cases) for members, cases in _review_groups(db, run_id)
                      if cases[0].id == item_id), None)
        if found:
            members, cases = found
            kind, origin = "review", "REVIEW"
            has_conflict = any(pair.pair_decision == "NON_MATCH" or pair.user_decision == "REJECTED"
                               for pair in _internal_pairs(db, run_id, members))
            meta = {"reason": cases[0].triage_reason, "primary_group": cases[0].primary_group,
                    "subgroup": cases[0].subgroup, "signature_hash": cases[0].signature_hash,
                    "pair_count": len({pid for case in cases for pid in case.pair_ids}),
                    "merge_blocked_reason": "Cụm có cặp đã xác định Không gộp; không thể gộp toàn cụm." if has_conflict else None}
        else:
            row = db.get(ResolutionProposal, item_id)
            if row is None or row.run_id != run_id or row.status != "REVIEW_REQUIRED":
                raise HTTPException(404, "Item không còn ở tab Cần review")
            members, kind, origin = row.member_record_ids, "deferred", "REVIEW"
            meta = {"reason": row.decision_reason, "pair_count": 0}
    else:
        raise HTTPException(422, "Tab không hợp lệ")
    records = _records(db, members)
    options = _options(records)
    choices, missing = _choice_state(db, run_id, kind, item_id, members, options, origin)
    return {"id": item_id, "bucket": bucket, "kind": kind, "origin": origin,
            "member_record_ids": list(members), "member_count": len(members),
            "records": [{"id": record.id, "external_id": record.external_id,
                         "source_system": record.source_system, "raw_fields": record.raw_fields}
                        for record in records], "field_options": options, "choices": choices,
            "missing_fields": missing, **meta}


def save_choices(db: Session, run_id: str, bucket: str, item_id: str, choices: dict,
                 actor_user_id: str):
    if bucket not in {"merge", "review"}:
        raise HTTPException(422, "Chỉ chọn giá trị ở Gộp hoặc Cần review")
    item = get_item(db, run_id, bucket, item_id)
    if set(choices) - set(FIELDS):
        raise HTTPException(422, "Trường không hợp lệ")
    if _valid_choices(choices, item["field_options"]) != choices:
        raise HTTPException(422, "Chỉ chọn nguồn hợp lệ cho trường có giá trị khác nhau")
    row = _draft(db, run_id, item["kind"], item_id)
    if row is None:
        row = ResolutionChoiceDraft(run_id=run_id, item_kind=item["kind"], item_id=item_id,
                                    actor_user_id=actor_user_id, member_hash=_hash_members(item["member_record_ids"]))
        db.add(row)
    row.choices = choices
    row.member_hash = _hash_members(item["member_record_ids"])
    row.actor_user_id = actor_user_id
    row.updated_at = now_utc()
    db.commit()
    return get_item(db, run_id, bucket, item_id)


def _internal_pairs(db, run_id, members):
    return db.query(ResolutionPair).filter(ResolutionPair.run_id == run_id,
        ResolutionPair.left_record_id.in_(members), ResolutionPair.right_record_id.in_(members)).all()


def _split_blocked_proposal(db, proposal, rejected_pairs, actor_user_id):
    """Retain safe edges when a rejected uncertain link cuts an eligible group."""
    members = set(proposal.member_record_ids)
    proposal.status = "SUPERSEDED"
    proposal.revision += 1
    pairs = _internal_pairs(db, proposal.run_id, members)
    cannot_link = {frozenset((pair.left_record_id, pair.right_record_id)) for pair in pairs
                   if pair.user_decision == "REJECTED" or pair.pair_decision == "NON_MATCH"}
    components = [{member} for member in members]
    for pair in sorted(pairs, key=lambda item: item.score, reverse=True):
        if pair.triage_status != "HIGH_CONFIDENCE_PROPOSAL" or pair.user_decision == "REJECTED":
            continue
        left = next(group for group in components if pair.left_record_id in group)
        right = next(group for group in components if pair.right_record_id in group)
        if left is right or any(frozenset((a, b)) in cannot_link for a in left for b in right):
            continue
        left.update(right)
        components.remove(right)
    for component in components:
        if len(component) < 2:
            continue
        rows = _records(db, component)
        payload = {row.id: {"id": row.id, "external_id": row.external_id,
                            "source_system": row.source_system, "raw_fields": row.raw_fields} for row in rows}
        new_id = sha256(f"{proposal.id}:split:{_hash_members(component)}".encode()).hexdigest()
        db.add(ResolutionProposal(id=new_id, run_id=proposal.run_id,
            member_record_ids=sorted(component), golden_fields=build_golden(sorted(component), payload),
            status="AUTO_READY", origin=proposal.origin, decision_reason="SAFE_LINKS_PRESERVED"))
    db.add(ResolutionAuditEvent(run_id=proposal.run_id, actor_user_id=actor_user_id,
        proposal_id=proposal.id, action="PROPOSAL_SPLIT",
        details={"rejected_pair_ids": [pair.id for pair in rejected_pairs]}))


def transition_review(db: Session, run_id: str, item_ids: list[str], decision: str, actor_user_id: str):
    if decision not in {"merge", "no_merge"} or not item_ids or len(item_ids) != len(set(item_ids)):
        raise HTTPException(422, "Quyết định hoặc danh sách item không hợp lệ")
    groups = {entry["id"]: entry for entry in list_items(db, run_id, "review", 0, 100000)["items"]}
    review_cases = {cases[0].id: cases for _, cases in _review_groups(db, run_id)}
    proposals = db.query(ResolutionProposal).filter(ResolutionProposal.run_id == run_id,
        ResolutionProposal.status.in_(ELIGIBLE)).all()
    if set(item_ids) - set(groups):
        raise HTTPException(409, "Danh sách review đã thay đổi; hãy tải lại")
    occupied = set()
    if decision == "merge":
        for item_id in item_ids:
            members = set(groups[item_id]["member_record_ids"])
            if occupied & members:
                raise HTTPException(409, "Các item review đã chọn có bản ghi giao nhau")
            occupied.update(members)
    changed = []
    for item_id in item_ids:
        entry = groups[item_id]
        members = set(entry["member_record_ids"])
        if entry["kind"] == "deferred":
            proposal = db.get(ResolutionProposal, item_id)
            if decision == "no_merge":
                raise HTTPException(422, "Item chờ xem lại không có cặp nghi ngờ để từ chối")
            draft = _draft(db, run_id, "deferred", item_id)
            if draft:
                copy = _draft(db, run_id, "proposal", item_id)
                if copy is None:
                    db.add(ResolutionChoiceDraft(run_id=run_id, item_kind="proposal", item_id=item_id,
                        member_hash=_hash_members(members), choices=_valid_choices(draft.choices, _options(_records(db, members))),
                        actor_user_id=actor_user_id))
            proposal.status = "REVIEW_APPROVED"
            proposal.origin = "REVIEW"
            proposal.revision += 1
            changed.append(proposal.id)
            continue
        cases = review_cases[item_id]
        pairs = _internal_pairs(db, run_id, members)
        uncertain = [pair for pair in pairs if pair.triage_status == "REVIEW" and pair.user_decision in {"UNDECIDED", "DEFERRED"}]
        if decision == "no_merge":
            for pair in uncertain:
                pair.user_decision = "REJECTED"
                pair.revision += 1
                db.add(ResolutionAuditEvent(run_id=run_id, actor_user_id=actor_user_id,
                    pair_id=pair.id, action="PAIR_REJECTED", details={"item_id": item_id}))
            for proposal in proposals:
                if proposal.status not in ELIGIBLE:
                    continue
                proposal_members = set(proposal.member_record_ids)
                within = [pair for pair in uncertain if pair.left_record_id in proposal_members
                          and pair.right_record_id in proposal_members]
                if within:
                    _split_blocked_proposal(db, proposal, within, actor_user_id)
            for case in cases:
                case.status = "REJECTED"
                case.revision += 1
            changed.append(item_id)
            continue
        if any(pair.pair_decision == "NON_MATCH" or pair.user_decision == "REJECTED" for pair in pairs):
            raise HTTPException(409, "Item có cặp xung đột hoặc đã bị từ chối")
        merged = {record_id for entity in db.query(ResolutionEntity).filter_by(run_id=run_id).all()
                  for record_id in entity.member_record_ids}
        if members & merged:
            raise HTTPException(409, "Item chứa bản ghi đã được gộp trước đó")
        for pair in uncertain:
            pair.user_decision = "ACCEPTED"
            pair.revision += 1
        for proposal in proposals:
            if proposal.status not in ELIGIBLE:
                continue
            if set(proposal.member_record_ids) & members:
                proposal.status = "SUPERSEDED"
                proposal.revision += 1
        for case in cases:
            case.status = "APPROVED"
            case.revision += 1
        records = _records(db, members)
        payload = {row.id: {"id": row.id, "external_id": row.external_id,
                            "source_system": row.source_system, "raw_fields": row.raw_fields} for row in records}
        run = db.get(ResolutionRun, run_id)
        proposal_id = sha256(f"{run_id}:{run.policy_version}:review:{_hash_members(members)}".encode()).hexdigest()
        proposal = db.get(ResolutionProposal, proposal_id)
        if proposal:
            raise HTTPException(409, "Item đã được duyệt; hãy tải lại")
        db.add(ResolutionProposal(id=proposal_id, run_id=run_id,
            member_record_ids=sorted(members), golden_fields=build_golden(sorted(members), payload),
            status="REVIEW_APPROVED", origin="REVIEW", decision_reason="USER_REVIEW_APPROVED"))
        draft = _draft(db, run_id, "review", item_id)
        if draft:
            valid = _valid_choices(draft.choices, _options(records))
            db.add(ResolutionChoiceDraft(run_id=run_id, item_kind="proposal", item_id=proposal_id,
                member_hash=_hash_members(members), choices=valid, actor_user_id=actor_user_id))
        db.add(ResolutionAuditEvent(run_id=run_id, actor_user_id=actor_user_id,
            proposal_id=proposal_id, action="REVIEW_TO_MERGE", details={"item_id": item_id}))
        changed.append(proposal_id)
    db.commit()
    return {"decision": decision, "changed_ids": changed, "changed_count": len(changed)}


def export_readiness(db: Session, run_id: str):
    proposals = db.query(ResolutionProposal).filter(ResolutionProposal.run_id == run_id,
                                                     ResolutionProposal.status.in_(ELIGIBLE)).order_by(ResolutionProposal.id).all()
    missing = []
    occupied = set()
    for proposal in proposals:
        members = set(proposal.member_record_ids)
        if occupied & members:
            raise HTTPException(409, "Các item Gộp có bản ghi giao nhau")
        occupied.update(members)
        options = _options(_records(db, members))
        _, fields = _choice_state(db, run_id, "proposal", proposal.id,
                                  proposal.member_record_ids, options, proposal.origin)
        if fields:
            missing.append({"item_id": proposal.id, "fields": fields})
    pending = len(_review_groups(db, run_id)) + db.query(ResolutionProposal).filter_by(
        run_id=run_id, status="REVIEW_REQUIRED").count()
    return {"merge_count": len(proposals), "pending_review_count": pending,
            "incomplete_count": len(missing), "incomplete_items": missing,
            "can_export": not missing}


def create_export(db: Session, run_id: str, actor_user_id: str, acknowledge_unreviewed: bool):
    readiness = export_readiness(db, run_id)
    if readiness["incomplete_count"]:
        raise HTTPException(409, "Cần chọn giá trị cho mọi trường khác nhau trước khi xuất CSV")
    if readiness["pending_review_count"] and not acknowledge_unreviewed:
        raise HTTPException(409, "Còn item Cần review; xác nhận xuất CSV với các bản ghi đó ở dạng riêng lẻ")
    records = db.query(ResolutionRecord).filter_by(run_id=run_id).order_by(ResolutionRecord.id).all()
    by_id = {row.id: row for row in records}
    occupied = set()
    rows = []
    columns = ["master_record_id", "full_name", "phone_number", "email_address", "raw_address", "source_record_ids",
               "source_systems", "record_count", "resolution_status", "review_pending"]
    def append_group(group_id, members, values, status, pending=False):
        if not members <= set(by_id) or occupied & members:
            raise HTTPException(409, "Nguồn bị thiếu hoặc trùng trong bản xuất")
        occupied.update(members)
        sources = sorted({by_id[record_id].source_system for record_id in members})
        rows.append({"master_record_id": group_id,
                     "full_name": values.get("name", ""), "phone_number": values.get("phone", ""),
                     "email_address": values.get("email", ""), "raw_address": values.get("address", ""),
                     "source_record_ids": "|".join(sorted(by_id[record_id].external_id for record_id in members)),
                     "source_systems": "|".join(sources), "record_count": len(members),
                     "resolution_status": status, "review_pending": "true" if pending else "false"})
    for entity in db.query(ResolutionEntity).filter_by(run_id=run_id).all():
        append_group(entity.id, set(entity.member_record_ids),
                     {field: entity.fields.get(field, {}).get("value", "") for field in FIELDS}, "MERGED")
    proposals = db.query(ResolutionProposal).filter(ResolutionProposal.run_id == run_id,
                                                     ResolutionProposal.status.in_(ELIGIBLE)).order_by(ResolutionProposal.id).all()
    for proposal in proposals:
        members = set(proposal.member_record_ids)
        options = _options([by_id[record_id] for record_id in members])
        choices, _ = _choice_state(db, run_id, "proposal", proposal.id, proposal.member_record_ids,
                                   options, proposal.origin)
        values = {}
        for field in FIELDS:
            candidates = options[field]
            values[field] = next((entry["value"] for entry in candidates
                                  if entry["source_record_id"] == choices.get(field)),
                                 candidates[0]["value"] if candidates else "")
        append_group(proposal.id, members, values, "MERGED")
    review_members = {record_id for members, _ in _review_groups(db, run_id) for record_id in members}
    for record in records:
        if record.id not in occupied:
            append_group(record.id, {record.id},
                         {field: record.raw_fields.get(field, "") for field in FIELDS},
                         "UNMERGED", record.id in review_members)
    if len(occupied) != len(records):
        raise HTTPException(409, "Bản xuất chưa bao phủ toàn bộ bản ghi đầu vào")
    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=columns)
    writer.writeheader()
    writer.writerows(rows)
    snapshot = ResolutionExport(run_id=run_id, actor_user_id=actor_user_id,
        csv_bytes=("\ufeff" + output.getvalue()).encode("utf-8"), row_count=len(rows),
        merged_group_count=len(proposals) + db.query(ResolutionEntity).filter_by(run_id=run_id).count(),
        pending_review_count=readiness["pending_review_count"])
    db.add(snapshot)
    db.commit()
    return snapshot


def source_csv(db: Session, run_id: str):
    """Reconstruct the combined input rows; original uploaded bytes are not stored."""
    columns = ["id", "source_system", "full_name", "phone_number", "email_address", "raw_address"]
    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=columns)
    writer.writeheader()
    for record in db.query(ResolutionRecord).filter_by(run_id=run_id).order_by(ResolutionRecord.id):
        writer.writerow({"id": record.external_id, "source_system": record.source_system,
                         "full_name": record.raw_fields.get("name", ""),
                         "phone_number": record.raw_fields.get("phone", ""),
                         "email_address": record.raw_fields.get("email", ""),
                         "raw_address": record.raw_fields.get("address", "")})
    return ("\ufeff" + output.getvalue()).encode("utf-8")
