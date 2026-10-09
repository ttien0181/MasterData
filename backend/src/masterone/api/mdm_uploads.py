"""Draft-first, chunked upload endpoints for resolution runs."""

import hashlib
import tempfile

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status, BackgroundTasks
from fastapi import Path as PathParam
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from masterone.auth.security import CurrentSession, require_capability, require_mutation_capability
from masterone.db.session import get_session
from masterone.mdm.models import ResolutionUploadSession
from masterone.mdm.upload_sessions import (
    MAX_UPLOAD_CHUNK_BYTES,

    create_upload_session,
    delete_upload_session,
    get_upload_file,
    owned_upload_session,
    replace_upload_file,
    save_upload_part,
    serialize_upload_session,
    update_upload_session_name,
    validate_upload_session,
)

router = APIRouter(prefix="/mdm/upload-sessions", tags=["MDM upload sessions"])


class UploadFileSpec(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    size: int = Field(gt=0)


class CreateUploadSessionRequest(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    files: list[UploadFileSpec] = Field(min_length=1, max_length=10)


class RenameUploadSessionRequest(BaseModel):
    name: str | None = Field(default=None, max_length=255)


class ReplaceUploadFileRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    size: int = Field(gt=0)


class RunCommitResponse(BaseModel):
    run_id: str
    status: str
    profile: dict


@router.get("")
def list_upload_sessions(
    limit: int = 20,
    include_closed: bool = False,
    db: Session = Depends(get_session),
    auth: CurrentSession = Depends(require_capability("read")),
):
    limit = min(max(limit, 1), 50)
    query = db.query(ResolutionUploadSession).filter_by(owner_user_id=auth.user.id)
    if not include_closed:
        query = query.filter(ResolutionUploadSession.status.notin_(("COMMITTED", "CANCELLED")))
    sessions = query.order_by(ResolutionUploadSession.updated_at.desc(),
                              ResolutionUploadSession.id.desc()).limit(limit).all()
    return {"items": [serialize_upload_session(db, session, include_parts=False) for session in sessions]}


@router.post("", status_code=status.HTTP_201_CREATED)
def create_upload_draft(
    body: CreateUploadSessionRequest,
    db: Session = Depends(get_session),
    auth: CurrentSession = Depends(require_mutation_capability("create_run")),
):
    session = create_upload_session(db, auth.user.id, body.name,
                                    [item.model_dump() for item in body.files])
    return serialize_upload_session(db, session)


@router.get("/{session_id}")
def get_upload_draft(
    session_id: str,
    db: Session = Depends(get_session),
    auth: CurrentSession = Depends(require_capability("read")),
):
    session = owned_upload_session(db, session_id, auth.user.id)
    return serialize_upload_session(db, session)


@router.patch("/{session_id}")
def rename_upload_draft(
    session_id: str,
    body: RenameUploadSessionRequest,
    db: Session = Depends(get_session),
    auth: CurrentSession = Depends(require_mutation_capability("create_run")),
):
    session = owned_upload_session(db, session_id, auth.user.id)
    session = update_upload_session_name(db, session, body.name)
    return serialize_upload_session(db, session)


@router.put("/{session_id}/files/{file_id}/replace")
def replace_draft_file(
    session_id: str,
    file_id: str,
    body: ReplaceUploadFileRequest,
    db: Session = Depends(get_session),
    auth: CurrentSession = Depends(require_mutation_capability("create_run")),
):
    session = owned_upload_session(db, session_id, auth.user.id)
    file = get_upload_file(db, session, file_id)
    replace_upload_file(db, session, file, body.filename, body.size)
    return serialize_upload_session(db, owned_upload_session(db, session_id, auth.user.id))


@router.put("/{session_id}/files/{file_id}/parts/{part_number}")
async def upload_draft_part(
    session_id: str,
    file_id: str,
    request: Request,
    part_number: int = PathParam(ge=0),
    db: Session = Depends(get_session),
    auth: CurrentSession = Depends(require_mutation_capability("create_run")),
):
    session = owned_upload_session(db, session_id, auth.user.id)
    file = get_upload_file(db, session, file_id)
    digest = hashlib.sha256()
    client_digest = request.headers.get("x-chunk-sha256")
    size = 0
    with tempfile.SpooledTemporaryFile(max_size=MAX_UPLOAD_CHUNK_BYTES) as chunk:
        async for block in request.stream():
            size += len(block)
            if size > MAX_UPLOAD_CHUNK_BYTES:
                raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE,
                                    f"Một khối upload không vượt quá {MAX_UPLOAD_CHUNK_BYTES // (1024 * 1024)} MiB")
            digest.update(block)
            chunk.write(block)
        if not size:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Khối upload trống")
        if client_digest and client_digest.lower() != digest.hexdigest():
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Checksum khối upload không khớp")
        chunk.seek(0)
        saved = save_upload_part(db, session, file, part_number, chunk, size, digest.hexdigest())
    return saved


@router.post("/{session_id}/validate")
def validate_draft(
    session_id: str,
    db: Session = Depends(get_session),
    auth: CurrentSession = Depends(require_mutation_capability("create_run")),
):
    session = owned_upload_session(db, session_id, auth.user.id)
    session = validate_upload_session(db, session)
    return serialize_upload_session(db, session)


@router.post("/{session_id}/commit", response_model=RunCommitResponse, status_code=status.HTTP_201_CREATED)
def commit_draft(
    session_id: str,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Session = Depends(get_session),
    auth: CurrentSession = Depends(require_mutation_capability("create_run")),
):
    from masterone.mdm.upload_sessions import prepare_commit_upload_session, execute_commit_upload_session
    session = owned_upload_session(db, session_id, auth.user.id)
    run, profile = prepare_commit_upload_session(db, session, auth.user.id)
    
    background_tasks.add_task(
        execute_commit_upload_session,
        run.id, session.id, auth.user.id, request.app.state.session_factory
    )
    
    return {"run_id": run.id, "status": run.status, "profile": profile}


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_draft(
    session_id: str,
    db: Session = Depends(get_session),
    auth: CurrentSession = Depends(require_mutation_capability("create_run")),
):
    session = owned_upload_session(db, session_id, auth.user.id)
    delete_upload_session(db, session)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
