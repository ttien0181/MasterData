from pydantic import BaseModel, Field


class UploadUrlRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(default="text/csv", min_length=1, max_length=100)


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
    object_keys: list[str] = Field(min_length=1, max_length=3)
    cleanup_sources: bool = True


class MergeCsvResponse(BaseModel):
    output_object_key: str
    rows_written: int
    columns: list[str]
    sources: list[dict[str, object]]
