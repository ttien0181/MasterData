import asyncio
import csv
from io import StringIO

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from masterone.auth.security import CurrentSession, current_session, require_mutation
from masterone.config import get_settings
from masterone.db.session import get_session
from masterone.mdm.gemini_explanation import explain_cohort
from masterone.mdm.models import (
    ResolutionExplanation,
    ResolutionExport,
    ResolutionPair,
    ResolutionProposal,
    ResolutionRecord,
    ResolutionReviewCase,
    ResolutionTask,
)
from masterone.mdm.ownership import owned_run
from masterone.mdm.resolution_items import (
    _review_groups,
    create_export,
    export_readiness,
    get_item,
    list_items,
    save_choices,
    source_csv,
    transition_review,
)
from masterone.mdm.resolution_merge import cancel_preview, confirm_merge, history_detail, history_page, save_preview
from masterone.mdm.resolution_review import approve_case, approve_deferred_proposal, build_merge_preview, decide_pair
from masterone.mdm.schemas import (
    DeferredProposalApproval,
    ResolutionCancelRequest,
    ResolutionConfirmRequest,
    ResolutionPairDecision,
    ResolutionPreviewRequest,
    ReviewCaseApproval,
)

router = APIRouter(prefix="/mdm/runs", tags=["MDM resolution"])


class ItemChoices(BaseModel):
    choices: dict[str, str]


class ReviewTransition(BaseModel):
    item_ids: list[str] = Field(min_length=1)
    decision: str


class ExportRequest(BaseModel):
    acknowledge_unreviewed: bool = False


@router.get("/{run_id}/items/{bucket}")
def get_items(run_id: str, bucket: str, limit: int = Query(25, ge=1, le=100),
              offset: int = Query(0, ge=0), origin: str | None = None,
              primary_group: str | None = None, subgroup: str | None = None,
              db: Session = Depends(get_session), auth: CurrentSession = Depends(current_session)):
    owned_run(db, run_id, auth.user.id)
    return list_items(db, run_id, bucket, offset, limit, origin, primary_group, subgroup)


@router.get("/{run_id}/items/{bucket}/{item_id}")
def get_item_detail(run_id: str, bucket: str, item_id: str,
                    db: Session = Depends(get_session), auth: CurrentSession = Depends(current_session)):
    owned_run(db, run_id, auth.user.id)
    return get_item(db, run_id, bucket, item_id)


@router.put("/{run_id}/items/{bucket}/{item_id}/choices")
def put_item_choices(run_id: str, bucket: str, item_id: str, request: ItemChoices,
                     db: Session = Depends(get_session), auth: CurrentSession = Depends(require_mutation)):
    owned_run(db, run_id, auth.user.id)
    return save_choices(db, run_id, bucket, item_id, request.choices, auth.user.id)


@router.post("/{run_id}/review-items/transition")
def post_review_transition(run_id: str, request: ReviewTransition,
                           db: Session = Depends(get_session), auth: CurrentSession = Depends(require_mutation)):
    owned_run(db, run_id, auth.user.id)
    return transition_review(db, run_id, request.item_ids, request.decision, auth.user.id)


@router.get("/{run_id}/export-readiness")
def get_export_readiness(run_id: str, db: Session = Depends(get_session),
                         auth: CurrentSession = Depends(current_session)):
    owned_run(db, run_id, auth.user.id)
    return export_readiness(db, run_id)


def _csv_response(snapshot: ResolutionExport):
    return Response(snapshot.csv_bytes, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="masterone-{snapshot.run_id[:8]}-{snapshot.id[:8]}.csv"',
                             "X-Export-Id": snapshot.id})


@router.post("/{run_id}/exports")
def post_export(run_id: str, request: ExportRequest, db: Session = Depends(get_session),
                auth: CurrentSession = Depends(require_mutation)):
    owned_run(db, run_id, auth.user.id)
    return _csv_response(create_export(db, run_id, auth.user.id, request.acknowledge_unreviewed))


@router.get("/{run_id}/exports")
def get_exports(run_id: str, db: Session = Depends(get_session),
                auth: CurrentSession = Depends(current_session)):
    owned_run(db, run_id, auth.user.id)
    rows = db.query(ResolutionExport).filter_by(run_id=run_id).order_by(
        ResolutionExport.created_at.desc()).all()
    return {"items": [{"id": row.id, "row_count": row.row_count,
                        "merged_group_count": row.merged_group_count,
                        "pending_review_count": row.pending_review_count,
                        "created_at": row.created_at} for row in rows]}


@router.get("/{run_id}/source-records.csv")
def download_source_records(run_id: str, db: Session = Depends(get_session),
                            auth: CurrentSession = Depends(current_session)):
    owned_run(db, run_id, auth.user.id)
    return Response(source_csv(db, run_id), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="masterone-{run_id[:8]}-source.csv"'})


@router.get("/{run_id}/exports/{export_id}/download")
def download_export(run_id: str, export_id: str, db: Session = Depends(get_session),
                    auth: CurrentSession = Depends(current_session)):
    owned_run(db, run_id, auth.user.id)
    snapshot = db.get(ResolutionExport, export_id)
    if snapshot is None or snapshot.run_id != run_id:
        raise HTTPException(404, "Không tìm thấy bản xuất")
    return _csv_response(snapshot)


@router.get("/{run_id}/exports/{export_id}/compare")
def compare_export(run_id: str, export_id: str, db: Session = Depends(get_session),
                   auth: CurrentSession = Depends(current_session)):
    owned_run(db, run_id, auth.user.id)
    snapshot = db.get(ResolutionExport, export_id)
    if snapshot is None or snapshot.run_id != run_id:
        raise HTTPException(404, "Không tìm thấy bản xuất")
    source_rows = list(csv.DictReader(StringIO(source_csv(db, run_id).decode("utf-8-sig"))))
    output_rows = list(csv.DictReader(StringIO(snapshot.csv_bytes.decode("utf-8-sig"))))
    return {"source_count": len(source_rows), "output_count": len(output_rows),
            "source_sample": source_rows[:10], "output_sample": output_rows[:10]}


def _record_summary(record: ResolutionRecord) -> dict:
    return {"id": record.id, "external_id": record.external_id, "source_system": record.source_system,
            "raw_fields": record.raw_fields, "normalized_fields": record.normalized_fields,
            "quality_flags": record.quality_flags}


def _pair_summary(db: Session, pair: ResolutionPair) -> dict:
    records = db.query(ResolutionRecord).filter(
        ResolutionRecord.id.in_([pair.left_record_id, pair.right_record_id])).all()
    by_id = {record.id: record for record in records}
    return {"id": pair.id, "score": pair.score, "pair_decision": pair.pair_decision,
            "revision": pair.revision, "user_decision": pair.user_decision,
            "decision_source": "ALGORITHM" if pair.pair_decision == "NON_MATCH" else "STEWARD",
            "triage_status": pair.triage_status, "triage_reason": pair.triage_reason,
            "field_evidence": pair.field_evidence, "left": _record_summary(by_id[pair.left_record_id]),
            "right": _record_summary(by_id[pair.right_record_id])}


def _review_members(case: ResolutionReviewCase, proposals: list[ResolutionProposal]) -> set[str]:
    """Expand a review pair to any safe proposals it touches, matching approval semantics."""
    member_ids = set(case.member_record_ids)
    changed = True
    while changed:
        changed = False
        for proposal in proposals:
            proposal_members = set(proposal.member_record_ids)
            if member_ids & proposal_members and not proposal_members <= member_ids:
                member_ids.update(proposal_members)
                changed = True
    return member_ids


@router.get("/{run_id}/overview")
def get_overview(run_id: str, db: Session = Depends(get_session),
                 auth: CurrentSession = Depends(current_session)):
    run = owned_run(db, run_id, auth.user.id)
    task = db.query(ResolutionTask).filter_by(run_id=run.id).one_or_none()
    counts = {
        "auto": db.query(ResolutionProposal).filter(ResolutionProposal.run_id == run_id,
                                                     ResolutionProposal.status.in_(["AUTO_READY", "REVIEW_APPROVED"])).count(),
        "rejected": db.query(ResolutionPair).filter(
            ResolutionPair.run_id == run_id,
            or_(ResolutionPair.pair_decision == "NON_MATCH", ResolutionPair.user_decision == "REJECTED"),
        ).count(),
        "review_cases": db.query(ResolutionReviewCase).filter_by(run_id=run_id, status="OPEN").count(),
        "manual": db.query(ResolutionProposal).filter_by(run_id=run_id, status="REVIEW_REQUIRED").count(),
    }
    counts["review"] = counts["review_cases"] + counts["manual"]
    source_counts = {source: count for source, count in db.query(ResolutionRecord.source_system,
                    func.count(ResolutionRecord.id)).filter_by(run_id=run_id).group_by(
                    ResolutionRecord.source_system).all()}
    history_count = db.query(ResolutionExport).filter_by(run_id=run_id).count()
    return {"run_id": run.id, "status": run.status, "phase": task.phase if task else run.phase,
            "processed_count": task.processed_count if task else 0,
            "total_count": task.total_count if task else run.record_count,
            "record_count": run.record_count, "file_count": run.file_count,
            "source_counts": source_counts, "history_count": history_count, "counts": counts}


@router.get("/{run_id}/categories")
def get_categories(run_id: str, db: Session = Depends(get_session),
                   auth: CurrentSession = Depends(current_session)):
    owned_run(db, run_id, auth.user.id)
    groups = _review_groups(db, run_id)
    grouped: dict[tuple[str, str], dict] = {}
    for members, cases in groups:
        case = cases[0]
        key = (case.primary_group, case.subgroup)
        item = grouped.setdefault(key, {"primary_group": key[0], "subgroup": key[1],
                                        "signature_hash": case.signature_hash,
                                        "pair_count": 0, "clusters": set()})
        item["pair_count"] += len({pair_id for row in cases for pair_id in row.pair_ids})
        item["clusters"].add(members)
    rule_templates = {
        "CONSTRAINT_OR_CLUSTER_RISK": "Cụm có rủi ro liên kết. Hãy kiểm tra các cặp và bằng chứng trước khi duyệt.",
        "SINGLE_MAJOR_CONFLICT": "Một trường có khác biệt rõ trong khi cặp có tín hiệu khác cần đối chiếu.",
        "SHARED_VALUE": "Có giá trị liên hệ được dùng chung; giá trị này chưa đủ để kết luận cùng khách hàng.",
        "VIETNAM_VARIANT": "Có thể có biến thể cách viết tiếng Việt hoặc số điện thoại đã chuẩn hóa.",
        "SPARSE_EVIDENCE": "Bằng chứng còn thưa; hãy xem các trường còn thiếu trước khi quyết định.",
        "MODEL_DISAGREEMENT": "Các tín hiệu chưa thống nhất; hãy kiểm tra từng trường trong cặp.",
    }
    return [{"primary_group": key[0], "subgroup": key[1], "signature_hash": value["signature_hash"],
             "pair_count": value["pair_count"],
             "unique_cluster_count": len(value["clusters"]),
             "rule_template_vi": rule_templates.get(key[0], "Hãy xem lại bằng chứng của các cặp.")}
            for key, value in sorted(grouped.items())]


@router.get("/{run_id}/buckets/{bucket}")
def list_bucket(run_id: str, bucket: str, limit: int = Query(25, ge=1, le=100),
                offset: int = Query(0, ge=0), primary_group: str | None = None,
                subgroup: str | None = None, db: Session = Depends(get_session),
                auth: CurrentSession = Depends(current_session)):
    owned_run(db, run_id, auth.user.id)
    if bucket == "auto":
        query = db.query(ResolutionProposal).filter(ResolutionProposal.run_id == run_id,
                                                    ResolutionProposal.status.in_(["AUTO_READY", "REVIEW_APPROVED"]))
        total = query.count()
        items = query.order_by(ResolutionProposal.id).offset(offset).limit(limit).all()
        return {"total": total, "items": [{"id": item.id, "status": item.status,
                                             "revision": item.revision, "member_record_ids": item.member_record_ids,
                                             "golden_fields": item.golden_fields} for item in items]}
    if bucket == "rejected":
        query = db.query(ResolutionPair).filter(
            ResolutionPair.run_id == run_id,
            or_(ResolutionPair.pair_decision == "NON_MATCH", ResolutionPair.user_decision == "REJECTED"),
        )
        total = query.count()
        return {"total": total, "items": [_pair_summary(db, pair) for pair in
                query.order_by(ResolutionPair.id).offset(offset).limit(limit).all()]}
    if bucket == "review":
        query = db.query(ResolutionReviewCase).filter_by(run_id=run_id, status="OPEN")
        if primary_group:
            query = query.filter_by(primary_group=primary_group)
        if subgroup:
            query = query.filter_by(subgroup=subgroup)
        cases = query.order_by(ResolutionReviewCase.primary_group, ResolutionReviewCase.subgroup,
                               ResolutionReviewCase.id).all()
        eligible_proposals = db.query(ResolutionProposal).filter(
            ResolutionProposal.run_id == run_id,
            ResolutionProposal.status.in_(["AUTO_READY", "REVIEW_APPROVED"]),
        ).all()
        manual = [] if primary_group or subgroup else db.query(ResolutionProposal).filter_by(
            run_id=run_id, status="REVIEW_REQUIRED").all()
        entries = []
        for case in cases:
            member_ids = _review_members(case, eligible_proposals)
            pairs = db.query(ResolutionPair).filter(
                ResolutionPair.run_id == run_id,
                ResolutionPair.left_record_id.in_(member_ids),
                ResolutionPair.right_record_id.in_(member_ids),
                ResolutionPair.triage_status == "REVIEW",
            ).order_by(ResolutionPair.id).all()
            entries.append({"id": case.id, "kind": "case", "primary_group": case.primary_group,
                            "subgroup": case.subgroup, "triage_reason": case.triage_reason,
                            "revision": case.revision, "member_record_ids": sorted(member_ids),
                            "pair_count": len(pairs),
                            "pairs": [_pair_summary(db, pair) for pair in pairs]})
        entries.extend({"id": item.id, "kind": "USER_DEFERRED", "status": item.status,
                        "revision": item.revision,
                        "triage_reason": item.decision_reason, "member_record_ids": item.member_record_ids,
                        "golden_fields": item.golden_fields, "pair_count": 0} for item in manual)
        total = len(entries)
        return {"total": total, "items": entries[offset:offset + limit]}
    raise HTTPException(422, "Bucket phải là auto, rejected hoặc review")


@router.patch("/{run_id}/pairs/{pair_id}/decision")
def update_pair_decision(run_id: str, pair_id: str, request: ResolutionPairDecision,
                         db: Session = Depends(get_session), auth: CurrentSession = Depends(require_mutation)):
    owned_run(db, run_id, auth.user.id)
    pair = db.get(ResolutionPair, pair_id)
    if pair is None or pair.run_id != run_id:
        raise HTTPException(404, "Pair not found")
    return decide_pair(db, pair, request.decision, request.expected_revision, auth.user.id)


@router.post("/{run_id}/review-cases/{case_id}/approve")
def approve_review_case(run_id: str, case_id: str, request: ReviewCaseApproval,
                        db: Session = Depends(get_session), auth: CurrentSession = Depends(require_mutation)):
    owned_run(db, run_id, auth.user.id)
    case = db.get(ResolutionReviewCase, case_id)
    if case is None or case.run_id != run_id:
        raise HTTPException(404, "Review case not found")
    return approve_case(db, case, request.expected_revision, request.accepted_pair_ids, auth.user.id)


@router.post("/{run_id}/proposals/{proposal_id}/approve")
def approve_deferred(run_id: str, proposal_id: str, request: DeferredProposalApproval,
                     db: Session = Depends(get_session), auth: CurrentSession = Depends(require_mutation)):
    owned_run(db, run_id, auth.user.id)
    proposal = db.get(ResolutionProposal, proposal_id)
    if proposal is None or proposal.run_id != run_id:
        raise HTTPException(404, "Proposal not found")
    return approve_deferred_proposal(db, proposal, request.expected_revision, auth.user.id)


@router.post("/{run_id}/merges/preview")
def preview_merges(run_id: str, request: ResolutionPreviewRequest, db: Session = Depends(get_session),
                   auth: CurrentSession = Depends(require_mutation)):
    owned_run(db, run_id, auth.user.id)
    result = build_merge_preview(db, run_id, auth.user.id, request.proposal_ids, request.selection_scope)
    save_preview(db, run_id, auth.user.id, result)
    return {**result, "selected_count": len(result["proposals"]), "excluded_count": 0}


@router.post("/{run_id}/merges/confirm")
def confirm_merges(run_id: str, request: ResolutionConfirmRequest, db: Session = Depends(get_session),
                   auth: CurrentSession = Depends(require_mutation)):
    owned_run(db, run_id, auth.user.id)
    return confirm_merge(db, run_id=run_id, owner_user_id=auth.user.id,
                         preview_token=request.preview_token, idempotency_key=request.idempotency_key,
                         choices=request.choices)


@router.post("/{run_id}/merges/cancel")
def cancel_merges(run_id: str, request: ResolutionCancelRequest, db: Session = Depends(get_session),
                  auth: CurrentSession = Depends(require_mutation)):
    owned_run(db, run_id, auth.user.id)
    return cancel_preview(db, run_id, auth.user.id, request.preview_token)


@router.get("/{run_id}/history")
def get_history(run_id: str, limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0),
                db: Session = Depends(get_session), auth: CurrentSession = Depends(current_session)):
    owned_run(db, run_id, auth.user.id)
    return history_page(db, run_id, limit, offset)


@router.get("/{run_id}/history/{event_id}")
def get_history_detail(run_id: str, event_id: str, db: Session = Depends(get_session),
                       auth: CurrentSession = Depends(current_session)):
    owned_run(db, run_id, auth.user.id)
    detail = history_detail(db, event_id)
    if detail["run_id"] != run_id:
        raise HTTPException(404, "History event not found")
    return detail


@router.post("/{run_id}/categories/{signature_hash}/explanation")
async def explain_category(run_id: str, signature_hash: str, db: Session = Depends(get_session),
                          auth: CurrentSession = Depends(require_mutation)):
    owned_run(db, run_id, auth.user.id)
    cases = db.query(ResolutionReviewCase).filter_by(run_id=run_id, signature_hash=signature_hash,
                                                      status="OPEN").all()
    if not cases:
        raise HTTPException(404, "Review cohort not found")
    existing = db.query(ResolutionExplanation).filter_by(run_id=run_id,
                                                          signature_hash=signature_hash).one_or_none()
    if existing and existing.status == "READY":
        return {"status": existing.status, "explanation": existing.explanation,
                "model_version": existing.model_version, "usage": existing.usage}
    pair_ids = {pair_id for case in cases for pair_id in case.pair_ids}
    pairs = db.query(ResolutionPair).filter(ResolutionPair.id.in_(pair_ids)).all()
    bands = {field: {"exact": 0, "similar": 0, "different": 0, "ambiguous": 0, "missing": 0}
             for field in ("name", "phone", "email", "address")}
    reason_counts: dict[str, int] = {}
    for pair in pairs:
        reason_counts[pair.triage_reason] = reason_counts.get(pair.triage_reason, 0) + 1
        for evidence in pair.field_evidence:
            field = evidence["field"]
            if field not in bands:
                continue
            score = evidence["similarity"]
            band = "exact" if score == 1 else "similar" if score >= 0.8 else "different" if score < 0.4 else "ambiguous"
            bands[field][band] += 1
    aggregate = {"group": cases[0].primary_group, "subgroup": cases[0].subgroup,
                 "pair_count": len(pairs), "unique_record_count": len({record_id for case in cases
                    for record_id in case.member_record_ids}), "reason_counts": reason_counts,
                 "field_signal_counts": bands}
    if existing is None:
        existing = ResolutionExplanation(run_id=run_id, signature_hash=signature_hash,
                                         primary_group=cases[0].primary_group,
                                         subgroup=cases[0].subgroup, status="RUNNING")
        db.add(existing)
    else:
        existing.status = "RUNNING"
        existing.error_code = None
    db.commit()
    try:
        result = await asyncio.to_thread(explain_cohort, aggregate, get_settings())
        existing.status = "READY"
        existing.explanation = result["explanation"]
        existing.model_version = result["model_version"]
        existing.usage = result["usage"]
        existing.error_code = None
        db.commit()
        return {"status": existing.status, "explanation": existing.explanation,
                "model_version": existing.model_version, "usage": existing.usage}
    except HTTPException as exc:
        existing.status = "ERROR"
        existing.error_code = exc.detail if isinstance(exc.detail, str) else "GEMINI_ERROR"
        db.commit()
        raise
    except Exception:
        existing.status = "ERROR"
        existing.error_code = "GEMINI_UNAVAILABLE"
        db.commit()
        raise HTTPException(502, "GEMINI_UNAVAILABLE") from None
