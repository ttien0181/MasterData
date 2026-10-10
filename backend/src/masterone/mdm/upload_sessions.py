"""Persistent, resumable CSV draft uploads with bounded-memory validation."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sqlite3
from collections.abc import Sequence
from contextlib import closing
from pathlib import Path
from typing import BinaryIO

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from masterone.config import get_settings
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


def staging_root() -> Path:
    return Path(get_settings().upload_staging_dir).resolve()


def local_part_path(session_id: str, file_id: str, part_number: int) -> Path:
    return staging_root() / session_id / file_id / f"{part_number}.part"


def assembled_file_path(session_id: str, file_id: str) -> Path:
    return staging_root() / session_id / file_id / "assembled.csv"


def remote_part_key(session_id: str, file_id: str, part_number: int) -> str:
    return f"upload-staging/{session_id}/{file_id}/{part_number}.part"


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
    if session.status == "COMMITTED":
        committed_run = db.query(ResolutionRun.id).filter_by(upload_session_id=session.id).scalar()
    return {
        "session_id": session.id, "name": session.name, "status": session.status,
        "total_bytes": session.total_bytes, "committed_run_id": committed_run,
        "error_message": session.error_message,
        "created_at": session.created_at, "updated_at": session.updated_at,
        "files": serialized_files,
    }


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


def save_upload_part(db: Session, session: ResolutionUploadSession, file: ResolutionUploadFile,
                     part_number: int, stream: BinaryIO, byte_size: int, digest: str) -> dict:
    if session.status in {"COMMITTED", "CANCELLED", "VALIDATING"}:
        raise HTTPException(status.HTTP_409_CONFLICT, "Upload draft không nhận thêm dữ liệu ở trạng thái hiện tại")
    if part_number < 0 or byte_size <= 0 or byte_size > MAX_UPLOAD_CHUNK_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Kích thước khối upload không hợp lệ")
    offset = part_number * UPLOAD_CHUNK_BYTES
    if offset >= file.expected_size:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Số thứ tự khối vượt quá dung lượng file")
    expected_bytes = min(UPLOAD_CHUNK_BYTES, file.expected_size - offset)
    if byte_size != expected_bytes:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            f"Khối {part_number} cần đúng {expected_bytes} byte")

    existing = db.query(ResolutionUploadPart).filter_by(upload_file_id=file.id,
                                                         part_number=part_number).one_or_none()
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

    previous_size = existing.byte_size if existing else 0
    if existing:
        existing.byte_size = byte_size
        existing.sha256 = digest
    else:
        db.add(ResolutionUploadPart(upload_file_id=file.id, part_number=part_number,
                                    byte_size=byte_size, sha256=digest))
    file.received_size += byte_size - previous_size
    file.status = "UPLOADING"
    file.sha256 = None
    file.row_count = None
    file.validation_error = None
    session.status = "UPLOADING"
    session.error_message = None
    db.commit()
    return {"part_number": part_number, "byte_size": byte_size, "sha256": digest,
            "received_size": file.received_size}


def _read_part(session: ResolutionUploadSession, file: ResolutionUploadFile,
               part: ResolutionUploadPart) -> bytes:
    if session.storage_backend == "r2":
        store = get_store(get_settings())
        if store is None:
            raise RuntimeError("Object storage is not configured")
        return store.download_bytes(remote_part_key(session.id, file.id, part.part_number))
    return local_part_path(session.id, file.id, part.part_number).read_bytes()


def materialize_upload_file(db: Session, session: ResolutionUploadSession,
                            file: ResolutionUploadFile) -> Path:
    parts = db.query(ResolutionUploadPart).filter_by(upload_file_id=file.id).order_by(
        ResolutionUploadPart.part_number).all()
    expected_count = (file.expected_size + UPLOAD_CHUNK_BYTES - 1) // UPLOAD_CHUNK_BYTES
    if len(parts) != expected_count or any(part.part_number != index for index, part in enumerate(parts)):
        raise HTTPException(status.HTTP_409_CONFLICT, f"{file.filename}: thiếu khối upload; có thể tiếp tục gửi file")
    destination = assembled_file_path(session.id, file.id)
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    total = 0
    with destination.open("wb") as output:
        for part in parts:
            data = _read_part(session, file, part)
            part_digest = hashlib.sha256(data).hexdigest()
            if len(data) != part.byte_size or part_digest != part.sha256:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                                    f"{file.filename}: khối {part.part_number} bị hỏng; hãy tải lại file")
            output.write(data)
            digest.update(data)
            total += len(data)
    if total != file.expected_size:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{file.filename}: dung lượng file không khớp")
    file.sha256 = digest.hexdigest()
    return destination


def validate_upload_session(db: Session, session: ResolutionUploadSession) -> ResolutionUploadSession:
    if session.status in {"COMMITTED", "CANCELLED", "VALIDATING"}:
        raise HTTPException(status.HTTP_409_CONFLICT, "Không thể validate draft ở trạng thái hiện tại")
    session.status = "VALIDATING"
    session.error_message = None
    for file in db.query(ResolutionUploadFile).filter_by(session_id=session.id).all():
        file.validation_error = None
    db.commit()
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


def commit_upload_session(db: Session, session: ResolutionUploadSession,
                          owner_user_id: str) -> tuple[ResolutionRun, dict]:
    run = db.query(ResolutionRun).filter_by(upload_session_id=session.id).one_or_none()
    if run:
        files = db.query(ResolutionFile).filter_by(run_id=run.id).order_by(ResolutionFile.filename).all()
        source_counts = dict(db.query(ResolutionRecord.source_system, func.count(ResolutionRecord.id))
                             .filter_by(run_id=run.id).group_by(ResolutionRecord.source_system).all())
        session.status = "COMMITTED"
        db.commit()
        return run, {"row_count": run.record_count, "file_count": run.file_count,
                     "source_counts": source_counts,
                     "files": [{"filename": row.filename, "row_count": row.row_count,
                                "sha256": row.sha256} for row in files]}
    if session.status != "READY":
        raise HTTPException(status.HTTP_409_CONFLICT, "Kiểm tra và hoàn tất tải file trước khi tạo phiên")
    if session.owner_user_id != owner_user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Upload draft không tồn tại")
    upload_files = db.query(ResolutionUploadFile).filter_by(session_id=session.id).order_by(
        ResolutionUploadFile.position).all()
    paths = [(file.filename, materialize_upload_file(db, session, file)) for file in upload_files]
    streams = []
    try:
        streams = [(filename, path.open("rb")) for filename, path in paths]
        run, profile = create_resolution_run_from_streams(
            db, streams, owner_user_id, session.name, upload_session_id=session.id)
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
    files = db.query(ResolutionUploadFile).filter_by(session_id=session.id).all()
    parts_by_file = {file.id: db.query(ResolutionUploadPart).filter_by(upload_file_id=file.id).all()
                     for file in files}
    db.delete(session)
    db.commit()
    for file in files:
        _delete_part_objects(session, file, parts_by_file[file.id])
    if session.storage_backend == "local":
        shutil.rmtree(staging_root() / session.id, ignore_errors=True)
