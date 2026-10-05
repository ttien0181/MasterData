import asyncio
import csv
from hashlib import sha256
from io import StringIO

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from masterone.auth.security import CurrentSession, current_session, require_mutation
from masterone.config import get_settings
from masterone.db.base import now_utc
from masterone.db.session import get_session
from masterone.storage.r2 import get_store
from masterone.mdm.gemini_explanation import explain_cohort
from masterone.mdm.models import (
    ResolutionAuditEvent,
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
    list_separate_records,
    resolve_staged_conflict,
    separate_record_count,
    source_csv,
    transition_review,
)
from masterone.mdm.resolution_preview import execute_preview, prepare_preview
from masterone.mdm.resolution_split import execute_split, list_merge_events, prepare_split

router = APIRouter(prefix="/mdm/runs", tags=["MDM resolution"])


def _category_key(primary_group: str, subgroup: str) -> str:
    return sha256(f"{primary_group}:{subgroup}".encode()).hexdigest()


class ReviewTransition(BaseModel):
    item_ids: list[str] = Field(min_length=1)
    decision: str


class ExportRequest(BaseModel):
    acknowledge_unreviewed: bool = False
    choices: dict[str, dict[str, str]] = Field(default_factory=dict)


class ExecutePreviewRequest(BaseModel):
    execute_request_id: str = Field(min_length=1, max_length=80)


class ExecuteSplitRequest(BaseModel):
    member_id: str
    version: str
    execute_request_id: str = Field(min_length=1, max_length=80)


@router.get("/{run_id}/items/{bucket}")
def get_items(run_id: str, bucket: str, limit: int = Query(25, ge=1, le=100),
              offset: int = Query(0, ge=0), origin: str | None = None,
              primary_group: str | None = None, subgroup: str | None = None,
              signature_hash: str | None = None,
              db: Session = Depends(get_session), auth: CurrentSession = Depends(current_session)):
    owned_run(db, run_id, auth.user.id)
    return list_items(db, run_id, bucket, offset, limit, origin, primary_group, subgroup, signature_hash)


@router.get("/{run_id}/separate-records")
def get_separate_records(run_id: str, offset: int = Query(0, ge=0),
                         limit: int = Query(25, ge=1, le=100), q: str = Query("", max_length=100),
                         db: Session = Depends(get_session),
                         auth: CurrentSession = Depends(current_session)):
    owned_run(db, run_id, auth.user.id)
    return list_separate_records(db, run_id, offset, limit, q)


@router.get("/{run_id}/items/{bucket}/{item_id}")
def get_item_detail(run_id: str, bucket: str, item_id: str, signature_hash: str | None = None,
                    primary_group: str | None = None, subgroup: str | None = None,
                    db: Session = Depends(get_session), auth: CurrentSession = Depends(current_session)):
    owned_run(db, run_id, auth.user.id)
    return get_item(db, run_id, bucket, item_id, signature_hash, primary_group, subgroup)


@router.post("/{run_id}/review-items/transition")
def post_review_transition(run_id: str, request: ReviewTransition,
                           db: Session = Depends(get_session), auth: CurrentSession = Depends(require_mutation)):
    owned_run(db, run_id, auth.user.id)
    return transition_review(db, run_id, request.item_ids, request.decision, auth.user.id)


@router.post("/{run_id}/merge-items/{item_id}/resolve-conflict")
def post_resolve_staged_conflict(run_id: str, item_id: str,
                                 db: Session = Depends(get_session), auth: CurrentSession = Depends(require_mutation)):
    owned_run(db, run_id, auth.user.id)
    return resolve_staged_conflict(db, run_id, item_id, auth.user.id)


@router.post("/{run_id}/export-readiness")
def get_export_readiness(run_id: str, request: ExportRequest, db: Session = Depends(get_session),
                         auth: CurrentSession = Depends(current_session)):
    owned_run(db, run_id, auth.user.id)
    return export_readiness(db, run_id, request.choices)


def _csv_bytes(snapshot: ResolutionExport) -> bytes:
    """Return CSV bytes — from DB fallback or R2."""
    if snapshot.csv_bytes is not None:
        return snapshot.csv_bytes
    if snapshot.r2_key:
        r2 = get_store(get_settings())
        if r2 is not None:
            return r2.download_bytes(snapshot.r2_key)
    raise HTTPException(500, "Export data unavailable")


def _csv_response(snapshot: ResolutionExport):
    return Response(_csv_bytes(snapshot), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="masterone-{snapshot.run_id[:8]}-{snapshot.id[:8]}.csv"',
                             "X-Export-Id": snapshot.id})


@router.post("/{run_id}/exports")
def post_export(run_id: str, request: ExportRequest, db: Session = Depends(get_session),
                auth: CurrentSession = Depends(require_mutation)):
    run = owned_run(db, run_id, auth.user.id)
    if run.policy_version == "person-linh-v1":
        raise HTTPException(409, "Hãy xem trước và xác nhận bản gộp trước khi xuất CSV")
    return _csv_response(create_export(db, run_id, auth.user.id, request.acknowledge_unreviewed,
                                       request.choices))


@router.post("/{run_id}/previews")
def post_preview(run_id: str, request: ExportRequest,
                 db: Session = Depends(get_session), auth: CurrentSession = Depends(require_mutation)):
    owned_run(db, run_id, auth.user.id)
    return prepare_preview(db, run_id, auth.user.id, request.choices, request.acknowledge_unreviewed)


@router.post("/{run_id}/previews/{preview_id}/execute")
def post_execute_preview(run_id: str, preview_id: str, request: ExecutePreviewRequest,
                         db: Session = Depends(get_session), auth: CurrentSession = Depends(require_mutation)):
    owned_run(db, run_id, auth.user.id)
    return _csv_response(execute_preview(db, run_id, preview_id, request.execute_request_id, auth.user.id))


@router.get("/{run_id}/exports")
def get_exports(run_id: str, db: Session = Depends(get_session),
                auth: CurrentSession = Depends(current_session)):
    owned_run(db, run_id, auth.user.id)
    rows = db.query(ResolutionExport).filter_by(run_id=run_id).order_by(
        ResolutionExport.created_at.desc(), ResolutionExport.id.desc()).all()
    return {"items": [{"id": row.id, "row_count": row.row_count,
                        "merged_group_count": row.merged_group_count,
                        "pending_review_count": row.pending_review_count,
                        "parent_export_id": row.parent_export_id,
                        "created_at": row.created_at} for row in rows]}


@router.get("/{run_id}/exports/{export_id}/merges")
def get_export_merges(run_id: str, export_id: str, offset: int = Query(0, ge=0),
                      limit: int = Query(25, ge=1, le=100), q: str = Query("", max_length=100),
                      db: Session = Depends(get_session), auth: CurrentSession = Depends(current_session)):
    owned_run(db, run_id, auth.user.id)
    return list_merge_events(db, run_id, export_id, offset, limit, q)


@router.get("/{run_id}/exports/{export_id}/merges/{event_id}/split-preview")
def get_split_preview(run_id: str, export_id: str, event_id: str, member_id: str,
                      db: Session = Depends(get_session), auth: CurrentSession = Depends(current_session)):
    owned_run(db, run_id, auth.user.id)
    return prepare_split(db, run_id, export_id, event_id, member_id)


@router.post("/{run_id}/exports/{export_id}/merges/{event_id}/split")
def post_split(run_id: str, export_id: str, event_id: str, request: ExecuteSplitRequest,
               db: Session = Depends(get_session), auth: CurrentSession = Depends(require_mutation)):
    owned_run(db, run_id, auth.user.id)
    snapshot = execute_split(db, run_id, export_id, event_id, request.member_id,
                             request.version, request.execute_request_id, auth.user.id)
    return {"export_id": snapshot.id, "row_count": snapshot.row_count,
            "parent_export_id": snapshot.parent_export_id}


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
    # When file is in R2, redirect to presigned URL — avoids pulling bytes through the app server
    if snapshot.r2_key and snapshot.csv_bytes is None:
        r2 = get_store(get_settings())
        if r2 is not None:
            url = r2.presigned_url(snapshot.r2_key, expires_in=300)
            return RedirectResponse(url, status_code=302)
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


@router.get("/{run_id}/overview")
def get_overview(run_id: str, db: Session = Depends(get_session),
                 auth: CurrentSession = Depends(current_session)):
    run = owned_run(db, run_id, auth.user.id)
    task = db.query(ResolutionTask).filter_by(run_id=run.id).one_or_none()
    counts = {
        "auto": db.query(ResolutionProposal).filter(ResolutionProposal.run_id == run_id,
                                                     ResolutionProposal.status.in_(["AUTO_READY", "REVIEW_APPROVED", "REVIEW_STAGED_CONFLICT"])).count(),
        "rejected": db.query(ResolutionPair).filter(
            ResolutionPair.run_id == run_id,
            or_(ResolutionPair.pair_decision.in_(["NON_MATCH", "NOT_MERGEABLE"]),
                ResolutionPair.user_decision == "REJECTED"),
        ).count(),
        "review_cases": len(_review_groups(db, run_id)),
        "manual": db.query(ResolutionProposal).filter_by(run_id=run_id, status="REVIEW_REQUIRED").count(),
    }
    counts["review"] = counts["review_cases"] + counts["manual"]
    counts["hard_blocked"] = db.query(ResolutionPair).filter_by(run_id=run_id,
                                                                  pair_decision="NOT_MERGEABLE").count()
    counts["insufficient"] = run.insufficient_count
    counts["group_a"] = db.query(ResolutionPair).filter_by(run_id=run_id, primary_group="A").count()
    counts["group_c"] = db.query(ResolutionPair).filter_by(run_id=run_id, primary_group="C").count()
    counts["group_d"] = db.query(ResolutionPair).filter_by(run_id=run_id, primary_group="D").count()
    counts["separate"] = separate_record_count(db, run_id, run.record_count)
    source_counts = {source: count for source, count in db.query(ResolutionRecord.source_system,
                    func.count(ResolutionRecord.id)).filter_by(run_id=run_id).group_by(
                    ResolutionRecord.source_system).all()}
    history_count = db.query(ResolutionExport).filter_by(run_id=run_id).count()
    return {"run_id": run.id, "status": run.status, "phase": task.phase if task else run.phase,
            "processed_count": task.processed_count if task else 0,
            "total_count": task.total_count if task else run.record_count,
            "record_count": run.record_count, "file_count": run.file_count,
            "source_counts": source_counts, "history_count": history_count,
            "candidate_count": run.candidate_count, "counts": counts}


@router.get("/{run_id}/categories")
def get_categories(run_id: str, db: Session = Depends(get_session),
                   auth: CurrentSession = Depends(current_session)):
    owned_run(db, run_id, auth.user.id)
    groups = _review_groups(db, run_id)
    grouped: dict[tuple[str, str], dict] = {}
    for members, cases in groups:
        for case in cases:
            key = (case.primary_group, case.subgroup)
            item = grouped.setdefault(key, {"primary_group": key[0], "subgroup": key[1],
                                            "signature_hash": _category_key(*key),
                                            "pair_ids": set(), "clusters": set()})
            item["pair_ids"].update(case.pair_ids)
            item["clusters"].add(members)
    rule_templates = {
        "A": "Tên và thông tin liên hệ giống nhau, nhưng hệ thống chưa thể tự gộp an toàn. Hãy đối chiếu các giá trị nguồn trước khi quyết định.",
        "C": "Thông tin liên hệ có thể giống nhau, nhưng tên chưa đủ rõ để xác nhận đây là cùng một khách hàng. Hãy xem các giá trị còn khác nhau.",
        "D": "Tên trên các bản ghi khác nhau rõ rệt dù có thông tin liên hệ trùng. Hãy kiểm tra trước khi quyết định có gộp hay không.",
        "CONSTRAINT_OR_CLUSTER_RISK": "Một số bản ghi có thể liên quan, nhưng gộp cả nhóm có thể tạo ra liên kết mâu thuẫn. Hãy đối chiếu thông tin trước khi quyết định.",
        "SINGLE_MAJOR_CONFLICT": "Có một thông tin quan trọng khác nhau dù các thông tin khác khá giống. Hãy kiểm tra giá trị ở từng bản ghi.",
        "SHARED_VALUE": "Số điện thoại hoặc email có thể được nhiều người dùng chung, nên chưa đủ để xác nhận các bản ghi thuộc cùng một khách hàng.",
        "VIETNAM_VARIANT": "Thông tin có thể được viết hoặc định dạng theo nhiều cách. Hãy đối chiếu giá trị gốc trước khi quyết định.",
        "SPARSE_EVIDENCE": "Các bản ghi còn thiếu thông tin để đối chiếu chắc chắn. Hãy xem những trường đã có dữ liệu trước khi quyết định.",
        "MODEL_DISAGREEMENT": "Các thông tin hiện có cho kết quả chưa thống nhất. Hãy xem các giá trị khác nhau trước khi quyết định.",
    }
    return [{"primary_group": key[0], "subgroup": key[1], "signature_hash": value["signature_hash"],
             "pair_count": len(value["pair_ids"]),
             "unique_cluster_count": len(value["clusters"]),
             "rule_template_vi": rule_templates.get(key[0], "Hãy đối chiếu thông tin trong các bản ghi trước khi quyết định.")}
            for key, value in sorted(grouped.items())]


@router.post("/{run_id}/categories/{signature_hash}/explanation")
async def explain_category(run_id: str, signature_hash: str, db: Session = Depends(get_session),
                          auth: CurrentSession = Depends(require_mutation)):
    owned_run(db, run_id, auth.user.id)
    cases = [case for case in db.query(ResolutionReviewCase).filter_by(run_id=run_id, status="OPEN").all()
             if _category_key(case.primary_group, case.subgroup) == signature_hash]
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


@router.post("/{run_id}/items/{bucket}/{item_id}/explanation")
async def explain_item(run_id: str, bucket: str, item_id: str,
                       db: Session = Depends(get_session),
                       auth: CurrentSession = Depends(require_mutation)):
    """Explain one item from comparison states; never send source values to AI."""
    owned_run(db, run_id, auth.user.id)
    detail = get_item(db, run_id, bucket, item_id)
    pair_ids = [pair["id"] for pair in detail["pairs"]]
    pairs = db.query(ResolutionPair).filter(
        ResolutionPair.run_id == run_id, ResolutionPair.id.in_(pair_ids)).all()
    version = "|".join(sorted(f"{pair.id}:{pair.revision}" for pair in pairs))
    signature = sha256(f"item:{bucket}:{item_id}:{version}".encode()).hexdigest()
    existing = db.query(ResolutionExplanation).filter_by(
        run_id=run_id, signature_hash=signature).one_or_none()
    if existing and existing.status == "READY":
        return {"status": existing.status, "explanation": existing.explanation,
                "model_version": existing.model_version, "usage": existing.usage}

    field_states: dict[str, dict[str, int]] = {}
    reasons: dict[str, int] = {}
    for pair in detail["pairs"]:
        reason = pair["reason"]
        reasons[reason] = reasons.get(reason, 0) + 1
        for evidence in pair["field_evidence"]:
            field = evidence.get("field")
            if field not in {"name", "phone", "email", "address"}:
                continue
            state = evidence.get("state", "NC")
            counts = field_states.setdefault(field, {})
            counts[state] = counts.get(state, 0) + 1
    aggregate = {"record_count": detail["member_count"], "pair_count": len(detail["pairs"]),
                 "field_state_counts": field_states, "reason_counts": reasons,
                 "score_is_probability": False}
    if existing is None:
        existing = ResolutionExplanation(run_id=run_id, signature_hash=signature,
                                         primary_group="ITEM", subgroup="ITEM", status="RUNNING")
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

# ── Golden record endpoints for Steward ──────────────────────────────────────

class GoldenRecordOverride(BaseModel):
    fields: dict[str, str] = Field(default_factory=dict,
        description='Map field to value; empty dict clears the override')


_GOLDEN_FIELDS = frozenset({'name', 'phone', 'email', 'address', 'dob', 'government_id'})


@router.get('/{run_id}/proposals/{proposal_id}/golden-record')
def get_golden_record(run_id: str, proposal_id: str,
                      db: Session = Depends(get_session),
                      auth: CurrentSession = Depends(current_session)):
    owned_run(db, run_id, auth.user.id)
    proposal = db.get(ResolutionProposal, proposal_id)
    if proposal is None or proposal.run_id != run_id:
        raise HTTPException(404, 'Proposal not found')
    gf = proposal.golden_fields or {}
    member_records = []
    for rid in proposal.member_record_ids:
        rec = db.get(ResolutionRecord, rid)
        if rec:
            member_records.append({'id': rec.id, 'external_id': rec.external_id,
                                   'source_system': rec.source_system,
                                   'raw_fields': rec.raw_fields,
                                   'normalized_fields': rec.normalized_fields})
    return {'proposal_id': proposal_id, 'status': proposal.status,
            'golden_fields': gf.get('fields', {}),
            'member_records': member_records,
            'steward_override': gf.get('_golden_override', {})}


@router.put('/{run_id}/proposals/{proposal_id}/golden-record')
def put_golden_record(run_id: str, proposal_id: str, request: GoldenRecordOverride,
                      db: Session = Depends(get_session),
                      auth: CurrentSession = Depends(require_mutation)):
    owned_run(db, run_id, auth.user.id)
    proposal = db.get(ResolutionProposal, proposal_id)
    if proposal is None or proposal.run_id != run_id:
        raise HTTPException(404, 'Proposal not found')
    if proposal.status not in ('AUTO_READY', 'REVIEW_APPROVED', 'REVIEW_REQUIRED'):
        raise HTTPException(409, f'Cannot edit golden record in status {proposal.status}')
    unknown = set(request.fields) - _GOLDEN_FIELDS
    if unknown:
        raise HTTPException(422, f'Unknown fields: {sorted(unknown)}')
    gf = dict(proposal.golden_fields or {})
    if request.fields:
        gf['_golden_override'] = request.fields
    else:
        gf.pop('_golden_override', None)
    proposal.golden_fields = gf
    db.add(ResolutionAuditEvent(
        run_id=run_id, actor_user_id=auth.user.id, proposal_id=proposal_id,
        action='GOLDEN_RECORD_OVERRIDE',
        details={'fields': request.fields, 'cleared': not request.fields},
    ))
    db.commit()
    return {'proposal_id': proposal_id, 'steward_override': request.fields}

