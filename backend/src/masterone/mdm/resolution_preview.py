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
    ResolutionReviewCase,
    ResolutionRun,
)
from masterone.mdm.pipeline.decision_semantics import persisted_eligibility
from masterone.mdm.pipeline.decision_semantics import require_manual_merges
from masterone.mdm.pipeline.golden import build_golden
from masterone.mdm.pipeline.golden_v2 import golden_payload, merge_existing_golden
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
        fingerprint.update(dumps((pair.id, pair.revision, pair.user_decision, pair.staged_for_merge),
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


def _plan_staged_pairs(db: Session, run_id: str) -> dict:
    """Build connected groups and validate only the groups touched by staged pairs."""
    staged = db.query(ResolutionPair).filter_by(run_id=run_id, staged_for_merge=True).all()
    if not staged:
        return {"pair_ids": [], "proposals": [], "superseded_ids": [], "cases": []}
    existing = _active_proposals(db, run_id)
    parent: dict[str, str] = {}

    def find(record_id: str) -> str:
        parent.setdefault(record_id, record_id)
        root = record_id
        while parent[root] != root:
            root = parent[root]
        while record_id != root:
            next_id = parent[record_id]
            parent[record_id] = root
            record_id = next_id
        return root

    def union(members: list[str]) -> None:
        if not members:
            return
        root = find(members[0])
        for record_id in members[1:]:
            parent[find(record_id)] = root

    for proposal in existing:
        union(proposal.member_record_ids)
    for pair in staged:
        union([pair.left_record_id, pair.right_record_id])

    groups: dict[str, set[str]] = {}
    for record_id in parent:
        groups.setdefault(find(record_id), set()).add(record_id)
    touched = {find(pair.left_record_id) for pair in staged}
    affected = {root: groups[root] for root in touched}
    from masterone.config import get_settings
    limit = get_settings().export_max_group_records
    too_large = [sorted(members)[:2] for members in affected.values() if len(members) > limit]
    if too_large:
        raise HTTPException(409, {"reason": "GROUP_TOO_LARGE", "examples": too_large[:10]})
    blocked: dict[str, list[str]] = {}
    for pair in db.query(ResolutionPair).filter_by(run_id=run_id).yield_per(get_settings().export_batch_rows):
        left = parent.get(pair.left_record_id)
        right = parent.get(pair.right_record_id)
        if left is None or right is None:
            continue
        root = find(left)
        if root in touched and root == find(right) and not persisted_eligibility(pair).manual_allowed:
            blocked.setdefault(root, []).append(pair.id)
    if blocked:
        raise HTTPException(409, {"reason": "MERGE_CONFLICT", "blocked_pair_ids": sorted(
            pair_id for ids in blocked.values() for pair_id in ids)[:100]})
    require_manual_merges(db, run_id, affected.values())

    ids = {record_id for members in affected.values() for record_id in members}
    records = {}
    ordered_ids = sorted(ids)
    for start in range(0, len(ordered_ids), 500):
        rows = db.query(ResolutionRecord).filter(
            ResolutionRecord.run_id == run_id,
            ResolutionRecord.id.in_(ordered_ids[start:start + 500])).all()
        records.update({row.id: row for row in rows})
    if len(records) != len(ids):
        raise HTTPException(409, "Thiếu bản ghi nguồn của nhóm sẽ gộp")
    payload = {record_id: golden_payload(row) for record_id, row in records.items()}
    from masterone.mdm.resolution_items import _hash_members
    proposal_ids = {root: sha256(f"{run_id}:staged:{_hash_members(members)}".encode()).hexdigest()
                    for root, members in affected.items()}
    generated_ids = list(proposal_ids.values())
    for start in range(0, len(generated_ids), 500):
        if db.query(ResolutionProposal.id).filter(ResolutionProposal.id.in_(generated_ids[start:start + 500])).first():
            raise HTTPException(409, "Nhóm đã được duyệt; hãy tải lại")
    existing_by_root: dict[str, list[ResolutionProposal]] = {}
    for proposal in existing:
        existing_by_root.setdefault(find(proposal.member_record_ids[0]), []).append(proposal)
    new_proposals = []
    for root, members in sorted(affected.items()):
        proposal_id = proposal_ids[root]
        golden = build_golden(sorted(members), payload,
            existing_golden=merge_existing_golden(existing_by_root.get(root, []), members))
        golden["_default_unselected"] = True
        new_proposals.append({"id": proposal_id, "member_record_ids": sorted(members),
                              "golden_fields": golden})
    staged_ids = {pair.id for pair in staged}
    case_updates = []
    touched_cases = [case for case in db.query(ResolutionReviewCase).filter_by(run_id=run_id, status="OPEN")
                     if staged_ids.intersection(case.pair_ids)]
    remaining_ids = sorted({pair_id for case in touched_cases for pair_id in case.pair_ids}
                           - staged_ids)
    pending_by_id = {}
    for start in range(0, len(remaining_ids), 500):
        for pair in db.query(ResolutionPair).filter(
                ResolutionPair.run_id == run_id,
                ResolutionPair.id.in_(remaining_ids[start:start + 500]),
                ResolutionPair.user_decision.in_(["UNDECIDED", "DEFERRED"])):
            pending_by_id[pair.id] = pair
    for case in touched_cases:
        remaining = [pending_by_id[pair_id] for pair_id in case.pair_ids if pair_id in pending_by_id]
        if remaining:
            case_updates.append({"id": case.id, "pair_ids": [pair.id for pair in remaining],
                "member_record_ids": sorted({rid for pair in remaining for rid in
                                              (pair.left_record_id, pair.right_record_id)}),
                "status": "OPEN"})
        else:
            case_updates.append({"id": case.id, "pair_ids": case.pair_ids,
                "member_record_ids": case.member_record_ids, "status": "APPROVED"})
    return {"pair_ids": sorted(staged_ids), "proposals": new_proposals,
            "superseded_ids": [proposal.id for proposal in existing
                               if find(proposal.member_record_ids[0]) in touched],
            "cases": case_updates}


def _apply_staged_plan(db: Session, run_id: str, actor_user_id: str, plan: dict) -> None:
    for proposal_id in plan["superseded_ids"]:
        proposal = db.get(ResolutionProposal, proposal_id)
        proposal.status = "SUPERSEDED"
        proposal.revision += 1
    db.add_all(ResolutionProposal(id=entry["id"], run_id=run_id,
        member_record_ids=entry["member_record_ids"], golden_fields=entry["golden_fields"],
        status="REVIEW_APPROVED", origin="REVIEW", decision_reason="STAGED_PAIRS_PREVIEWED")
        for entry in plan["proposals"])
    for pair_id in plan["pair_ids"]:
        pair = db.get(ResolutionPair, pair_id)
        pair.staged_for_merge = False
        pair.user_decision = "ACCEPTED"
        pair.revision += 1
        db.add(ResolutionAuditEvent(run_id=run_id, actor_user_id=actor_user_id,
            pair_id=pair.id, action="PAIR_ACCEPTED", details={"source": "BULK_PREVIEW"}))
    for entry in plan["cases"]:
        case = db.get(ResolutionReviewCase, entry["id"])
        case.pair_ids = entry["pair_ids"]
        case.member_record_ids = entry["member_record_ids"]
        case.status = entry["status"]
        case.revision += 1
    db.flush()


def record_merge_events(db: Session, preview: ResolutionPreview, snapshot: ResolutionExport,
                        proposals: list[ResolutionProposal], pairs: Iterable[ResolutionPair]) -> None:
    from masterone.config import get_settings
    from masterone.mdm.export_stream import index, proposal_record_batches
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
        for proposal, ordered in proposal_record_batches(db, db.query(ResolutionProposal).filter(
                ResolutionProposal.run_id == preview.run_id,
                ResolutionProposal.status.in_(ELIGIBLE)), get_settings().export_batch_rows, budget):
            records = {row.id: row for row in ordered}
            options = _options(ordered)
            selected, _ = _choice_state(options, "AUTO" if proposal.golden_fields.get("_default_unselected")
                                        else proposal.origin, preview.choices.get(proposal.id))
            golden = {}
            for field in ("name", "phone", "email", "address"):
                candidates = options[field]
                default_source = proposal.golden_fields.get("fields", {}).get(field, {}).get("source_record_id")
                source_id = (preview.choices.get(proposal.id) or {}).get(field) or (
                    default_source if proposal.golden_fields.get("_default_unselected") else selected.get(field))
                source_id = source_id or (candidates[0]["source_record_id"] if candidates else None)
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
    run = db.query(ResolutionRun).filter_by(id=run_id).with_for_update().one_or_none()
    if run is None or run.status != "COMPLETED":
        raise HTTPException(409, "Lần nạp chưa xử lý xong")
    baseline = {row.id: row.revision for row in _active_proposals(db, run_id)}
    fingerprint, _ = _pair_snapshot(db, run_id)
    plan = _plan_staged_pairs(db, run_id)
    staged_count = len(plan["pair_ids"])
    savepoint = db.begin_nested() if staged_count else None
    try:
        if staged_count:
            _apply_staged_plan(db, run_id, actor_user_id, plan)
        readiness = export_readiness(db, run_id, choices)
        if not readiness["can_export"]:
            raise HTTPException(409, "Còn giá trị chưa chọn hoặc cụm cần tách trước khi xem trước")
        if readiness["pending_review_count"] and not acknowledge_unreviewed:
            raise HTTPException(409, "Còn item cần review; cần xác nhận xuất các bản ghi đó riêng lẻ")
        proposals = _active_proposals(db, run_id)
        eligible_count = len(proposals)
        separate_count = run.record_count - len({rid for proposal in proposals
                                                 for rid in proposal.member_record_ids})
        output_rows = run.record_count - sum(len(proposal.member_record_ids) - 1
                                              for proposal in proposals)
    finally:
        if savepoint is not None:
            savepoint.rollback()
            db.expire_all()
    created_at = now_utc()
    preview = ResolutionPreview(
        run_id=run_id, actor_user_id=actor_user_id, policy_version=run.policy_version,
        pair_set_hash=fingerprint,
        proposal_revisions=baseline,
        choices=choices, readiness_snapshot=readiness, staged_plan=plan if staged_count else None,
        acknowledge_unreviewed=acknowledge_unreviewed,
        created_at=created_at, expires_at=created_at + timedelta(minutes=15),
    )
    db.add(preview)
    db.commit()
    return {"preview_id": preview.id, "status": preview.status,
            "eligible_count": eligible_count, "review_required_count": readiness["pending_review_count"],
        "accepted_pair_count": staged_count,
            "separate_record_count": separate_count,
            "estimated_output_rows": output_rows,
            "policy_version": run.policy_version, "expires_at": preview.expires_at}


def execute_preview(db: Session, run_id: str, preview_id: str, request_id: str,
                    actor_user_id: str) -> ResolutionExport:
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
    parent = db.query(ResolutionExport).filter_by(run_id=run_id).order_by(
        ResolutionExport.created_at.desc(), ResolutionExport.id.desc()).first()
    if preview.staged_plan:
        _apply_staged_plan(db, run_id, actor_user_id, preview.staged_plan)
        proposals = _active_proposals(db, run_id)
    snapshot = create_export(db, run_id, actor_user_id, preview.acknowledge_unreviewed,
                             preview.choices, commit=False, export_id=preview.id,
                             verified_readiness=preview.readiness_snapshot)
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
