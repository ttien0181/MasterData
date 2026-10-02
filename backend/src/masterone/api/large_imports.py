from datetime import UTC, datetime
from hashlib import sha256
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from masterone.auth.security import CurrentSession, current_session, require_mutation
from masterone.config import get_settings
from masterone.db.session import get_session
from masterone.pipeline.csv_merge import CsvMergeError, merge_csv_objects
from masterone.pipeline.r2 import R2SignedUrlService
from masterone.pipeline.schemas import (
    CompleteUploadRequest, MergeCsvRequest, MergeCsvResponse, UploadCompleteResponse,
    UploadUrlRequest, UploadUrlResponse,
)
from masterone.storage.manifest import StageManifest
from masterone.storage.object_store import R2ObjectStore
from masterone.storage.paths import JobPaths
from masterone.storage.repository import StorageJob, upsert_stage

router = APIRouter(prefix="/large-imports", tags=["Large file pipeline"])


@router.post("/upload-url", response_model=UploadUrlResponse)
def create_upload_url(request: UploadUrlRequest, auth: CurrentSession = Depends(require_mutation)):
    del auth
    settings = get_settings()
    if not settings.r2_endpoint_url or not settings.r2_access_key_id or not settings.r2_secret_access_key or not settings.r2_bucket:
        raise HTTPException(503, "Large-file storage is not configured")
    service = R2SignedUrlService(settings.r2_endpoint_url, settings.r2_access_key_id, settings.r2_secret_access_key, settings.r2_bucket, settings.large_file_url_ttl_seconds)
    object_name, url = service.create_upload_url(request.filename, request.content_type)
    return UploadUrlResponse(object_name=object_name, upload_url=url, expires_in=settings.large_file_url_ttl_seconds)


@router.post("/complete", response_model=UploadCompleteResponse)
def complete_upload(request: CompleteUploadRequest, auth: CurrentSession = Depends(current_session)):
    del auth
    settings = get_settings()
    if (not settings.r2_endpoint_url or not settings.r2_access_key_id
            or not settings.r2_secret_access_key or not settings.r2_bucket):
        raise HTTPException(503, "Large-file pipeline is not configured")
    return UploadCompleteResponse(object_name=request.object_name)


@router.post("/merge", response_model=MergeCsvResponse)
def merge_uploads(
    request: MergeCsvRequest,
    db: Session = Depends(get_session),
    auth: CurrentSession = Depends(require_mutation),
):
    del auth
    settings = get_settings()
    if not all((settings.r2_endpoint_url, settings.r2_access_key_id,
                settings.r2_secret_access_key, settings.r2_bucket)):
        raise HTTPException(503, "Cloudflare R2 is not configured")
    job_id = request.job_id or str(uuid4())
    paths = JobPaths(job_id)
    source_fingerprint = sha256("\n".join(sorted(request.object_keys)).encode()).hexdigest()
    job = db.get(StorageJob, job_id)
    if job is None:
        job = StorageJob(
            job_id=job_id,
            status="running",
            current_stage="merge",
            files=[{"object_key": key} for key in request.object_keys],
            config={"cleanup_sources": request.cleanup_sources},
        )
        db.add(job)
    else:
        job.status = "running"
        job.current_stage = "merge"
    upsert_stage(
        db, job_id, "merge", status="running", started_at=datetime.now(UTC),
        input_uri=f"r2://{settings.r2_bucket}/incoming/",
        output_uri=f"r2://{settings.r2_bucket}/{paths.merged()}",
        input_fingerprint=source_fingerprint,
        config_hash=sha256(str(request.cleanup_sources).encode()).hexdigest(),
        code_version=settings.storage_code_version,
    )
    db.commit()
    output_key = paths.merged()
    try:
        result = merge_csv_objects(
            endpoint_url=settings.r2_endpoint_url,
            access_key_id=settings.r2_access_key_id,
            secret_access_key=settings.r2_secret_access_key,
            bucket=settings.r2_bucket,
            object_keys=request.object_keys,
            cleanup_sources=request.cleanup_sources,
            output_key=output_key,
        )
    except CsvMergeError as exc:
        job.status = "failed"
        job.error = str(exc)
        upsert_stage(db, job_id, "merge", status="failed", finished_at=datetime.now(UTC), metrics={"error": str(exc)})
        db.commit()
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        job.status = "failed"
        job.error = str(exc)
        stage = upsert_stage(db, job_id, "merge", status="failed", finished_at=datetime.now(UTC), metrics={"error": str(exc)})
        del stage
        db.commit()
        raise HTTPException(502, "Unable to merge CSV objects in R2") from exc

    try:
        store = R2ObjectStore(settings.r2_endpoint_url, settings.r2_access_key_id, settings.r2_secret_access_key, settings.r2_bucket)
        head = store.client.head_object(Bucket=settings.r2_bucket, Key=output_key)
        manifest = StageManifest(
            job_id=job_id,
            stage="merge",
            input_uri=f"r2://{settings.r2_bucket}/incoming/",
            output_uri=f"r2://{settings.r2_bucket}/{output_key}",
            schema={column: "string" for column in result["columns"]},
            input_fingerprint=source_fingerprint,
            config_hash=sha256(str(request.cleanup_sources).encode()).hexdigest(),
            code_version=settings.storage_code_version,
            row_count=int(result["rows_written"]),
            bytes=int(head.get("ContentLength", 0)),
            files=(output_key,),
        )
        store.write_manifest(paths.success("merge"), manifest.to_dict())
        job.status = "done"
        job.current_stage = "merge"
        job.row_count = int(result["rows_written"])
        stage = upsert_stage(
            db, job_id, "merge", status="done", finished_at=datetime.now(UTC),
            output_uri=manifest.output_uri, row_count=manifest.row_count,
            bytes=manifest.bytes, schema=manifest.schema,
            metrics={"sources": result["sources"], "manifest_uri": f"r2://{settings.r2_bucket}/{paths.success('merge')}"},
            config_hash=manifest.config_hash, code_version=manifest.code_version,
            input_fingerprint=manifest.input_fingerprint,
        )
        del stage
        db.commit()
    except Exception as exc:
        job.status = "failed"
        job.error = f"manifest: {exc}"
        upsert_stage(db, job_id, "merge", status="failed", finished_at=datetime.now(UTC), metrics={"error": str(exc)})
        db.commit()
        raise HTTPException(502, "Merged object was created but its manifest could not be published") from exc

    return MergeCsvResponse(job_id=job_id, **result)
