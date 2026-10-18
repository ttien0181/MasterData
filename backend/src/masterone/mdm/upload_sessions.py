"""Persistent, resumable CSV draft uploads with bounded-memory validation."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import shutil
import sqlite3
from collections import deque
from collections.abc import Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, timedelta
from pathlib import Path
from typing import BinaryIO
from uuid import UUID, uuid5

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from masterone.config import get_settings
from masterone.db.base import now_utc
from masterone.mdm.input_schema import InputMappingError
from masterone.mdm.models import (
    ResolutionFile,
    ResolutionRecord,
    ResolutionRun,
    ResolutionUploadFile,
    ResolutionUploadPart,
    ResolutionUploadSession,
)
from masterone.mdm.resolution_import import MAX_FILES_PER_RUN, _iter_normalized, create_resolution_run_from_streams
from masterone.storage.r2 import get_store

UPLOAD_CHUNK_BYTES = 8 * 1024 * 1024
MAX_UPLOAD_CHUNK_BYTES = 16 * 1024 * 1024
DIRECT_CONTENT_TYPE = "application/octet-stream"
DIRECT_URL_TTL_SECONDS = 900
# Staged parts that no draft references are swept after this long. It must exceed the
# longest time a browser can sit between its PUT and its commit call.
ORPHAN_PART_RETENTION = timedelta(hours=24)
STAGING_PREFIX = "upload-staging/"


def staging_root() -> Path:
    return Path(get_settings().upload_staging_dir).resolve()


def local_part_path(session_id: str, file_id: str, part_number: int) -> Path:
    return staging_root() / session_id / file_id / f"{part_number}.part"


def assembled_file_path(session_id: str, file_id: str) -> Path:
    return staging_root() / session_id / file_id / "assembled.csv"


def remote_part_key(session_id: str, file_id: str, part_number: int) -> str:
    return f"{STAGING_PREFIX}{session_id}/{file_id}/{part_number}.part"


def owned_upload_session(db: Session, session_id: str, owner_user_id: str) -> ResolutionUploadSession:
    session = db.get(ResolutionUploadSession, session_id)
    if session is None or session.owner_user_id != owner_user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Upload draft không tồn tại")
    return session


def get_upload_file(db: Session, session: ResolutionUploadSession, file_id: str) -> ResolutionUploadFile:
    file = db.get(ResolutionUploadFile, file_id)
    if file is None or file.session_id != session.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File trong upload draft không tồn tại")
    return file


def create_upload_session(db: Session, owner_user_id: str, name: str | None,
                          files: Sequence[dict]) -> ResolutionUploadSession:
    if not files:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Chọn ít nhất một file CSV")
    if len(files) > MAX_FILES_PER_RUN:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE,
                            f"Mỗi phiên tối đa {MAX_FILES_PER_RUN} file; dung lượng từng file không bị giới hạn bởi ứng dụng")
    normalized_name = (name or "").strip() or None
    if normalized_name and len(normalized_name) > 255:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Tên phiên tối đa 255 ký tự")

    total_bytes = 0
    prepared = []
    for position, item in enumerate(files):
        filename = str(item.get("filename") or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
        size = item.get("size")
        if not filename or len(filename) > 255 or not filename.lower().endswith(".csv"):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Chỉ hỗ trợ file CSV có tên tối đa 255 ký tự")
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{filename} đang trống hoặc có dung lượng không hợp lệ")
        prepared.append((position, filename, size))
        total_bytes += size

    use_r2 = get_store(get_settings()) is not None
    session = ResolutionUploadSession(owner_user_id=owner_user_id, name=normalized_name,
                                      status="UPLOADING", storage_backend="r2" if use_r2 else "local",
                                      total_bytes=total_bytes)
    db.add(session)
    db.flush()
    for position, filename, size in prepared:
        db.add(ResolutionUploadFile(session_id=session.id, position=position, filename=filename,
                                    expected_size=size, received_size=0, status="UPLOADING",
                                    source_counts={}))
    db.commit()
    db.refresh(session)
    return session


def serialize_upload_session(db: Session, session: ResolutionUploadSession,
                             include_parts: bool = True) -> dict:
    files = db.query(ResolutionUploadFile).filter_by(session_id=session.id).order_by(
        ResolutionUploadFile.position).all()
    replacement_by_source = {file.replaces_file_id: file for file in files if file.replaces_file_id}

    def serialize_file(file: ResolutionUploadFile) -> dict:
        parts = db.query(ResolutionUploadPart).filter_by(upload_file_id=file.id).order_by(
            ResolutionUploadPart.part_number).all() if include_parts else []
        error_message = file.validation_error
        error_report = None
        if error_message and error_message.startswith('{"code":'):
            # Legacy validation_error strings remain readable without migration.
            try:
                error_report = json.loads(error_message)
            except json.JSONDecodeError:
                error_report = None
            if isinstance(error_report, dict) and isinstance(error_report.get("message"), str):
                error_message = error_report["message"]
        return {
            "file_id": file.id, "position": file.position, "filename": file.filename,
            "expected_size": file.expected_size, "received_size": file.received_size,
            "status": file.status, "sha256": file.sha256, "row_count": file.row_count,
            "source_counts": file.source_counts or {}, "validation_error": error_message,
            "validation_report": error_report,
            "parts": [{"part_number": part.part_number, "byte_size": part.byte_size,
                       "sha256": part.sha256} for part in parts],
        }

    current_files = [file for file in files if file.replaces_file_id is None]
    serialized_files = []
    for file in current_files:
        serialized = serialize_file(file)
        replacement = replacement_by_source.get(file.id)
        serialized["replacement"] = serialize_file(replacement) if replacement else None
        serialized_files.append(serialized)
    committed_run = None
    committed_profile = None
    if session.status == "COMMITTED":
        run = db.query(ResolutionRun).filter_by(upload_session_id=session.id).one_or_none()
        committed_run = run.id if run else None
        # Profile is aggregated over every record, so only the single-draft view pays for it.
        if run is not None and include_parts:
            committed_profile = run_profile(db, run)
    return {
        "session_id": session.id, "name": session.name, "status": session.status, "use_embedding": session.use_embedding,
        "total_bytes": session.total_bytes, "upload_mode": "direct" if session.storage_backend == "r2" else "proxy",
        "committed_run_id": committed_run,
        "committed_profile": committed_profile,
        "error_message": session.error_message,
        "created_at": session.created_at, "updated_at": session.updated_at,
        "files": serialized_files,
    }


def lock_upload_session(db: Session, session_id: str):
    # Reserve SQLite's writer before reading positions/status; FOR UPDATE is ignored there.
    if db.get_bind().dialect.name == 'sqlite':
        connection = db.connection()
        if not connection.connection.driver_connection.in_transaction:
            connection.exec_driver_sql('BEGIN IMMEDIATE')
    return db.query(ResolutionUploadSession).filter_by(id=session_id).with_for_update().populate_existing().one_or_none()


def add_upload_files(db: Session, session_id: str, owner_user_id: str, files: Sequence[dict], request_id: str):
    session = lock_upload_session(db, session_id)
    if session is None or session.owner_user_id != owner_user_id:
        raise HTTPException(404, 'Upload draft not found')
    prepared = []
    for index, item in enumerate(files):
        filename = str(item['filename']).replace('\\', '/').rsplit('/', 1)[-1].strip()
        size = item['size']
        if not filename or len(filename) > 255 or not filename.lower().endswith('.csv') or type(size) is not int or size <= 0:
            raise HTTPException(422, 'Invalid CSV specification')
        prepared.append((str(uuid5(UUID(session.id), request_id + ':' + str(index))), filename, size))
    if not prepared:
        raise HTTPException(422, 'Choose at least one file')
    existing = [db.get(ResolutionUploadFile, fid) for fid, _, _ in prepared]
    if any(existing):
        if not all(row and row.filename == filename and row.expected_size == size for row, (_, filename, size) in zip(existing, prepared)):
            raise HTTPException(409, 'Request ID payload mismatch')
        # A smaller replay is also a different payload.
        extra_id = str(uuid5(UUID(session.id), request_id + ':' + str(len(prepared))))
        if db.get(ResolutionUploadFile, extra_id):
            raise HTTPException(409, 'Request ID payload mismatch')
        return session
    if session.status in ('VALIDATING', 'COMMITTING', 'COMMITTED', 'CANCELLED'):
        raise HTTPException(409, 'Upload draft is locked')
    query = db.query(ResolutionUploadFile).filter_by(session_id=session.id)
    if query.filter(ResolutionUploadFile.replaces_file_id.is_(None)).count() + len(prepared) > MAX_FILES_PER_RUN:
        raise HTTPException(413, 'Maximum 10 CSV files per draft')
    last = query.with_entities(func.max(ResolutionUploadFile.position)).scalar()
    for index, (fid, filename, size) in enumerate(prepared):
        db.add(ResolutionUploadFile(id=fid, session_id=session.id, position=(last if last is not None else -1) + index + 1,
                                   filename=filename, expected_size=size, received_size=0, status='UPLOADING', source_counts={}))
    session.total_bytes += sum(size for _, _, size in prepared)
    session.status, session.error_message = 'UPLOADING', None
    db.commit()
    return session


def update_upload_session_name(db: Session, session: ResolutionUploadSession,
                               name: str | None) -> ResolutionUploadSession:
    if session.status in {"COMMITTED", "CANCELLED"}:
        raise HTTPException(status.HTTP_409_CONFLICT, "Không thể đổi tên draft đã đóng")
    normalized = (name or "").strip() or None
    if normalized and len(normalized) > 255:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Tên phiên tối đa 255 ký tự")
    session.name = normalized
    db.commit()
    db.refresh(session)
    return session


def _delete_part_objects(session: ResolutionUploadSession, file: ResolutionUploadFile,
                         parts: Sequence[ResolutionUploadPart]) -> None:
    if session.storage_backend == "r2":
        store = get_store(get_settings())
        if store:
            for part in parts:
                try:
                    store.delete(remote_part_key(session.id, file.id, part.part_number))
                except Exception:
                    logging.exception("Could not delete staged R2 part %s/%s", file.id, part.part_number)
    else:
        shutil.rmtree(local_part_path(session.id, file.id, 0).parent, ignore_errors=True)


def replace_upload_file(db: Session, session: ResolutionUploadSession,
                        file: ResolutionUploadFile, filename: str, size: int) -> ResolutionUploadFile:
    if session.status in {"COMMITTED", "CANCELLED", "VALIDATING"}:
        raise HTTPException(status.HTTP_409_CONFLICT, "Không thể đổi file ở trạng thái hiện tại")
    safe_name = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not safe_name.lower().endswith(".csv") or len(safe_name) > 255:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Chỉ hỗ trợ file CSV có tên tối đa 255 ký tự")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "File đang trống hoặc có dung lượng không hợp lệ")
    if file.replaces_file_id is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Chọn file gốc để thay thế")

    pending = db.query(ResolutionUploadFile).filter_by(replaces_file_id=file.id).one_or_none()
    if pending:
        parts = db.query(ResolutionUploadPart).filter_by(upload_file_id=pending.id).all()
        old_size = pending.expected_size
        db.query(ResolutionUploadPart).filter_by(upload_file_id=pending.id).delete(synchronize_session=False)
        pending.filename = safe_name
        pending.expected_size = size
        pending.received_size = 0
        pending.status = "UPLOADING"
        pending.sha256 = None
        pending.row_count = None
        pending.source_counts = {}
        pending.validation_error = None
        pending_target = pending
    else:
        parts = []
        old_size = file.expected_size
        max_position = (db.query(func.max(ResolutionUploadFile.position))
                        .filter(ResolutionUploadFile.session_id == session.id).scalar())
        position = (max_position if max_position is not None else -1) + 1
        pending_target = ResolutionUploadFile(
            session_id=session.id, replaces_file_id=file.id, position=position,
            filename=safe_name, expected_size=size, received_size=0, status="UPLOADING",
            source_counts={},
        )
        db.add(pending_target)

    session.total_bytes = session.total_bytes - old_size + size
    session.status = "UPLOADING"
    session.error_message = None
    db.commit()
    if parts:
        _delete_part_objects(session, pending_target, parts)
    db.refresh(pending_target)
    return pending_target


def _ensure_accepting_parts(session: ResolutionUploadSession) -> None:
    if session.status in {"COMMITTED", "CANCELLED", "VALIDATING"}:
        raise HTTPException(status.HTTP_409_CONFLICT, "Upload draft không nhận thêm dữ liệu ở trạng thái hiện tại")


def _check_part_shape(file: ResolutionUploadFile, part_number: int, byte_size: int) -> None:
    if part_number < 0 or byte_size <= 0 or byte_size > MAX_UPLOAD_CHUNK_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Kích thước khối upload không hợp lệ")
    offset = part_number * UPLOAD_CHUNK_BYTES
    if offset >= file.expected_size:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Số thứ tự khối vượt quá dung lượng file")
    expected_bytes = min(UPLOAD_CHUNK_BYTES, file.expected_size - offset)
    if byte_size != expected_bytes:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            f"Khối {part_number} cần đúng {expected_bytes} byte")


def save_upload_part(db: Session, session: ResolutionUploadSession, file: ResolutionUploadFile,
                     part_number: int, stream: BinaryIO, byte_size: int, digest: str) -> dict:
    """Proxy path: the bytes pass through the API. Used for local staging and as a fallback."""
    _ensure_accepting_parts(session)
    _check_part_shape(file, part_number, byte_size)

    stream.seek(0)
    if session.storage_backend == "r2":
        store = get_store(get_settings())
        if store is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Object storage hiện không khả dụng")
        store.upload_stream(stream, remote_part_key(session.id, file.id, part_number), "application/octet-stream")
    else:
        destination = local_part_path(session.id, file.id, part_number)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(".part.tmp")
        with temporary.open("wb") as output:
            shutil.copyfileobj(stream, output, length=1024 * 1024)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)

    return _record_part(db, session, file, part_number, byte_size, digest)


def _record_part(db: Session, session: ResolutionUploadSession, file: ResolutionUploadFile,
                 part_number: int, byte_size: int, digest: str) -> dict:
    # Parts of one file can commit concurrently. Lock the file row, then total the stored parts,
    # so no commit overwrites another's count with a stale value.
    locked = (db.query(ResolutionUploadFile).filter_by(id=file.id)
              .with_for_update().populate_existing().one())
    existing = db.query(ResolutionUploadPart).filter_by(upload_file_id=locked.id,
                                                         part_number=part_number).one_or_none()
    if existing:
        existing.byte_size = byte_size
        existing.sha256 = digest
    else:
        db.add(ResolutionUploadPart(upload_file_id=locked.id, part_number=part_number,
                                    byte_size=byte_size, sha256=digest))
    db.flush()
    locked.received_size = db.query(func.coalesce(func.sum(ResolutionUploadPart.byte_size), 0)).filter_by(
        upload_file_id=locked.id).scalar()
    locked.status = "UPLOADING"
    locked.sha256 = None
    locked.row_count = None
    locked.validation_error = None
    session.status = "UPLOADING"
    session.error_message = None
    db.commit()
    return {"part_number": part_number, "byte_size": byte_size, "sha256": digest,
            "received_size": locked.received_size}


def _base64_digest(hex_digest: str) -> str:
    """S3 checksum headers carry the raw digest in base64, not hex."""
    return base64.b64encode(bytes.fromhex(hex_digest)).decode("ascii")


def _direct_store(session: ResolutionUploadSession):
    if session.storage_backend != "r2":
        raise HTTPException(status.HTTP_409_CONFLICT, "Phiên này lưu tạm trên máy chủ; hãy tải khối qua API")
    store = get_store(get_settings())
    if store is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Object storage hiện không khả dụng")
    return store


def presign_upload_parts(session: ResolutionUploadSession, file: ResolutionUploadFile,
                         parts: Sequence[dict]) -> list[dict]:
    """Direct path: issue one PUT URL per part. The browser then sends bytes straight to R2.

    The SHA-256 is part of the signed URL, so R2 itself rejects any body that does not match it.
    """
    _ensure_accepting_parts(session)
    store = _direct_store(session)
    targets = []
    for item in parts:
        _check_part_shape(file, item["part_number"], item["byte_size"])
        checksum = _base64_digest(item["sha256"])
        targets.append({
            "part_number": item["part_number"],
            "upload_url": store.presigned_put_url(
                remote_part_key(session.id, file.id, item["part_number"]),
                DIRECT_CONTENT_TYPE, checksum, DIRECT_URL_TTL_SECONDS),
            "headers": {"Content-Type": DIRECT_CONTENT_TYPE, "x-amz-checksum-sha256": checksum},
            "expires_in": DIRECT_URL_TTL_SECONDS,
        })
    return targets


def commit_uploaded_part(db: Session, session: ResolutionUploadSession, file: ResolutionUploadFile,
                         part_number: int, byte_size: int, digest: str) -> dict:
    """Record a part only after the object is confirmed in R2 with the expected size and checksum."""
    _ensure_accepting_parts(session)
    _check_part_shape(file, part_number, byte_size)
    store = _direct_store(session)
    head = store.head_part(remote_part_key(session.id, file.id, part_number))
    if head is None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Khối {part_number} chưa có trên kho lưu trữ; hãy tải lại khối")
    if head["byte_size"] != byte_size or head["sha256_base64"] != _base64_digest(digest):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            f"Khối {part_number} trên kho lưu trữ không khớp kích thước hoặc checksum; hãy tải lại khối")
    return _record_part(db, session, file, part_number, byte_size, digest)


def sweep_orphan_parts(db: Session, store, *, now=None, dry_run: bool = False) -> int:
    """Delete staged part objects that no draft references and that are older than the retention window.

    With dry_run=True nothing is deleted; the return value is how many objects would be.
    """
    cutoff = (now or now_utc()) - ORPHAN_PART_RETENTION
    referenced = {
        remote_part_key(session_id, file_id, part_number)
        for part_number, file_id, session_id in db.query(
            ResolutionUploadPart.part_number, ResolutionUploadPart.upload_file_id, ResolutionUploadFile.session_id)
        .join(ResolutionUploadFile, ResolutionUploadFile.id == ResolutionUploadPart.upload_file_id)
    }
    count = 0
    for key, last_modified in store.list_objects(STAGING_PREFIX):
        if key in referenced or last_modified >= cutoff:
            continue
        if not dry_run:
            store.delete(key)
        count += 1
    return count


def _part_locator(session: ResolutionUploadSession, file: ResolutionUploadFile, part_number: int) -> str:
    if session.storage_backend == "r2":
        return remote_part_key(session.id, file.id, part_number)
    return str(local_part_path(session.id, file.id, part_number))


def _download_part(storage_backend: str, locator: str) -> bytes:
    # Runs on worker threads: takes plain strings, never ORM objects.
    if storage_backend == "r2":
        store = get_store(get_settings())
        if store is None:
            raise RuntimeError("Object storage is not configured")
        return store.download_bytes(locator)
    return Path(locator).read_bytes()


def _iter_part_bytes(storage_backend: str, jobs: list[tuple[int, int, str, str]]
                     ) -> Iterator[tuple[int, int, str, bytes]]:
    """Yield (part_number, byte_size, sha256, data) in order.

    Remote parts download in a bounded sliding window, so memory stays at about
    2 * workers parts while the network stays busy.
    """
    if storage_backend != "r2" or len(jobs) == 1:
        for part_number, byte_size, digest, locator in jobs:
            yield part_number, byte_size, digest, _download_part(storage_backend, locator)
        return
    workers = get_settings().upload_download_workers
    window = 2 * workers
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending: deque = deque()
        try:
            for job in jobs:
                pending.append((job, pool.submit(_download_part, storage_backend, job[3])))
                if len(pending) >= window:
                    done_job, future = pending.popleft()
                    yield done_job[0], done_job[1], done_job[2], future.result()
            while pending:
                done_job, future = pending.popleft()
                yield done_job[0], done_job[1], done_job[2], future.result()
        finally:
            for _, future in pending:
                future.cancel()


def materialize_upload_file(db: Session, session: ResolutionUploadSession,
                            file: ResolutionUploadFile) -> Path:
    parts = db.query(ResolutionUploadPart).filter_by(upload_file_id=file.id).order_by(
        ResolutionUploadPart.part_number).all()
    expected_count = (file.expected_size + UPLOAD_CHUNK_BYTES - 1) // UPLOAD_CHUNK_BYTES
    if len(parts) != expected_count or any(part.part_number != index for index, part in enumerate(parts)):
        raise HTTPException(status.HTTP_409_CONFLICT, f"{file.filename}: thiếu khối upload; có thể tiếp tục gửi file")
    # Copy what the read loop needs, then end the DB transaction: downloading and
    # hashing a large file can take minutes and must not hold a transaction open.
    storage_backend = session.storage_backend
    jobs = [(part.part_number, part.byte_size, part.sha256, _part_locator(session, file, part.part_number))
            for part in parts]
    db.commit()
    destination = assembled_file_path(session.id, file.id)
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    total = 0
    with destination.open("wb") as output:
        for part_number, byte_size, expected_digest, data in _iter_part_bytes(storage_backend, jobs):
            if len(data) != byte_size or hashlib.sha256(data).hexdigest() != expected_digest:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                                    f"{file.filename}: khối {part_number} bị hỏng; hãy tải lại file")
            output.write(data)
            digest.update(data)
            total += len(data)
    if total != file.expected_size:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{file.filename}: dung lượng file không khớp")
    file.sha256 = digest.hexdigest()
    return destination


STALE_JOB_AFTER = timedelta(hours=2)


def _job_is_stale(session: ResolutionUploadSession) -> bool:
    """True for a draft left in VALIDATING/COMMITTING by a job that stopped without recording an outcome."""
    updated = session.updated_at
    if updated is None:
        return False
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=UTC)
    return now_utc() - updated > STALE_JOB_AFTER


def _failure_message(error: BaseException) -> str:
    if isinstance(error, HTTPException):
        return str(error.detail)
    return "Không thể xử lý phiên tải lên. Hãy thử lại."


def _fail_draft(db: Session, session_id: str, expected_status: str, status_after: str, error: BaseException) -> None:
    """Move a draft out of an in-progress state so the user can retry instead of waiting forever."""
    db.rollback()
    draft = db.get(ResolutionUploadSession, session_id)
    if draft is not None and draft.status == expected_status:
        draft.status = status_after
        draft.error_message = _failure_message(error)
        db.commit()
    logging.error("UPLOAD_DRAFT_FAILED session=%s from=%s error_type=%s", session_id, expected_status,
                  type(error).__name__)


def begin_validation(db: Session, session: ResolutionUploadSession) -> None:
    session = lock_upload_session(db, session.id)
    busy = session.status == "VALIDATING" and not _job_is_stale(session)
    if session.status in {"COMMITTED", "CANCELLED", "COMMITTING"} or busy:
        raise HTTPException(status.HTTP_409_CONFLICT, "Không thể validate draft ở trạng thái hiện tại")
    session.status = "VALIDATING"
    session.error_message = None
    for file in db.query(ResolutionUploadFile).filter_by(session_id=session.id).all():
        file.validation_error = None
    db.commit()


def validate_upload_session(db: Session, session: ResolutionUploadSession, *,
                            begun: bool = False) -> ResolutionUploadSession:
    """Validate every file of a draft. `begun=True` is the import-job path, where begin_validation already ran."""
    if not begun:
        begin_validation(db, session)
    try:
        return _validate_files(db, session)
    except BaseException as error:
        _fail_draft(db, session.id, "VALIDATING", "ERROR", error)
        raise


def _validate_files(db: Session, session: ResolutionUploadSession) -> ResolutionUploadSession:
    files = db.query(ResolutionUploadFile).filter_by(session_id=session.id).order_by(
        ResolutionUploadFile.position).all()
    replacements = {file.replaces_file_id: file for file in files if file.replaces_file_id}
    current_files = [file for file in files if file.replaces_file_id is None]
    ids_path = staging_root() / session.id / "validation.sqlite"
    ids_path.parent.mkdir(parents=True, exist_ok=True)
    checked_file: ResolutionUploadFile | None = None
    replaced_files: list[tuple[ResolutionUploadFile, list[ResolutionUploadPart]]] = []
    try:
        with closing(sqlite3.connect(ids_path)) as ids_db:
            with ids_db:
                ids_db.execute("CREATE TABLE IF NOT EXISTS ids (source_system TEXT, external_id TEXT, PRIMARY KEY (source_system, external_id))")
                ids_db.execute("DELETE FROM ids")
                for original in current_files:
                    file = replacements.get(original.id, original)
                    checked_file = file
                    path = materialize_upload_file(db, session, file)
                    row_count = 0
                    source_counts: dict[str, int] = {}
                    with path.open("rb") as stream:
                        for row in _iter_normalized(stream, file.filename):
                            try:
                                ids_db.execute("INSERT INTO ids (source_system, external_id) VALUES (?, ?)",
                                                (row["source_system"], row["external_id"]))
                            except sqlite3.IntegrityError as exc:
                                raise InputMappingError("DUPLICATE_SOURCE_ID", "ID bị lặp trong cùng nguồn",
                                    source_row_number=row["lineage"]["source_row_number"]) from exc
                            row_count += 1
                            source = row["source_system"]
                            source_counts[source] = source_counts.get(source, 0) + 1
                    if row_count == 0:
                        raise ValueError(f"{file.filename} không có bản ghi")
                    file.row_count = row_count
                    file.source_counts = source_counts
                    file.status = "READY"
                    file.validation_error = None
                for original in current_files:
                    replacement = replacements.get(original.id)
                    if replacement is None:
                        continue
                    old_parts = db.query(ResolutionUploadPart).filter_by(upload_file_id=original.id).all()
                    original_position = original.position
                    replaced_files.append((original, old_parts))
                    db.query(ResolutionUploadPart).filter_by(upload_file_id=original.id).delete(
                        synchronize_session=False)
                    db.delete(original)
                    db.flush()
                    replacement.replaces_file_id = None
                    replacement.position = original_position
                    db.flush()
        session.status = "READY"
        session.error_message = None
    except HTTPException as exc:
        session.status = "ERROR"
        session.error_message = str(exc.detail)
        if checked_file:
            checked_file.status = "ERROR"
            checked_file.validation_error = (json.dumps(exc.report, ensure_ascii=False)
                if isinstance(exc, InputMappingError) else str(exc.detail))
    except (ValueError, OSError, RuntimeError) as exc:
        session.status = "ERROR"
        session.error_message = str(exc)
        if checked_file:
            checked_file.status = "ERROR"
            checked_file.validation_error = str(exc)
    finally:
        try:
            ids_path.unlink(missing_ok=True)
        except OSError:
            logging.exception("Could not remove upload validation index %s", ids_path)
    db.commit()
    for old_file, old_parts in replaced_files:
        _delete_part_objects(session, old_file, old_parts)
    db.refresh(session)
    return session


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _assembled_for_commit(db: Session, session: ResolutionUploadSession, file: ResolutionUploadFile) -> Path:
    """Reuse the file that validate assembled, if it still matches the stored digest.

    Validate already downloaded and verified every part; re-downloading them at
    commit doubled the object-storage traffic. Local disk is cheap to re-hash.
    """
    path = assembled_file_path(session.id, file.id)
    if (file.sha256 and path.is_file() and path.stat().st_size == file.expected_size
            and _sha256_file(path) == file.sha256):
        return path
    return materialize_upload_file(db, session, file)


def run_profile(db: Session, run: ResolutionRun) -> dict:
    files = db.query(ResolutionFile).filter_by(run_id=run.id).order_by(ResolutionFile.filename).all()
    source_counts = dict(db.query(ResolutionRecord.source_system, func.count(ResolutionRecord.id))
                         .filter_by(run_id=run.id).group_by(ResolutionRecord.source_system).all())
    return {"row_count": run.record_count, "file_count": run.file_count,
            "source_counts": source_counts,
            "files": [{"filename": row.filename, "row_count": row.row_count, "sha256": row.sha256}
                      for row in files]}


def begin_commit(db: Session, session: ResolutionUploadSession, owner_user_id: str) -> None:
    session = lock_upload_session(db, session.id)
    if session.owner_user_id != owner_user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Upload draft không tồn tại")
    if session.status == "COMMITTING" and not _job_is_stale(session):
        raise HTTPException(status.HTTP_409_CONFLICT, "Phiên đang được tạo; hãy đợi hoàn tất")
    if session.status != "READY":
        raise HTTPException(status.HTTP_409_CONFLICT, "Kiểm tra và hoàn tất tải file trước khi tạo phiên")
    session.status = "COMMITTING"
    session.error_message = None
    db.commit()


def commit_upload_session(db: Session, session: ResolutionUploadSession,
                          owner_user_id: str, *, begun: bool = False) -> tuple[ResolutionRun, dict]:
    """Create the run for a READY draft. `begun=True` is the import-job path, where begin_commit already ran."""
    run = db.query(ResolutionRun).filter_by(upload_session_id=session.id).one_or_none()
    if run:
        session.status = "COMMITTED"
        db.commit()
        return run, run_profile(db, run)
    if not begun:
        begin_commit(db, session, owner_user_id)
    try:
        return _commit_files(db, session, owner_user_id)
    except BaseException as error:
        _fail_draft(db, session.id, "COMMITTING", "READY", error)
        raise


def _commit_files(db: Session, session: ResolutionUploadSession,
                  owner_user_id: str) -> tuple[ResolutionRun, dict]:
    upload_files = db.query(ResolutionUploadFile).filter_by(session_id=session.id).order_by(
        ResolutionUploadFile.position).all()
    paths = [(file.filename, _assembled_for_commit(db, session, file)) for file in upload_files]
    known_hashes = [file.sha256 for file in upload_files]
    streams = []
    try:
        streams = [(filename, path.open("rb")) for filename, path in paths]
        run, profile = create_resolution_run_from_streams(
            db, streams, owner_user_id, session.name, upload_session_id=session.id, known_hashes=known_hashes,
            workflow_version=session.workflow_version, use_embedding=session.use_embedding)
        session.status = "COMMITTED"
        db.commit()
    finally:
        for _, stream in streams:
            stream.close()
    cleanup_upload_session_storage(db, session)
    return run, profile


def cleanup_upload_session_storage(db: Session, session: ResolutionUploadSession) -> None:
    files = db.query(ResolutionUploadFile).filter_by(session_id=session.id).all()
    for file in files:
        parts = db.query(ResolutionUploadPart).filter_by(upload_file_id=file.id).all()
        _delete_part_objects(session, file, parts)
    shutil.rmtree(staging_root() / session.id, ignore_errors=True)


def delete_upload_session(db: Session, session: ResolutionUploadSession) -> None:
    if session.status == "COMMITTED":
        raise HTTPException(status.HTTP_409_CONFLICT, "Phiên đã được tạo; không thể xóa draft này")
    # A running validate or commit reads these parts from storage; deleting them under it fails the job.
    if session.status in {"VALIDATING", "COMMITTING"} and not _job_is_stale(session):
        raise HTTPException(status.HTTP_409_CONFLICT, "Phiên đang được xử lý; hãy đợi hoàn tất rồi mới xóa")
    files = db.query(ResolutionUploadFile).filter_by(session_id=session.id).all()
    parts_by_file = {file.id: db.query(ResolutionUploadPart).filter_by(upload_file_id=file.id).all()
                     for file in files}
    db.delete(session)
    db.commit()
    for file in files:
        _delete_part_objects(session, file, parts_by_file[file.id])
    if session.storage_backend == "local":
        shutil.rmtree(staging_root() / session.id, ignore_errors=True)
