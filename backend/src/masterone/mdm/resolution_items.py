"""Steward item workflow and complete, reproducible CSV snapshots."""

import csv
from hashlib import sha256
from io import StringIO

from fastapi import HTTPException
from sqlalchemy import case as sql_case
from sqlalchemy import or_
from sqlalchemy.orm import Session

from masterone.mdm.models import (
    ResolutionAuditEvent,
    ResolutionPair,
    ResolutionProposal,
    ResolutionRecord,
    ResolutionReviewCase,
    ResolutionRun,
)
from masterone.mdm.pipeline.decision_semantics import persisted_eligibility, require_manual_merge
from masterone.mdm.pipeline.golden import build_golden
from masterone.mdm.pipeline.golden_v2 import golden_payload, merge_existing_golden
from masterone.mdm.pipeline.matching import score_pair

FIELDS = ("name", "phone", "email", "address")
ELIGIBLE = ("AUTO_READY", "REVIEW_APPROVED")
STAGED_CONFLICT = "REVIEW_STAGED_CONFLICT"


def _is_blocked(pair):
    return not persisted_eligibility(pair).manual_allowed


def _covered_record_ids(proposals):
    return {record_id for proposal in proposals for record_id in proposal.member_record_ids}


def separate_record_count(db: Session, run_id: str, record_count: int) -> int:
    proposals = db.query(ResolutionProposal).filter(
        ResolutionProposal.run_id == run_id, ResolutionProposal.status.in_(ELIGIBLE)).all()
    return record_count - len(_covered_record_ids(proposals))


def list_separate_records(db: Session, run_id: str, offset: int, limit: int, search: str = "") -> dict:
    """List records that would occupy their own row in the next CSV export."""
    proposals = db.query(ResolutionProposal).filter(
        ResolutionProposal.run_id == run_id, ResolutionProposal.status.in_(ELIGIBLE)).all()
    covered = _covered_record_ids(proposals)
    pending = {record_id for members, _ in _review_groups(db, run_id) for record_id in members}
    for proposal in db.query(ResolutionProposal).filter_by(run_id=run_id, status="REVIEW_REQUIRED"):
        pending.update(proposal.member_record_ids)
    needle = search.strip().casefold()
    rows = db.query(ResolutionRecord).filter_by(run_id=run_id).order_by(
        ResolutionRecord.source_system, ResolutionRecord.external_id, ResolutionRecord.id).all()
    separate = [row for row in rows if row.id not in covered and (
        not needle or needle in row.external_id.casefold()
        or needle in row.source_system.casefold()
        or needle in str(row.raw_fields.get("name", "")).casefold())]
    return {"total": len(separate), "items": [{
        "id": row.id, "external_id": row.external_id, "source_system": row.source_system,
        "raw_fields": row.raw_fields, "review_pending": row.id in pending,
    } for row in separate[offset:offset + limit]]}


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


def _choice_state(options, origin, submitted=None):
    choices = _valid_choices(submitted or {}, options)
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
    parent = {}

    def find(record_id):
        parent.setdefault(record_id, record_id)
        if parent[record_id] != record_id:
            parent[record_id] = find(parent[record_id])
        return parent[record_id]

    def union(members):
        if not members:
            return
        root = find(members[0])
        for member in members[1:]:
            parent[find(member)] = root

    for proposal in proposals:
        union(proposal.member_record_ids)
    for case in cases:
        union(case.member_record_ids)
    groups = {}
    for case in cases:
        groups.setdefault(find(case.member_record_ids[0]), []).append(case)
    members_by_root = {}
    for record_id in parent:
        root = find(record_id)
        if root in groups:
            members_by_root.setdefault(root, set()).add(record_id)
    return [(tuple(sorted(members_by_root[root])), sorted(grouped, key=lambda row: row.id))
            for root, grouped in groups.items()]


def _review_entry(members, cases, selected_case=None):
    first = selected_case or cases[0]
    return {"id": cases[0].id, "bucket": "review", "kind": "review", "origin": "REVIEW",
            "member_record_ids": list(members), "pair_count": len({pid for case in cases for pid in case.pair_ids}),
            "primary_group": first.primary_group, "subgroup": first.subgroup,
            "signature_hash": first.signature_hash, "reason": first.triage_reason}


def list_items(db: Session, run_id: str, bucket: str, offset: int, limit: int,
               origin: str | None = None, primary_group: str | None = None,
               subgroup: str | None = None, signature_hash: str | None = None):
    if bucket == "merge":
        query = db.query(ResolutionProposal).filter(ResolutionProposal.run_id == run_id,
                                                     ResolutionProposal.status.in_((*ELIGIBLE, STAGED_CONFLICT)))
        if origin:
            if origin not in {"AUTO", "REVIEW"}:
                raise HTTPException(422, "Nguồn item không hợp lệ")
            query = query.filter_by(origin=origin)
        total = query.count()
        rows = query.order_by(sql_case((ResolutionProposal.status == STAGED_CONFLICT, 0), else_=1),
                              ResolutionProposal.id).offset(offset).limit(limit).all()
        entries = [{"id": row.id, "bucket": bucket, "kind": "proposal", "origin": row.origin,
                    "member_record_ids": row.member_record_ids, "pair_count": 0,
                    "label": row.golden_fields.get("fields", {}).get("name", {}).get("value", ""),
                    "status": row.status}
                   for row in rows]
    elif bucket == "review_pairs":
        from masterone.mdm.resolution_pairs import pending_pairs
        query = pending_pairs(db, run_id)
        if primary_group:
            query = query.filter(ResolutionPair.primary_group.is_(None) if primary_group == "OTHER"
                                 else ResolutionPair.primary_group == primary_group)
        if subgroup:
            query = query.filter(ResolutionPair.subgroup.is_(None) if subgroup == "OTHER"
                                 else ResolutionPair.subgroup == subgroup)
        total = query.count()
        rows = query.order_by(ResolutionPair.id).offset(offset).limit(limit).all()
        entries = [{"id": row.id, "bucket": bucket, "kind": "pair", "origin": "REVIEW",
                    "member_record_ids": [row.left_record_id, row.right_record_id], "pair_count": 1,
                    "reason": row.triage_reason, "primary_group": row.primary_group,
                    "subgroup": row.subgroup, "revision": row.revision} for row in rows]
    elif bucket == "no_merge":
        query = db.query(ResolutionPair).filter(ResolutionPair.run_id == run_id,
            or_(ResolutionPair.pair_decision.in_(["NON_MATCH", "NOT_MERGEABLE"]),
                ResolutionPair.user_decision.in_(["REJECTED", "NON_MATCH"])))
        total = query.count()
        rows = query.order_by(ResolutionPair.id).offset(offset).limit(limit).all()
        entries = [{"id": row.id, "bucket": bucket, "kind": "pair",
                    "origin": "REVIEW" if row.user_decision in {"REJECTED", "NON_MATCH"} else "AUTO",
                    "member_record_ids": [row.left_record_id, row.right_record_id],
                    "pair_count": 1, "score": row.score, "reason": row.triage_reason} for row in rows]
    elif bucket == "review":
        entries = []
        for members, cases in _review_groups(db, run_id):
            matching = next((case for case in cases
                             if (not primary_group or case.primary_group == primary_group)
                             and (not subgroup or case.subgroup == subgroup)
                             and (not signature_hash or case.signature_hash == signature_hash)), None)
            if matching is not None:
                entries.append(_review_entry(members, cases, matching))
        deferred = db.query(ResolutionProposal).filter_by(run_id=run_id, status="REVIEW_REQUIRED").all()
        if not primary_group and not subgroup and not signature_hash:
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
        if bucket in {"review_pairs", "no_merge"}:
            entry["record_labels"] = [names.get(record_id, "") for record_id in entry["member_record_ids"]]
        if bucket == "review":
            pairs = _internal_pairs(db, run_id, entry["member_record_ids"])
            entry["can_merge"] = not any(pair.pair_decision in {"NON_MATCH", "NOT_MERGEABLE"}
                                         or pair.user_decision in {"REJECTED", "NON_MATCH"}
                                         for pair in pairs)
    return {"total": total, "items": entries}


def get_item(db: Session, run_id: str, bucket: str, item_id: str, signature_hash: str | None = None,
             primary_group: str | None = None, subgroup: str | None = None):
    if bucket == "review_pairs":
        from masterone.mdm.resolution_pairs import pending_pairs
        row = pending_pairs(db, run_id).filter(ResolutionPair.id == item_id).one_or_none()
        if row is None:
            raise HTTPException(404, "Cặp không còn chờ xem xét")
        members, kind, origin = [row.left_record_id, row.right_record_id], "pair", "REVIEW"
        related = set(members)
        for proposal in db.query(ResolutionProposal).filter(
                ResolutionProposal.run_id == run_id, ResolutionProposal.status.in_(ELIGIBLE)):
            if related & set(proposal.member_record_ids):
                related.update(proposal.member_record_ids)
        meta = {"reason": row.triage_reason, "revision": row.revision,
                "primary_group": row.primary_group, "subgroup": row.subgroup,
                "affected_record_count": len(related),
                "merge_blocked_reason": "Nhóm liên quan có cặp Không gộp."
                if any(_is_blocked(pair) for pair in _internal_pairs(db, run_id, related)) else None}
    elif bucket == "merge":
        row = db.get(ResolutionProposal, item_id)
        if row is None or row.run_id != run_id or row.status not in (*ELIGIBLE, STAGED_CONFLICT):
            raise HTTPException(404, "Item không còn ở tab Gộp")
        members, kind, origin = row.member_record_ids, "proposal", row.origin
        meta = {"reason": row.decision_reason, "status": row.status,
                "merge_blocked_reason": "Cụm còn cặp Không gộp; cần tách trước khi xuất CSV."
                if row.status == STAGED_CONFLICT else None}
    elif bucket == "no_merge":
        row = db.get(ResolutionPair, item_id)
        if row is None or row.run_id != run_id or not (row.pair_decision in {"NON_MATCH", "NOT_MERGEABLE"}
                                                       or row.user_decision in {"REJECTED", "NON_MATCH"}):
            raise HTTPException(404, "Item không còn ở tab Không gộp")
        members, kind, origin = [row.left_record_id, row.right_record_id], "pair", "REVIEW" if row.user_decision in {"REJECTED", "NON_MATCH"} else "AUTO"
        meta = {"reason": row.triage_reason, "score": row.score,
                "field_evidence": row.field_evidence}
    elif bucket == "review":
        found = next(((members, cases) for members, cases in _review_groups(db, run_id)
                      if cases[0].id == item_id), None)
        if found:
            members, cases = found
            kind, origin = "review", "REVIEW"
            has_conflict = any(_is_blocked(pair) for pair in _internal_pairs(db, run_id, members))
            selected_case = next((case for case in cases
                                  if (not primary_group or case.primary_group == primary_group)
                                  and (not subgroup or case.subgroup == subgroup)
                                  and (not signature_hash or case.signature_hash == signature_hash)), cases[0])
            meta = {"reason": selected_case.triage_reason, "primary_group": selected_case.primary_group,
                    "subgroup": selected_case.subgroup, "signature_hash": selected_case.signature_hash,
                    "pair_count": len({pid for case in cases for pid in case.pair_ids}),
                    "merge_blocked_reason": "Cụm có cặp đã xác định Không gộp; không thể gộp toàn cụm." if has_conflict else None}
        else:
            row = db.get(ResolutionProposal, item_id)
            if row is None or row.run_id != run_id or row.status != "REVIEW_REQUIRED":
                raise HTTPException(404, "Item không còn ở tab Cần review")
            members, kind, origin = row.member_record_ids, "deferred", "REVIEW"
            has_conflict = any(_is_blocked(pair) for pair in _internal_pairs(db, run_id, members))
            meta = {"reason": row.decision_reason, "pair_count": 0,
                    "merge_blocked_reason": "Cụm có cặp Không gộp; cần tách trước khi xuất CSV."
                    if has_conflict else None}
    else:
        raise HTTPException(422, "Tab không hợp lệ")
    if len(members) > 200:
        raise HTTPException(413, "Review detail exceeds 200 records; use pair review queue")
    records = _records(db, members)
    if bucket == "no_merge" and not meta["field_evidence"]:
        by_id = {record.id: record for record in records}
        left = by_id[row.left_record_id]
        right = by_id[row.right_record_id]
        meta["field_evidence"] = score_pair(
            {"normalized_fields": left.normalized_fields, "raw_fields": left.raw_fields,
             "quality_flags": left.quality_flags},
            {"normalized_fields": right.normalized_fields, "raw_fields": right.raw_fields,
             "quality_flags": right.quality_flags}, set(row.blocking_reasons),
        )["field_evidence"]
    options = _options(records)
    choices, missing = _choice_state(options, origin)
    records_by_id = {record.id: record for record in records}
    from masterone.mdm.engine_adapter import EngineResultAdapter
    adapter = EngineResultAdapter()
    if "field_evidence" in meta:
        meta["field_evidence"] = adapter.evidence(meta["field_evidence"])
    detail_pairs = db.query(ResolutionPair).filter(ResolutionPair.run_id == run_id,
        ResolutionPair.left_record_id.in_(members), ResolutionPair.right_record_id.in_(members)).order_by(ResolutionPair.id).limit(2001).all()
    if len(detail_pairs) > 2000:
        raise HTTPException(413, "Review detail exceeds 2000 pairs; use pair review queue")
    pair_details = [adapter.pair_detail(pair, records_by_id,
        meta["field_evidence"] if bucket == "no_merge" and pair.id == item_id else None) for pair in detail_pairs]
    return {"id": item_id, "bucket": bucket, "kind": kind, "origin": origin,
            "member_record_ids": list(members), "member_count": len(members),
            "records": [{"id": record.id, "external_id": record.external_id,
                         "source_system": record.source_system, "raw_fields": record.raw_fields}
                        for record in records], "field_options": options, "choices": choices,
            "missing_fields": missing, "pairs": pair_details, **meta}


def _internal_pairs(db, run_id, members):
    return db.query(ResolutionPair).filter(ResolutionPair.run_id == run_id,
        ResolutionPair.left_record_id.in_(members), ResolutionPair.right_record_id.in_(members)).all()


def _split_blocked_proposal(db, proposal, rejected_pairs, actor_user_id, excluded_members=()):
    """Retain safe edges when a rejected uncertain link cuts an eligible group."""
    members = set(proposal.member_record_ids) - set(excluded_members)
    proposal.status = "SUPERSEDED"
    proposal.revision += 1
    pairs = _internal_pairs(db, proposal.run_id, members)
    cannot_link = {frozenset((pair.left_record_id, pair.right_record_id)) for pair in pairs
                   if _is_blocked(pair)}
    components = [{member} for member in members]
    for pair in sorted(pairs, key=lambda item: item.score, reverse=True):
        if (pair.triage_status != "HIGH_CONFIDENCE_PROPOSAL" and pair.user_decision != "ACCEPTED") or _is_blocked(pair):
            continue
        left = next(group for group in components if pair.left_record_id in group)
        right = next(group for group in components if pair.right_record_id in group)
        if left is right or any(frozenset((a, b)) in cannot_link for a in left for b in right):
            continue
        left.update(right)
        components.remove(right)
    preserved = []
    for component in components:
        if len(component) < 2:
            continue
        preserved.append(set(component))
        rows = _records(db, component)
        payload = {row.id: golden_payload(row) for row in rows}
        new_id = sha256(f"{proposal.id}:split:{_hash_members(component)}".encode()).hexdigest()
        db.add(ResolutionProposal(id=new_id, run_id=proposal.run_id,
            member_record_ids=sorted(component), golden_fields=build_golden(sorted(component), payload),
            status="AUTO_READY", origin=proposal.origin, decision_reason="SAFE_LINKS_PRESERVED"))
    db.add(ResolutionAuditEvent(run_id=proposal.run_id, actor_user_id=actor_user_id,
        proposal_id=proposal.id, action="PROPOSAL_SPLIT",
        details={"rejected_pair_ids": [pair.id for pair in rejected_pairs]}))
    return preserved


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
        if decision == "merge":
            require_manual_merge(db, run_id, members)
        if entry["kind"] == "deferred":
            proposal = db.get(ResolutionProposal, item_id)
            if decision == "no_merge":
                raise HTTPException(422, "Item chờ xem lại không có cặp nghi ngờ để từ chối")
            blocked = [pair for pair in _internal_pairs(db, run_id, members) if _is_blocked(pair)]
            proposal.status = STAGED_CONFLICT if blocked else "REVIEW_APPROVED"
            proposal.origin = "REVIEW"
            if blocked:
                proposal.golden_fields = {**proposal.golden_fields, "staged_case_ids": []}
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
        if any(_is_blocked(pair) for pair in pairs):
            records = _records(db, members)
            payload = {row.id: golden_payload(row) for row in records}
            staged_id = sha256(f"{run_id}:staged:{_hash_members(members)}".encode()).hexdigest()
            if db.get(ResolutionProposal, staged_id):
                raise HTTPException(409, "Item đã được đưa sang Gộp; hãy tải lại")
            golden = build_golden(sorted(members), payload, existing_golden=merge_existing_golden(proposals, members))
            golden["staged_case_ids"] = [case.id for case in cases]
            db.add(ResolutionProposal(id=staged_id, run_id=run_id,
                member_record_ids=sorted(members), golden_fields=golden,
                status=STAGED_CONFLICT, origin="REVIEW", decision_reason="CONFLICT_REQUIRES_SPLIT"))
            db.flush()
            for case in cases:
                case.status = "STAGED"
                case.revision += 1
            db.add(ResolutionAuditEvent(run_id=run_id, actor_user_id=actor_user_id,
                proposal_id=staged_id, action="REVIEW_STAGED_CONFLICT", details={"item_id": item_id}))
            changed.append(staged_id)
            continue
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
        payload = {row.id: golden_payload(row) for row in records}
        run = db.get(ResolutionRun, run_id)
        proposal_id = sha256(f"{run_id}:{run.policy_version}:review:{_hash_members(members)}".encode()).hexdigest()
        proposal = db.get(ResolutionProposal, proposal_id)
        if proposal:
            raise HTTPException(409, "Item đã được duyệt; hãy tải lại")
        db.add(ResolutionProposal(id=proposal_id, run_id=run_id,
            member_record_ids=sorted(members), golden_fields=build_golden(sorted(members), payload,
                existing_golden=merge_existing_golden(proposals, members)),
            status="REVIEW_APPROVED", origin="REVIEW", decision_reason="USER_REVIEW_APPROVED"))
        db.flush()
        db.add(ResolutionAuditEvent(run_id=run_id, actor_user_id=actor_user_id,
            proposal_id=proposal_id, action="REVIEW_TO_MERGE", details={"item_id": item_id}))
        changed.append(proposal_id)
    db.commit()
    return {"decision": decision, "changed_ids": changed, "changed_count": len(changed)}


def resolve_staged_conflict(db: Session, run_id: str, item_id: str, actor_user_id: str):
    """Reject undecided links, then retain groups compatible with every known non-match."""
    staged = db.get(ResolutionProposal, item_id)
    if staged is None or staged.run_id != run_id or staged.status != STAGED_CONFLICT:
        raise HTTPException(404, "Item chờ tách không còn ở tab Gộp")
    members = set(staged.member_record_ids)
    case_ids = staged.golden_fields.get("staged_case_ids", [])
    cases = db.query(ResolutionReviewCase).filter(
        ResolutionReviewCase.run_id == run_id,
        ResolutionReviewCase.id.in_(case_ids),
        ResolutionReviewCase.status == "STAGED").all()
    if len(cases) != len(case_ids):
        raise HTTPException(409, "Các cặp review đã thay đổi; hãy tải lại")
    pairs = _internal_pairs(db, run_id, members)
    uncertain = [pair for pair in pairs if pair.triage_status == "REVIEW"
                 and pair.user_decision in {"UNDECIDED", "DEFERRED"}]
    for pair in uncertain:
        pair.user_decision = "REJECTED"
        pair.revision += 1
        db.add(ResolutionAuditEvent(run_id=run_id, actor_user_id=actor_user_id,
            pair_id=pair.id, action="PAIR_REJECTED", details={"staged_item_id": item_id}))
    eligible_covered = set()
    for proposal in db.query(ResolutionProposal).filter(
            ResolutionProposal.run_id == run_id,
            ResolutionProposal.status.in_(ELIGIBLE)).all():
        proposal_members = set(proposal.member_record_ids)
        if not proposal_members & members:
            continue
        blocked = [pair for pair in _internal_pairs(db, run_id, proposal_members) if _is_blocked(pair)]
        if blocked:
            for component in _split_blocked_proposal(db, proposal, blocked, actor_user_id):
                eligible_covered.update(component)
        else:
            eligible_covered.update(proposal_members)
    if not case_ids:
        blocked = [pair for pair in pairs if _is_blocked(pair)]
        _split_blocked_proposal(db, staged, blocked, actor_user_id, eligible_covered)
    for review_case in cases:
        review_case.status = "REJECTED"
        review_case.revision += 1
    if staged.status != "SUPERSEDED":
        staged.status = "SUPERSEDED"
        staged.revision += 1
    db.add(ResolutionAuditEvent(run_id=run_id, actor_user_id=actor_user_id,
        proposal_id=item_id, action="STAGED_CONFLICT_SPLIT",
        details={"rejected_pair_ids": [pair.id for pair in uncertain]}))
    db.commit()
    return {"rejected_pair_count": len(uncertain)}


def export_readiness(db: Session, run_id: str, submitted_choices: dict | None = None):
    from masterone.mdm.export_stream import export_readiness as streamed_readiness
    return streamed_readiness(db, run_id, submitted_choices)


def create_export(db: Session, run_id: str, actor_user_id: str, acknowledge_unreviewed: bool,
                  submitted_choices: dict | None = None, *, commit: bool = True, export_id=None):
    from masterone.mdm.export_stream import create_export as streamed_export
    return streamed_export(db, run_id, actor_user_id, acknowledge_unreviewed,
                           submitted_choices, commit=commit, export_id=export_id)


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
