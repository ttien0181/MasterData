from fastapi import APIRouter, Depends, HTTPException

from masterone.auth.security import CurrentSession, current_session, require_mutation
from masterone.config import get_settings
from masterone.pipeline.csv_merge import CsvMergeError, merge_csv_objects
from masterone.pipeline.r2 import R2SignedUrlService
from masterone.pipeline.schemas import (
    CompleteUploadRequest, MergeCsvRequest, MergeCsvResponse, UploadCompleteResponse,
    UploadUrlRequest, UploadUrlResponse,
)

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
def merge_uploads(request: MergeCsvRequest, auth: CurrentSession = Depends(require_mutation)):
    del auth
    settings = get_settings()
    if not all((settings.r2_endpoint_url, settings.r2_access_key_id,
                settings.r2_secret_access_key, settings.r2_bucket)):
        raise HTTPException(503, "Cloudflare R2 is not configured")
    try:
        result = merge_csv_objects(
            endpoint_url=settings.r2_endpoint_url,
            access_key_id=settings.r2_access_key_id,
            secret_access_key=settings.r2_secret_access_key,
            bucket=settings.r2_bucket,
            object_keys=request.object_keys,
            cleanup_sources=request.cleanup_sources,
        )
    except CsvMergeError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, "Unable to merge CSV objects in R2") from exc
    return MergeCsvResponse(**result)
