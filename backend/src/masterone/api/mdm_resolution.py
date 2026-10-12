import asyncio
import csv
import json
import time
from datetime import timedelta
from hashlib import sha256
from io import StringIO
from itertools import islice
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect, status
from fastapi.responses import RedirectResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from masterone.auth.security import (
    CurrentSession,
    check_capability,
    current_session,
    require_capability,
    require_mutation_capability,
    require_origin,
)
from masterone.config import get_settings
from masterone.db.session import get_session
from masterone.llm.explanation import explain_cohort
from masterone.llm.policy import digest
from masterone.mdm.benchmark import run_benchmark
from masterone.mdm.engine_adapter import EngineResultAdapter, FrontendJSONRoute, api_engine_context
from masterone.mdm.models import (
    ResolutionAuditEvent,
    ResolutionEntity,
    ResolutionEntityMember,
    ResolutionEntityVersion,
    ResolutionExplanation,
    ResolutionExport,
    ResolutionPair,
    ResolutionProposal,
    ResolutionRecord,
    ResolutionReviewCase,
    ResolutionRun,
    ResolutionTask,
)
from masterone.mdm.ownership import owned_run
from masterone.mdm.pipeline.blocking import generate_candidates
from masterone.mdm.pipeline.policy_engine import _DEFAULT_YAML_PATH, POLICY_FILE_VERSION, simulate_policy
from masterone.mdm.resolution_analytics import session_analytics
from masterone.mdm.resolution_items import (
    _review_groups,
    create_export,
    export_readiness,
    get_item,
    list_items,
    list_separate_records,
    resolve_staged_conflict,
    source_csv,
    transition_review,
)
from masterone.mdm.resolution_pairs import decide_review_pair, pair_categories
from masterone.mdm.resolution_preview import execute_preview, prepare_preview
from masterone.mdm.resolution_split import execute_split, list_merge_events, prepare_split
from masterone.mdm.run_summary import summary_for
from masterone.mdm.schemas import ResolutionOverviewRead, task_read
from masterone.storage.r2 import get_store


def legacy_mutation_guard(request: Request, db: Session = Depends(get_session)):
    run_id = request.path_params.get('run_id')
    run = db.get(ResolutionRun, run_id) if run_id else None
    if run is not None and run.workflow_version == 2 and request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
        from masterone.mdm.workflow_service import conflict
        # Snapshot export boundaries validate finalized state themselves.
        suffix = request.url.path.split('/runs/' + run_id, 1)[-1]
        if not (suffix in ('/exports', '/previews', '/export-readiness') or
                (suffix.startswith('/previews/') and suffix.endswith('/execute'))):
            conflict('USE_WORKFLOW_API')


router = APIRouter(prefix="/mdm/runs", tags=["MDM resolution"], dependencies=[Depends(api_engine_context), Depends(legacy_mutation_guard)], route_class=FrontendJSONRoute)


@router.get("/{run_id}/analytics")
def get_session_analytics(run_id: str, db: Session = Depends(get_session),
                          auth: CurrentSession = Depends(require_capability("read"))):
    run = owned_run(db, run_id, auth.user.id)
    if run.status != "COMPLETED":
        raise HTTPException(409, "Số liệu tổng hợp có sau khi phiên xử lý xong")
    return session_analytics(db, run_id)


def _category_key(primary_group: str, subgroup: str) -> str:
    return sha256(f"{primary_group}:{subgroup}".encode()).hexdigest()


class ReviewTransition(BaseModel):
    item_ids: list[str] = Field(min_length=1)
    decision: Literal["merge", "no_merge"]


class PairDecisionRequest(BaseModel):
    decision: Literal["merge", "no_merge"]
    revision: int = Field(ge=1)


@router.get("/{run_id}/pair-categories")
def get_pair_categories(run_id: str, db: Session = Depends(get_session),
                        auth: CurrentSession = Depends(require_capability("read"))):
    owned_run(db, run_id, auth.user.id)
    return pair_categories(db, run_id)


@router.post("/{run_id}/review-pairs/{pair_id}/decision")
def post_pair_decision(run_id: str, pair_id: str, request: PairDecisionRequest,
                       db: Session = Depends(get_session),
                       auth: CurrentSession = Depends(require_mutation_capability("read"))):
    check_capability(auth, "approve_pair" if request.decision == "merge" else "reject_pair")
    owned_run(db, run_id, auth.user.id)
    return decide_review_pair(db, run_id, pair_id, request.decision, request.revision, auth.user.id)


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
              db: Session = Depends(get_session), auth: CurrentSession = Depends(require_capability("read"))):
    owned_run(db, run_id, auth.user.id)
    return list_items(db, run_id, bucket, offset, limit, origin, primary_group, subgroup, signature_hash)


@router.get("/{run_id}/separate-records")
def get_separate_records(run_id: str, offset: int = Query(0, ge=0),
                         limit: int = Query(25, ge=1, le=100), q: str = Query("", max_length=100),
                         db: Session = Depends(get_session),
                         auth: CurrentSession = Depends(require_capability("read"))):
    owned_run(db, run_id, auth.user.id)
    return list_separate_records(db, run_id, offset, limit, q)


@router.get("/{run_id}/items/{bucket}/{item_id}")
def get_item_detail(run_id: str, bucket: str, item_id: str, signature_hash: str | None = None,
                    primary_group: str | None = None, subgroup: str | None = None,
                    db: Session = Depends(get_session), auth: CurrentSession = Depends(require_capability("read"))):
    owned_run(db, run_id, auth.user.id)
    return get_item(db, run_id, bucket, item_id, signature_hash, primary_group, subgroup)


@router.post("/{run_id}/review-items/transition")
def post_review_transition(run_id: str, request: ReviewTransition,
                           db: Session = Depends(get_session),
                           auth: CurrentSession = Depends(require_mutation_capability("read"))):
    check_capability(auth, "approve_pair" if request.decision == "merge" else "reject_pair")
    owned_run(db, run_id, auth.user.id)
    return transition_review(db, run_id, request.item_ids, request.decision, auth.user.id)


@router.post("/{run_id}/merge-items/{item_id}/resolve-conflict")
def post_resolve_staged_conflict(run_id: str, item_id: str,
                                 db: Session = Depends(get_session),
                                 auth: CurrentSession = Depends(require_mutation_capability("reject_pair"))):
    owned_run(db, run_id, auth.user.id)
    return resolve_staged_conflict(db, run_id, item_id, auth.user.id)


@router.post("/{run_id}/export-readiness")
def get_export_readiness(run_id: str, request: ExportRequest, db: Session = Depends(get_session),
                         auth: CurrentSession = Depends(require_capability("read"))):
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
    if snapshot.csv_bytes is None and snapshot.r2_key:
        from masterone.mdm.export_stream import artifact_key, artifact_store
        store = (artifact_store(get_settings(), snapshot.r2_key)
                 if snapshot.r2_key.startswith("local:") else get_store(get_settings()))
        if store is None:
            raise HTTPException(503, "Export storage unavailable")
        return StreamingResponse(store.iter_chunks(artifact_key(snapshot.r2_key)),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="masterone-{snapshot.run_id[:8]}-{snapshot.id[:8]}.csv"',
                     "X-Export-Id": snapshot.id})
    return Response(_csv_bytes(snapshot), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="masterone-{snapshot.run_id[:8]}-{snapshot.id[:8]}.csv"',
                             "X-Export-Id": snapshot.id})


def _csv_sample(snapshot: ResolutionExport, limit: int = 10) -> list[dict[str, str]]:
    if snapshot.csv_bytes is not None:
        return list(islice(csv.DictReader(StringIO(snapshot.csv_bytes.decode("utf-8-sig"))), limit))
    if snapshot.r2_key:
        from masterone.mdm.export_stream import artifact_key, artifact_store
        store = (artifact_store(get_settings(), snapshot.r2_key)
                 if snapshot.r2_key.startswith("local:") else get_store(get_settings()))
        if store is not None:
            with TemporaryDirectory(prefix="masterone-export-compare-") as temp_dir:
                path = Path(temp_dir) / "snapshot.csv"
                store.download_file(artifact_key(snapshot.r2_key), path)
                with path.open("r", encoding="utf-8-sig", newline="") as stream:
                    return list(islice(csv.DictReader(stream), limit))
    raise HTTPException(500, "Export data unavailable")


@router.post("/{run_id}/exports")
def post_export(run_id: str, request: ExportRequest, db: Session = Depends(get_session),
                auth: CurrentSession = Depends(require_mutation_capability("export_data")),
                execute_request_id: str | None = Header(default=None, min_length=1, max_length=80,
                                                       alias="Idempotency-Key")):
    run = owned_run(db, run_id, auth.user.id)
    if run.policy_version == "person-linh-v1":
        raise HTTPException(409, "Hãy xem trước và xác nhận bản gộp trước khi xuất CSV")
    from uuid import NAMESPACE_URL, uuid5
    export_id = (str(uuid5(NAMESPACE_URL, f"export:{run_id}:{auth.user.id}:{execute_request_id}"))
                 if execute_request_id else None)
    return _csv_response(create_export(db, run_id, auth.user.id, request.acknowledge_unreviewed,
                                       request.choices, export_id=export_id))


@router.post("/{run_id}/previews")
def post_preview(run_id: str, request: ExportRequest,
                 db: Session = Depends(get_session),
                 auth: CurrentSession = Depends(require_mutation_capability("export_data"))):
    owned_run(db, run_id, auth.user.id)
    return prepare_preview(db, run_id, auth.user.id, request.choices, request.acknowledge_unreviewed)


@router.post("/{run_id}/previews/{preview_id}/execute")
def post_execute_preview(run_id: str, preview_id: str, request: ExecutePreviewRequest,
                         db: Session = Depends(get_session),
                         auth: CurrentSession = Depends(require_mutation_capability("export_data"))):
    owned_run(db, run_id, auth.user.id)
    return _csv_response(execute_preview(db, run_id, preview_id, request.execute_request_id, auth.user.id))


@router.get("/{run_id}/exports")
def get_exports(run_id: str, db: Session = Depends(get_session),
                auth: CurrentSession = Depends(require_capability("read"))):
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
                      db: Session = Depends(get_session), auth: CurrentSession = Depends(require_capability("read"))):
    owned_run(db, run_id, auth.user.id)
    return list_merge_events(db, run_id, export_id, offset, limit, q)


@router.get("/{run_id}/exports/{export_id}/merges/{event_id}/split-preview")
def get_split_preview(run_id: str, export_id: str, event_id: str, member_id: str,
                      db: Session = Depends(get_session),
                      auth: CurrentSession = Depends(require_capability("selective_unmerge"))):
    owned_run(db, run_id, auth.user.id)
    return prepare_split(db, run_id, export_id, event_id, member_id)


@router.post("/{run_id}/exports/{export_id}/merges/{event_id}/split")
def post_split(run_id: str, export_id: str, event_id: str, request: ExecuteSplitRequest,
               db: Session = Depends(get_session),
               auth: CurrentSession = Depends(require_mutation_capability("selective_unmerge"))):
    owned_run(db, run_id, auth.user.id)
    snapshot = execute_split(db, run_id, export_id, event_id, request.member_id,
                             request.version, request.execute_request_id, auth.user.id)
    return {"export_id": snapshot.id, "row_count": snapshot.row_count,
            "parent_export_id": snapshot.parent_export_id}


@router.get("/{run_id}/source-records.csv")
def download_source_records(run_id: str, db: Session = Depends(get_session),
                            auth: CurrentSession = Depends(require_capability("export_data"))):
    owned_run(db, run_id, auth.user.id)
    return Response(source_csv(db, run_id), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="masterone-{run_id[:8]}-source.csv"'})


@router.get("/{run_id}/exports/{export_id}/download")
def download_export(run_id: str, export_id: str, db: Session = Depends(get_session),
                    auth: CurrentSession = Depends(require_capability("export_data"))):
    owned_run(db, run_id, auth.user.id)
    snapshot = db.get(ResolutionExport, export_id)
    if snapshot is None or snapshot.run_id != run_id:
        raise HTTPException(404, "Không tìm thấy bản xuất")
    # When file is in R2, redirect to presigned URL — avoids pulling bytes through the app server
    if snapshot.r2_key and snapshot.csv_bytes is None and not snapshot.r2_key.startswith("local:"):
        r2 = get_store(get_settings())
        if r2 is not None:
            try:
                present = r2.exists(snapshot.r2_key)
            except Exception as error:
                # Fail closed without exposing SDK credentials/response text.
                raise HTTPException(503, "Không kiểm tra được file xuất; hãy thử lại") from error
            if not present:
                raise HTTPException(409, "File xuất không còn tồn tại; cần tạo lại bản xuất")
            url = r2.presigned_url(snapshot.r2_key, expires_in=300)
            return RedirectResponse(url, status_code=302)
    return _csv_response(snapshot)


@router.get("/{run_id}/exports/{export_id}/compare")
def compare_export(run_id: str, export_id: str, db: Session = Depends(get_session),
                   auth: CurrentSession = Depends(require_capability("read"))):
    run = owned_run(db, run_id, auth.user.id)
    snapshot = db.get(ResolutionExport, export_id)
    if snapshot is None or snapshot.run_id != run_id:
        raise HTTPException(404, "Không tìm thấy bản xuất")
    source_rows = db.query(ResolutionRecord).filter_by(run_id=run_id).order_by(
        ResolutionRecord.id).limit(10).all()
    source_sample = [{"id": record.external_id, "source_system": record.source_system,
                      "full_name": record.raw_fields.get("name", ""),
                      "phone_number": record.raw_fields.get("phone", ""),
                      "email_address": record.raw_fields.get("email", ""),
                      "raw_address": record.raw_fields.get("address", "")}
                     for record in source_rows]
    return {"source_count": run.record_count, "output_count": snapshot.row_count,
            "source_sample": source_sample, "output_sample": _csv_sample(snapshot)}


def _record_summary(record: ResolutionRecord) -> dict:
    return {"id": record.id, "external_id": record.external_id, "source_system": record.source_system,
            "raw_fields": record.raw_fields, "normalized_fields": record.normalized_fields,
            "quality_flags": record.quality_flags}


@router.get("/{run_id}/overview", response_model=ResolutionOverviewRead)
def get_overview(run_id: str, db: Session = Depends(get_session),
                 auth: CurrentSession = Depends(require_capability("read"))):
    run = owned_run(db, run_id, auth.user.id)
    task = db.query(ResolutionTask).filter_by(run_id=run.id).one_or_none()
    # One stored row holds the figures; it is recomputed only when pairs, proposals or review cases changed.
    summary = summary_for(db, run)
    payload = summary.payload
    counts = {**payload["counts"], "insufficient": run.insufficient_count}
    history_count = db.query(ResolutionExport).filter_by(run_id=run_id).count()
    return {"run_id": run.id, "status": run.status, "phase": task.phase if task else run.phase,
            "processed_count": task.processed_count if task else 0,
            "total_count": task.total_count if task else run.record_count,
            "record_count": run.record_count, "file_count": run.file_count,
            "source_counts": summary.source_counts, "history_count": history_count,
            "candidate_count": run.candidate_count,
            "estimated_entity_count": payload["estimated_entity_count"],
            "duplicate_record_count": payload["duplicate_record_count"],
            "eligible_merge_group_count": payload["eligible_merge_group_count"],
            "counts": counts,
            "task": task_read(task) if task else None}


@router.get("/{run_id}/audit")
def get_run_audit(run_id: str, limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0),
                  action: str | None = Query(None, max_length=40), pair_id: str | None = Query(None, max_length=64),
                  proposal_id: str | None = Query(None, max_length=64), db: Session = Depends(get_session),
                  auth: CurrentSession = Depends(require_capability("read_audit"))):
    owned_run(db, run_id, auth.user.id)
    query = db.query(ResolutionAuditEvent).filter_by(run_id=run_id)
    if action and action.strip():
        query = query.filter(ResolutionAuditEvent.action == action.strip().upper())
    if pair_id:
        query = query.filter_by(pair_id=pair_id)
    if proposal_id:
        query = query.filter_by(proposal_id=proposal_id)
    total = query.count()
    events = query.order_by(ResolutionAuditEvent.created_at.desc(), ResolutionAuditEvent.id.desc()).offset(offset).limit(limit).all()
    return {"total": total, "items": [{"id": event.id, "run_id": event.run_id,
            "actor_user_id": event.actor_user_id, "pair_id": event.pair_id,
            "proposal_id": event.proposal_id, "action": event.action,
            "details": event.details, "created_at": event.created_at} for event in events]}


@router.get("/{run_id}/categories")
def get_categories(run_id: str, db: Session = Depends(get_session),
                   auth: CurrentSession = Depends(require_capability("read"))):
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


def _explanation_context(db, run_id, owner_user_id):
    """The aggregate is the whole explanation input, so freshness needs the cheap fence, never a run scan."""
    from masterone.llm.policy import fence
    return {"run_id": run_id, "user_id": owner_user_id, "fence": fence(db, run_id),
            "input_hash": "", "review_hash": "", "reference_version": "", "reference_hash": ""}


def _explanation_model_identity(settings):
    from masterone.llm.gateway import PROMPT_VERSION, SCHEMA_VERSION
    from masterone.llm.policy import digest
    return digest([settings.gemini_model, settings.openrouter_model, settings.llm_policy_revision,
                   PROMPT_VERSION["EXPLANATION"], SCHEMA_VERSION["EXPLANATION"]])


def _explanation_payload(row):
    return {"status": row.status, "explanation": row.explanation,
            "model_version": row.model_version, "usage": row.usage, "error_code": row.error_code}


def _guarded_explanation(db, run_id, signature, primary_group, subgroup, aggregate):
    """READY results are cached by aggregate identity. TEMPLATE and STALE are never served as READY."""
    from masterone.db.base import now_utc
    from masterone.llm.explanation import template_explanation
    from masterone.llm.policy import factory_for
    settings = get_settings()
    row = db.query(ResolutionExplanation).filter_by(run_id=run_id, signature_hash=signature).one_or_none()
    if row is None:
        row = ResolutionExplanation(run_id=run_id, signature_hash=signature, primary_group=primary_group,
                                    subgroup=subgroup, status="PENDING")
        db.add(row)
    if row.status == "READY" and row.updated_at is not None:
        updated = row.updated_at if row.updated_at.tzinfo else row.updated_at.replace(tzinfo=now_utc().tzinfo)
        if updated + timedelta(seconds=settings.llm_cache_ttl_seconds) >= now_utc():
            return _explanation_payload(row)
    if not settings.llm_enabled or not settings.llm_explanation_enabled:
        result = template_explanation("LLM_DISABLED")
    else:
        run = db.get(ResolutionRun, run_id)
        context = _explanation_context(db, run_id, run.owner_user_id)
        factory = factory_for(db.get_bind().url.render_as_string(hide_password=False))
        db.commit()  # the provider call must not hold this request's transaction open
        result = explain_cohort(aggregate, settings, context=context, factory=factory)
    if result["status"] == "STALE":
        return {"status": "STALE", "explanation": None, "model_version": None, "usage": {}}
    row = db.query(ResolutionExplanation).filter_by(run_id=run_id, signature_hash=signature).one()
    row.status = result["status"]
    row.explanation = result["explanation"]
    row.model_version = result["model_version"]
    row.usage = result.get("usage", {})
    row.error_code = result.get("error_code")
    db.commit()
    return _explanation_payload(row)


@router.post("/{run_id}/categories/{signature_hash}/explanation")
def explain_category(run_id: str, signature_hash: str, db: Session = Depends(get_session),
                     auth: CurrentSession = Depends(require_mutation_capability("explain"))):
    owned_run(db, run_id, auth.user.id)
    cases = [case for case in db.query(ResolutionReviewCase).filter_by(run_id=run_id, status="OPEN").all()
             if _category_key(case.primary_group, case.subgroup) == signature_hash]
    if not cases:
        raise HTTPException(404, "Review cohort not found")
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
    signature = sha256(f"category:{signature_hash}:{digest(aggregate)}:{_explanation_model_identity(get_settings())}"
                       .encode()).hexdigest()
    return _guarded_explanation(db, run_id, signature, cases[0].primary_group, cases[0].subgroup, aggregate)


@router.post("/{run_id}/items/{bucket}/{item_id}/explanation")
def explain_item(run_id: str, bucket: str, item_id: str,
                 db: Session = Depends(get_session),
                 auth: CurrentSession = Depends(require_mutation_capability("explain"))):
    """Explain one item from comparison states; never send source values to AI."""
    owned_run(db, run_id, auth.user.id)
    detail = get_item(db, run_id, bucket, item_id)
    pair_ids = [pair["id"] for pair in detail["pairs"]]
    pairs = db.query(ResolutionPair).filter(
        ResolutionPair.run_id == run_id, ResolutionPair.id.in_(pair_ids)).all()
    version = "|".join(sorted(f"{pair.id}:{pair.revision}" for pair in pairs))
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
    signature = sha256(f"item:{bucket}:{item_id}:{version}:{_explanation_model_identity(get_settings())}"
                       .encode()).hexdigest()
    return _guarded_explanation(db, run_id, signature, "ITEM", "ITEM", aggregate)


# ── Golden record endpoints for Steward ──────────────────────────────────────

class GoldenRecordOverride(BaseModel):
    fields: dict[str, str] = Field(default_factory=dict,
        description='Map field to value; empty dict clears the override')


_GOLDEN_FIELDS = frozenset({'name', 'phone', 'email', 'address', 'dob', 'government_id'})


@router.get('/{run_id}/proposals/{proposal_id}/golden-record')
def get_golden_record(run_id: str, proposal_id: str,
                      db: Session = Depends(get_session),
                      auth: CurrentSession = Depends(require_capability("read"))):
    owned_run(db, run_id, auth.user.id)
    proposal = db.get(ResolutionProposal, proposal_id)
    if proposal is None or proposal.run_id != run_id:
        raise HTTPException(404, 'Proposal not found')
    gf = EngineResultAdapter.golden(proposal.golden_fields)
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
                      auth: CurrentSession = Depends(require_mutation_capability("edit_golden"))):
    owned_run(db, run_id, auth.user.id)
    # Serialize with manual merge and generation publication before reading
    # the proposal/override, so a confirmed edit cannot land on stale members.
    db.query(ResolutionRun).filter_by(id=run_id).with_for_update().one()
    proposal = db.query(ResolutionProposal).filter_by(id=proposal_id, run_id=run_id).with_for_update().one_or_none()
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
        # A prior manual merge can materialize the override into base fields.
        # Explicit clearing must restore survivorship, not retain that value.
        from masterone.mdm.pipeline.golden import build_golden
        from masterone.mdm.pipeline.golden_v2 import golden_payload
        records = db.query(ResolutionRecord).filter(
            ResolutionRecord.run_id == run_id,
            ResolutionRecord.id.in_(proposal.member_record_ids)).all()
        gf = build_golden(sorted(proposal.member_record_ids),
                          {row.id: golden_payload(row) for row in records})
    proposal.golden_fields = gf
    proposal.revision += 1
    db.add(ResolutionAuditEvent(
        run_id=run_id, actor_user_id=auth.user.id, proposal_id=proposal_id,
        action='GOLDEN_RECORD_OVERRIDE',
        details={'fields': request.fields, 'cleared': not request.fields},
    ))
    db.commit()
    return {'proposal_id': proposal_id, 'steward_override': request.fields}


# ── §SVC-07 Benchmark endpoint ────────────────────────────────────────────────

@router.post("/{run_id}/benchmark")
def run_benchmark_endpoint(
    run_id: str,
    ground_truth_field: str = Query(default="benchmark_entity_id", description="CSV column used as ground truth label"),
    db: Session = Depends(get_session),
    auth: CurrentSession = Depends(require_capability("read")),
):
    """Compute benchmark metrics for a completed run.

    Requires that records were imported with a `benchmark_entity_id` column (or equivalent).
    Role required: any role with `read` capability.
    """
    run = owned_run(db, run_id, auth.user.id)
    if run.status != "COMPLETED":
        raise HTTPException(409, "Benchmark chỉ khả dụng sau khi phiên hoàn thành (status=COMPLETED)")

    records = [
        {
            "id": r.id,
            "external_id": r.external_id,
            "source_system": r.source_system,
            "entity_type": r.entity_type,
            "normalized_fields": r.normalized_fields,
            "raw_fields": r.raw_fields,
            "quality_flags": r.quality_flags,
            "benchmark_entity_id": r.benchmark_entity_id,
        }
        for r in db.query(ResolutionRecord).filter_by(run_id=run_id).order_by(ResolutionRecord.id).all()
    ]

    if not any(r.get("benchmark_entity_id") for r in records):
        raise HTTPException(
            422,
            f"Không có record nào có field '{ground_truth_field}'. "
            "Import CSV cần cột benchmark_entity_id để chạy benchmark."
        )

    pairs = [
        {
            "left_id": p.left_record_id,
            "right_id": p.right_record_id,
            "decision": p.decision,
            "subgroup": p.subgroup,
            "primary_group": p.primary_group,
            "match_weight": float(p.match_weight) if p.match_weight else 0.0,
        }
        for p in db.query(ResolutionPair).filter_by(run_id=run_id).all()
    ]

    proposals = [
        {
            "member_ids": p.member_ids,
            "golden_fields": p.golden_fields,
        }
        for p in db.query(ResolutionProposal).filter_by(run_id=run_id).all()
    ]

    candidate_blocks = generate_candidates(records)
    result = run_benchmark(records, candidate_blocks, pairs, proposals)

    def _metrics(obj) -> dict | None:
        if obj is None:
            return None
        return {k: v for k, v in obj.__dict__.items()}

    return {
        "run_id": run_id,
        "record_count": result.record_count,
        "pair_count": result.pair_count,
        "candidate": _metrics(result.candidate),
        "pair": _metrics(result.pair),
        "auto_merge": _metrics(result.auto_merge),
        "cluster": _metrics(result.cluster),
        "per_subgroup": {sg: _metrics(m) for sg, m in result.per_subgroup.items()},
    }


# ── §SVC-06 Policy simulate (TASK-6.4) ───────────────────────────────────────

@router.get("/runs/policies/current")
def get_current_policy(
    auth: CurrentSession = Depends(require_capability("read")),
):
    """Return the active policy YAML content and version."""
    try:
        yaml_content = _DEFAULT_YAML_PATH.read_text(encoding="utf-8")
    except Exception:
        yaml_content = ""
    return {"yaml_content": yaml_content, "version": POLICY_FILE_VERSION}


class PolicySimulateRequest(BaseModel):
    run_id: str = Field(..., description="ID của run cần simulate")
    policy_yaml: str = Field(..., description="Nội dung YAML policy mới muốn test")


@router.post("/policies/simulate")
def policy_simulate(
    request: PolicySimulateRequest,
    db: Session = Depends(get_session),
    auth: CurrentSession = Depends(require_capability("write")),
):
    """Simulate impact of a new policy YAML against an existing run's pairs.

    Returns a diff: how many pairs would change decision.
    POLICY_ADMIN role recommended; does NOT modify any data.
    """
    owned_run(db, request.run_id, auth.user.id)

    db_pairs = db.query(ResolutionPair).filter_by(run_id=request.run_id).all()
    if not db_pairs:
        raise HTTPException(404, "Run không có pair nào để simulate")

    pair_inputs = []
    for p in db_pairs:
        comparisons = {}
        for ev in (p.field_evidence or []):
            field = ev.get("field")
            if field:
                comparisons[field] = {"state": ev.get("state", "MI"),
                                      "normalization_tags": ev.get("tags", [])}
        pair_inputs.append({
            "pair_id": p.id,
            "primary_group": p.primary_group,
            "match_weight": float(p.score or 0.0),
            "comparisons": comparisons,
            "nf_left": {},
            "nf_right": {},
            "current_decision": p.triage_status,
        })

    result = simulate_policy(pair_inputs, request.policy_yaml)

    return {
        "run_id": request.run_id,
        "policy_yaml_length": len(request.policy_yaml),
        "total_pairs": result.total_pairs,
        "changed": result.changed,
        "review_to_merge": result.review_to_merge,
        "merge_to_review": result.merge_to_review,
        "unchanged_merge": result.unchanged_merge,
        "unchanged_review": result.unchanged_review,
        "details": result.details[:100],  # cap at 100 items
    }


# ── §SVC-01 Entity view + history (TASK-9.1) ─────────────────────────────────

@router.get("/{run_id}/entities/{entity_id}")
def get_entity(
    run_id: str,
    entity_id: str,
    db: Session = Depends(get_session),
    auth: CurrentSession = Depends(require_capability("read")),
):
    """Return entity detail: members + latest golden fields."""
    owned_run(db, run_id, auth.user.id)
    entity = db.query(ResolutionEntity).filter_by(id=entity_id, run_id=run_id).first()
    if entity is None:
        raise HTTPException(404, "Entity không tìm thấy")

    members = db.query(ResolutionEntityMember).filter_by(entity_id=entity_id).all()
    member_ids = [m.record_id for m in members]

    latest_version = db.query(ResolutionEntityVersion).filter_by(
        entity_id=entity_id, version=entity.version
    ).first()

    return {
        "entity_id": entity_id,
        "run_id": run_id,
        "status": entity.status,
        "entity_type": entity.entity_type,
        "version": entity.version,
        "member_record_ids": member_ids,
        "golden_fields": latest_version.golden_fields if latest_version else {},
        "created_at": entity.created_at.isoformat() if entity.created_at else None,
        "updated_at": entity.updated_at.isoformat() if entity.updated_at else None,
    }


@router.get("/{run_id}/entities/{entity_id}/history")
def get_entity_history(
    run_id: str,
    entity_id: str,
    db: Session = Depends(get_session),
    auth: CurrentSession = Depends(require_capability("read")),
):
    """Return entity version history timeline."""
    owned_run(db, run_id, auth.user.id)
    entity = db.query(ResolutionEntity).filter_by(id=entity_id, run_id=run_id).first()
    if entity is None:
        raise HTTPException(404, "Entity không tìm thấy")

    versions = (
        db.query(ResolutionEntityVersion)
        .filter_by(entity_id=entity_id)
        .order_by(ResolutionEntityVersion.version)
        .all()
    )

    return {
        "entity_id": entity_id,
        "run_id": run_id,
        "current_version": entity.version,
        "history": [
            {
                "version": v.version,
                "changed_by": v.changed_by,
                "golden_fields": v.golden_fields,
                "created_at": v.created_at.isoformat() if v.created_at else None,
            }
            for v in versions
        ],
    }


# Separate router: the HTTP router's engine dependency takes a Request, which WebSocket routes never receive.
ws_router = APIRouter(prefix="/mdm/runs", tags=["MDM resolution"])


@ws_router.websocket("/{run_id}/overview/ws")
async def overview_ws(websocket: WebSocket, run_id: str):
    # Mirrors GET /overview: Origin, session cookie, read capability and run ownership.
    with websocket.app.state.session_factory() as db:
        try:
            require_origin(websocket)
            auth = current_session(websocket, db)
            check_capability(auth, "read")
            owned_run(db, run_id, auth.user.id)
        except HTTPException:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        await websocket.accept()
        last_sent, last_sent_at = None, 0.0
        try:
            while True:
                db.rollback()  # Pick up progress committed by the background run
                snapshot = await asyncio.to_thread(get_overview, run_id, db, auth)
                payload = ResolutionOverviewRead.model_validate(snapshot).model_dump(mode="json", by_alias=True)
                # Send on change, plus a heartbeat, instead of the full payload every tick.
                encoded = json.dumps(payload, sort_keys=True, default=str)
                now = time.monotonic()
                if encoded != last_sent or now - last_sent_at >= 15:
                    await websocket.send_json(payload)
                    last_sent, last_sent_at = encoded, now
                await asyncio.sleep(2)
        except WebSocketDisconnect:
            return
        except HTTPException:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
