import base64
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from masterone.auth.security import CurrentSession, require_capability, require_mutation_capability
from masterone.db.base import now_utc
from masterone.db.session import get_session
from masterone.mdm.models import ResolutionAuditEvent, ResolutionFile, ResolutionRecord, ResolutionRun, ResolutionTask
from masterone.mdm.ownership import owned_run
from masterone.mdm.resolution_import import create_resolution_run_from_streams
from masterone.mdm.resolution_service import create_task
from masterone.mdm.schemas import ResolutionTaskRead, task_read

router = APIRouter(prefix="/mdm/runs", tags=["MDM resolution runs"])


class UpdateRunRequest(BaseModel):
    name: str | None = Field(default=None, max_length=255)


class BulkRunRequest(BaseModel):
    operation: Literal["archive", "restore"]
    run_ids: list[str] = Field(min_length=1, max_length=100)


def _encode_cursor(run: ResolutionRun) -> str:
    raw = f"{run.created_at.isoformat()}\n{run.id}".encode()
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode("utf-8")
        created_at, run_id = raw.split("\n", 1)
        return datetime.fromisoformat(created_at), run_id
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(422, "Cursor phiên không hợp lệ") from exc


@router.get("")
def list_runs(limit: int = 25, offset: int = 0, cursor: str | None = None,
              q: str = "", run_status: str | None = None, archived: str = "active",
              db: Session = Depends(get_session),
              auth: CurrentSession = Depends(require_capability("read"))):
    limit = min(max(limit, 1), 100)
    offset = max(offset, 0)
    query = db.query(ResolutionRun).filter_by(owner_user_id=auth.user.id)
    if archived not in {"active", "archived", "all"}:
        raise HTTPException(422, "archived must be active, archived, or all")
    if archived == "active":
        query = query.filter(ResolutionRun.archived_at.is_(None))
    elif archived == "archived":
        query = query.filter(ResolutionRun.archived_at.is_not(None))
    if run_status:
        statuses = {value.strip().upper() for value in run_status.split(",") if value.strip()}
        if statuses:
            query = query.filter(ResolutionRun.status.in_(statuses))
    search = q.strip()[:200]
    if search:
        pattern = f"%{search}%"
        matching_file = db.query(ResolutionFile.id).filter(
            ResolutionFile.run_id == ResolutionRun.id,
            ResolutionFile.filename.ilike(pattern),
        ).exists()
        query = query.filter(or_(ResolutionRun.name.ilike(pattern), matching_file))
    total = query.count()
    page_query = query.order_by(ResolutionRun.created_at.desc(), ResolutionRun.id.desc())
    if cursor:
        cursor_created, cursor_id = _decode_cursor(cursor)
        page_query = page_query.filter(or_(ResolutionRun.created_at < cursor_created,
                                           (ResolutionRun.created_at == cursor_created) & (ResolutionRun.id < cursor_id)))
    elif offset:
        page_query = page_query.offset(offset)
    fetched = page_query.limit(limit + 1).all()
    has_more = len(fetched) > limit
    runs = fetched[:limit]
    files_by_run: dict[str, list[str]] = {run.id: [] for run in runs}
    if runs:
        rows = db.query(ResolutionFile.run_id, ResolutionFile.filename).filter(
            ResolutionFile.run_id.in_(files_by_run)).order_by(ResolutionFile.filename).all()
        for run_id, filename in rows:
            files_by_run[run_id].append(filename)
    return {
        "total": total,
        "next_cursor": _encode_cursor(runs[-1]) if has_more and runs else None,
        "items": [{"run_id": run.id, "name": run.name, "archived_at": run.archived_at,
                   "status": run.status, "phase": run.phase, "created_at": run.created_at,
                   "record_count": run.record_count, "file_count": run.file_count,
                   "files": files_by_run[run.id]} for run in runs],
    }


@router.patch("/{run_id}")
def update_run(run_id: str, body: UpdateRunRequest, db: Session = Depends(get_session),
               auth: CurrentSession = Depends(require_mutation_capability("manage_run"))):
    run = owned_run(db, run_id, auth.user.id)
    name = (body.name or "").strip() or None
    if name and len(name) > 255:
        raise HTTPException(422, "Tên phiên tối đa 255 ký tự")
    previous = run.name
    run.name = name
    db.add(ResolutionAuditEvent(run_id=run.id, actor_user_id=auth.user.id,
                                action="SESSION_RENAMED", details={"from": previous, "to": name}))
    db.commit()
    return {"run_id": run.id, "name": run.name}


@router.post("/bulk")
def bulk_run_action(body: BulkRunRequest, db: Session = Depends(get_session),
                    auth: CurrentSession = Depends(require_mutation_capability("manage_run"))):
    run_ids = list(dict.fromkeys(body.run_ids))
    runs = db.query(ResolutionRun).filter(ResolutionRun.owner_user_id == auth.user.id,
                                          ResolutionRun.id.in_(run_ids)).all()
    if len(runs) != len(run_ids):
        raise HTTPException(404, "Có phiên không tồn tại hoặc bạn không có quyền thao tác")
    if body.operation == "archive" and any(run.status in {"QUEUED", "RUNNING"} for run in runs):
        raise HTTPException(409, "Không thể lưu trữ phiên đang chờ hoặc đang xử lý")
    archived_at = now_utc() if body.operation == "archive" else None
    action = "SESSION_ARCHIVED" if body.operation == "archive" else "SESSION_RESTORED"
    for run in runs:
        run.archived_at = archived_at
        db.add(ResolutionAuditEvent(run_id=run.id, actor_user_id=auth.user.id,
                                    action=action, details={"source": "sessions_hub"}))
    db.commit()
    return {"changed_count": len(runs), "operation": body.operation, "run_ids": run_ids}


@router.post("", status_code=status.HTTP_201_CREATED)
def create_run(
    files: list[UploadFile] = File(...),
    workflow_version: int = Form(1, ge=1, le=2),
    db: Session = Depends(get_session),
    auth: CurrentSession = Depends(require_mutation_capability("create_run")),
):
    from masterone.mdm.workflow_service import validate_version
    validate_version(workflow_version)
    run, profile = create_resolution_run_from_streams(
        db, [(file.filename or "records.csv", file.file) for file in files], auth.user.id,
        workflow_version=workflow_version)
    return {"run_id": run.id, "status": run.status, "profile": profile}


@router.get("/tasks/{task_id}", response_model=ResolutionTaskRead)
def get_task(
    task_id: str,
    db: Session = Depends(get_session),
    auth: CurrentSession = Depends(require_capability("read")),
):
    task = db.get(ResolutionTask, task_id)
    if task is None:
        raise HTTPException(404, "Task not found")
    owned_run(db, task.run_id, auth.user.id)
    return task_read(task)


@router.post("/{run_id}/cancel", response_model=ResolutionTaskRead)
def cancel_run(run_id: str, request: Request, db: Session = Depends(get_session),
               auth: CurrentSession = Depends(require_mutation_capability("start_run"))):
    from masterone.mdm.job_queue import request_cancel
    owned_run(db, run_id, auth.user.id)
    task = request_cancel(request.app.state.session_factory, run_id)
    if task is None:
        raise HTTPException(404, "Task not found")
    return task_read(task)


@router.get("/{run_id}")
def get_run(
    run_id: str,
    db: Session = Depends(get_session),
    auth: CurrentSession = Depends(require_capability("read")),
):
    run = owned_run(db, run_id, auth.user.id)
    sources = db.query(ResolutionRecord.source_system).filter_by(run_id=run.id).all()
    counts: dict[str, int] = {}
    for (source,) in sources:
        counts[source] = counts.get(source, 0) + 1
    return {"run_id": run.id, "status": run.status, "created_at": run.created_at,
            "record_count": run.record_count, "file_count": run.file_count, "source_counts": counts}


@router.post("/{run_id}/start", status_code=status.HTTP_202_ACCEPTED)
def start_run(
    run_id: str,
    request: Request,
    db: Session = Depends(get_session),
    auth: CurrentSession = Depends(require_mutation_capability("start_run")),
):
    run = owned_run(db, run_id, auth.user.id)
    from masterone.mdm.workflow_service import require_legacy
    require_legacy(db, run_id)
    if run.archived_at is not None:
        raise HTTPException(409, "Khôi phục phiên đã lưu trữ trước khi chạy")
    previous_task = db.query(ResolutionTask).filter_by(run_id=run_id).one_or_none()
    is_retry = previous_task is not None and previous_task.status not in {"QUEUED", "RUNNING", "COMPLETED"}
    task = create_task(run_id, request.app.state.session_factory)
    if task.status != "QUEUED":
        raise HTTPException(409, "Run đã được xử lý hoặc đang chạy")
    if previous_task is None or is_retry:
        db.add(ResolutionAuditEvent(
            run_id=run.id, actor_user_id=auth.user.id,
            action="RUN_RETRIED" if is_retry else "RUN_STARTED",
            details={"task_id": task.id},
        ))
        db.commit()
    return {"task_id": task.id, "run_id": run_id, "status": task.status}
