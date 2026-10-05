from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from sqlalchemy.orm import Session

from masterone.auth.security import CurrentSession, current_session, require_mutation
from masterone.db.session import get_session
from masterone.mdm.models import ResolutionFile, ResolutionRecord, ResolutionRun, ResolutionTask
from masterone.mdm.ownership import owned_run
from masterone.mdm.resolution_import import create_resolution_run
from masterone.mdm.resolution_service import create_task

router = APIRouter(prefix="/mdm/runs", tags=["MDM resolution runs"])


@router.get("")
def list_runs(limit: int = 25, offset: int = 0, db: Session = Depends(get_session),
              auth: CurrentSession = Depends(current_session)):
    limit = min(max(limit, 1), 100)
    offset = max(offset, 0)
    query = db.query(ResolutionRun).filter_by(owner_user_id=auth.user.id)
    total = query.count()
    runs = query.order_by(ResolutionRun.created_at.desc(), ResolutionRun.id).offset(offset).limit(limit).all()
    return {"total": total, "items": [{"run_id": run.id, "status": run.status,
            "created_at": run.created_at, "record_count": run.record_count,
            "file_count": run.file_count, "files": [file.filename for file in
                db.query(ResolutionFile).filter_by(run_id=run.id).order_by(ResolutionFile.filename).all()]}
            for run in runs]}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_run(
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_session),
    auth: CurrentSession = Depends(require_mutation),
):
    run, profile = await create_resolution_run(db, files, auth.user.id)
    return {"run_id": run.id, "status": run.status, "profile": profile}


@router.get("/tasks/{task_id}")
def get_task(
    task_id: str,
    db: Session = Depends(get_session),
    auth: CurrentSession = Depends(current_session),
):
    task = db.get(ResolutionTask, task_id)
    if task is None:
        raise HTTPException(404, "Task not found")
    owned_run(db, task.run_id, auth.user.id)
    return {"task_id": task.id, "run_id": task.run_id, "status": task.status, "phase": task.phase,
            "processed_count": task.processed_count, "total_count": task.total_count,
            "error_code": task.error_code, "updated_at": task.updated_at}


@router.get("/{run_id}")
def get_run(
    run_id: str,
    db: Session = Depends(get_session),
    auth: CurrentSession = Depends(current_session),
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
    auth: CurrentSession = Depends(require_mutation),
):
    owned_run(db, run_id, auth.user.id)
    task = create_task(run_id, request.app.state.session_factory)
    if task.status != "QUEUED":
        raise HTTPException(409, "Run đã được xử lý hoặc đang chạy")
    return {"task_id": task.id, "run_id": run_id, "status": task.status}
