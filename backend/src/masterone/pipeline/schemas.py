from pydantic import BaseModel, Field


class UploadUrlRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(default="text/csv", min_length=1, max_length=100)


# ── Resolution Run upload schemas (R2 direct-upload path) ──────────────────

class RunUploadUrlRequest(BaseModel):
    """Request a presigned PUT URL for one resolution-run CSV file."""
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(default="text/csv", min_length=1, max_length=100)


class RunUploadUrlResponse(BaseModel):
    object_name: str   # R2 key under runs-incoming/
    upload_url: str    # presigned PUT URL (valid for large_file_url_ttl_seconds)
    expires_in: int    # seconds


class CreateRunFromR2Request(BaseModel):
    """Create a resolution run from files already uploaded to R2."""
    object_keys: list[str] = Field(min_length=1, max_length=10)
    cleanup_sources: bool = True


class UploadUrlResponse(BaseModel):
    object_name: str
    upload_url: str
    expires_in: int


class CompleteUploadRequest(BaseModel):
    object_name: str = Field(min_length=1, max_length=500)


class UploadCompleteResponse(BaseModel):
    object_name: str
    status: str = "stored"


class MergeCsvRequest(BaseModel):
    object_keys: list[str] = Field(min_length=1, max_length=10)
    cleanup_sources: bool = True
    job_id: str | None = Field(default=None, min_length=1, max_length=36)


class MergeCsvResponse(BaseModel):
    job_id: str | None = None
    output_object_key: str
    rows_written: int
    columns: list[str]
    sources: list[dict[str, object]]
