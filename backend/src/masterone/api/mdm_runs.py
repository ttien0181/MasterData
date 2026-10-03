from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from sqlalchemy.orm import Session

from masterone.auth.security import CurrentSession, current_session, require_mutation
from masterone.config import get_settings
from masterone.db.session import get_session
from masterone.mdm.models import ResolutionFile, ResolutionRecord, ResolutionRun, ResolutionTask
from masterone.mdm.ownership import owned_run
from masterone.mdm.resolution_import import (
    R2_RUN_PREFIX,
    create_resolution_run,
    create_resolution_run_from_r2,
)
from masterone.mdm.resolution_service import create_task
from masterone.pipeline.r2 import R2SignedUrlService, create_r2_client
from masterone.pipeline.schemas import (
    CreateRunFromR2Request,
    RunUploadUrlRequest,
    RunUploadUrlResponse,
)

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


@router.post("/upload-url", response_model=RunUploadUrlResponse)
def create_run_upload_url(
    request: RunUploadUrlRequest,
    auth: CurrentSession = Depends(require_mutation),
):
    """Generate a presigned PUT URL so the client can upload a CSV directly to R2."""
    from pathlib import PurePosixPath
    from uuid import uuid4

    settings = get_settings()
    if not all((settings.r2_endpoint_url, settings.r2_access_key_id,
                settings.r2_secret_access_key, settings.r2_bucket)):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "R2 storage chưa được cấu hình")

    service = R2SignedUrlService(
        settings.r2_endpoint_url, settings.r2_access_key_id,
        settings.r2_secret_access_key, settings.r2_bucket,
        settings.large_file_url_ttl_seconds,
    )
    safe_name = PurePosixPath(request.filename).name
    object_name = f"{R2_RUN_PREFIX}{uuid4()}-{safe_name}"
    url = service._client.generate_presigned_url(
        "put_object",
        Params={"Bucket": settings.r2_bucket, "Key": object_name, "ContentType": request.content_type},
        ExpiresIn=settings.large_file_url_ttl_seconds,
        HttpMethod="PUT",
    )
    return RunUploadUrlResponse(
        object_name=object_name,
        upload_url=url,
        expires_in=settings.large_file_url_ttl_seconds,
    )


@router.post("/from-r2", status_code=status.HTTP_201_CREATED)
def create_run_from_r2(
    body: CreateRunFromR2Request,
    db: Session = Depends(get_session),
    auth: CurrentSession = Depends(require_mutation),
):
    """Create a resolution run from CSV files already uploaded to R2."""
    settings = get_settings()
    if not all((settings.r2_endpoint_url, settings.r2_access_key_id,
                settings.r2_secret_access_key, settings.r2_bucket)):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "R2 storage chưa được cấu hình")

    client = create_r2_client(
        settings.r2_endpoint_url, settings.r2_access_key_id, settings.r2_secret_access_key,
    )
    run, profile = create_resolution_run_from_r2(
        db, client, settings.r2_bucket,
        body.object_keys, auth.user.id, body.cleanup_sources,
    )
    return {"run_id": run.id, "status": run.status, "profile": profile}


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
