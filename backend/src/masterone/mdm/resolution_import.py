"""Stream and validate CSV files into an isolated resolution run."""

import csv
import hashlib
import io
import logging
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import BinaryIO
from uuid import uuid4

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import func, insert
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from masterone.config import get_settings
from masterone.mdm.ids import new_ordered_uuid
from masterone.mdm.input_schema import InputMappingError, input_error, resolve_source_schema
from masterone.mdm.models import ResolutionAuditEvent, ResolutionFile, ResolutionRecord, ResolutionRun
from masterone.mdm.pipeline.matching import POLICY_VERSION
from masterone.mdm.pipeline.standardization import standardize
from masterone.storage.r2 import get_store, source_file_key

MAX_FILES_PER_RUN = 10
INSERT_BATCH_SIZE = 1000
# Python's csv default field limit is 128 KiB. File and row counts are not capped;
# rows are streamed into bounded insert batches below.
csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

CORE_RAW_FIELDS = {"name", "phone", "email", "address", "dob", "government_id", "government_id_type"}


@dataclass(frozen=True)
class ParsedFile:
    """Compatibility representation for small callers and parser tests."""

    filename: str
    sha256: str
    rows: list[dict]
    source_counts: dict[str, int]
    raw_bytes: bytes


def _validate_headers(filename, fieldnames):
    if not filename.lower().endswith(".csv") or "pairs.csv" in filename.lower():
        input_error("INVALID_INPUT_SCHEMA", "Chọn file records.csv")
    mapping = resolve_source_schema(fieldnames)
    return set(fieldnames), mapping, mapping.unknown_fields


@contextmanager
def _csv_reader(stream: BinaryIO, filename: str):
    stream.seek(0)
    text = io.TextIOWrapper(stream, encoding="utf-8-sig", newline="")
    try:
        sample = text.read(4096)
        text.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel

        reader = csv.DictReader(text, dialect=dialect, strict=True)
        headers, schema_type, extra_columns = _validate_headers(filename, reader.fieldnames)
        yield reader, headers, schema_type, extra_columns
    except UnicodeDecodeError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{filename} phải dùng UTF-8") from exc
    except csv.Error as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            f"{filename} không phải CSV hợp lệ: {exc}") from exc
    finally:
        try:
            text.detach()
        except ValueError:
            pass
        stream.seek(0)


def _normalized_row(filename, line, source_row, schema_type, extra_columns):
    if None in source_row or any(value is None for value in source_row.values()):
        input_error("CSV_PARSE_ERROR", "Sai số cột CSV", source_row_number=line - 1)
    mapping = schema_type
    values = {field: source_row[column] for field, column in mapping.fields.items()}
    external_id = values["source_record_id"].strip()
    source = values["source_system"].strip().upper()
    missing = [field for field in mapping.required_fields if not values[field].strip()]
    if missing or source not in mapping.allowed_sources or len(external_id) > 200:
        input_error("INVALID_INPUT_RECORD", "Dữ liệu bắt buộc không hợp lệ",
                    source_row_number=line - 1, missing_fields=missing)
    return {
        "external_id": external_id,
        "source_system": source,
        "entity_type": (values.get("entity_type") or "PERSON").strip().upper(),
        "raw_fields": {field: value for field, value in values.items() if field in CORE_RAW_FIELDS},
        "original_fields": dict(source_row),
        "mapping_provenance": mapping.provenance(),
        "lineage": {"record_id": new_ordered_uuid(), "source_system": source, "source_record_id": external_id,
                    "source_file": filename, "source_row_number": line - 1},
        "benchmark_entity_id": (values.get("ground_truth") or "").strip() or None,
        "extra_fields": {column: source_row[column] for column in extra_columns},
    }


def _iter_normalized(stream: BinaryIO, filename: str) -> Iterator[dict]:
    with _csv_reader(stream, filename) as (reader, _headers, schema_type, extra_columns):
        for line, source_row in enumerate(reader, start=2):
            yield _normalized_row(filename, line, source_row, schema_type, extra_columns)


async def parse_upload(upload: UploadFile) -> ParsedFile:
    """Compatibility helper; run creation uses the streaming path below."""
    filename = upload.filename or "records.csv"
    upload.file.seek(0)
    content = upload.file.read()
    upload.file.seek(0)
    rows: list[dict] = []
    source_counts: dict[str, int] = {}
    seen: set[tuple[str, str]] = set()
    for row in _iter_normalized(upload.file, filename):
        identity = (row["source_system"], row["external_id"])
        if identity in seen:
            input_error("DUPLICATE_SOURCE_ID", "ID bị lặp trong cùng nguồn",
                        source_row_number=row["lineage"]["source_row_number"])
        seen.add(identity)
        rows.append(row)
        source = row["source_system"]
        source_counts[source] = source_counts.get(source, 0) + 1
    if not rows:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{filename} không có bản ghi")
    return ParsedFile(filename, hashlib.sha256(content).hexdigest(), rows, source_counts, content)


async def create_resolution_run(db: Session, uploads: list[UploadFile], owner_user_id: str,
                                name: str | None = None,
                                upload_session_id: str | None = None) -> tuple[ResolutionRun, dict]:
    sources = [(upload.filename or "records.csv", upload.file) for upload in uploads]
    return create_resolution_run_from_streams(db, sources, owner_user_id, name, upload_session_id)


def create_resolution_run_from_streams(db: Session, files: Sequence[tuple[str, BinaryIO]],
                                       owner_user_id: str, name: str | None = None,
                                       upload_session_id: str | None = None) -> tuple[ResolutionRun, dict]:
    if not files:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Chọn ít nhất một file records.csv")
    if len(files) > MAX_FILES_PER_RUN:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, f"Mỗi run tối đa {MAX_FILES_PER_RUN} file")
    normalized_name = (name or "").strip() or None
    if normalized_name and len(normalized_name) > 255:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Tên phiên tối đa 255 ký tự")

    if upload_session_id:
        existing = db.query(ResolutionRun).filter_by(upload_session_id=upload_session_id).one_or_none()
        if existing:
            stored_files = db.query(ResolutionFile).filter_by(run_id=existing.id).order_by(
                ResolutionFile.filename).all()
            counts = dict(db.query(ResolutionRecord.source_system, func.count(ResolutionRecord.id))
                          .filter_by(run_id=existing.id).group_by(ResolutionRecord.source_system).all())
            return existing, {"row_count": existing.record_count, "file_count": existing.file_count,
                              "source_counts": counts,
                              "files": [{"filename": row.filename, "row_count": row.row_count,
                                         "sha256": row.sha256} for row in stored_files]}

    run = ResolutionRun(owner_user_id=owner_user_id, upload_session_id=upload_session_id,
                        name=normalized_name, record_count=0,
                        file_count=len(files), policy_version=POLICY_VERSION)
    db.add(run)
    total_rows = 0
    source_counts: dict[str, int] = {}
    file_profiles = []
    r2 = None
    uploaded_keys: list[str] = []

    def compensate_uploads():
        # Keys belong exclusively to this uncommitted UUID run. Never remove
        # staging receipts: they remain available for an explicit retry.
        for key in uploaded_keys:
            try:
                r2.delete(key)
            except Exception as error:
                logging.error("SOURCE_COMPENSATION_FAILED run_id=%s error_type=%s",
                              run.id, type(error).__name__)
    try:
        db.flush()
        r2 = get_store(get_settings())
        for original_name, stream in files:
            filename = original_name.replace("\\", "/").rsplit("/", 1)[-1] or "records.csv"
            file_hash = hashlib.sha256()
            stream.seek(0)
            while block := stream.read(1024 * 1024):
                file_hash.update(block)
            digest = file_hash.hexdigest()
            file_row = ResolutionFile(run_id=run.id, filename=filename[:255], sha256=digest, row_count=0)
            db.add(file_row)
            db.flush()
            file_rows = 0
            batch = []
            for row in _iter_normalized(stream, filename):
                raw = row["raw_fields"]
                normalized, quality_flags = standardize(
                    {field: value.strip() for field, value in raw.items()}, entity_type=row["entity_type"])
                record_id = row["lineage"]["record_id"]
                lineage = {**row["lineage"], "record_id": record_id, "source_file_id": file_row.id,
                           "source_file_sha256": digest}
                batch.append({
                    "id": record_id,
                    "original_fields": row["original_fields"],
                    "mapping_provenance": row["mapping_provenance"], "lineage": lineage,
                    "run_id": run.id, "file_id": file_row.id, "external_id": row["external_id"],
                    "source_system": row["source_system"], "entity_type": row["entity_type"],
                    "benchmark_entity_id": row["benchmark_entity_id"],
                    "raw_fields": raw, "normalized_fields": normalized, "quality_flags": quality_flags,
                    "extra_fields": row["extra_fields"],
                })
                file_rows += 1
                total_rows += 1
                source = row["source_system"]
                source_counts[source] = source_counts.get(source, 0) + 1
                if len(batch) >= INSERT_BATCH_SIZE:
                    db.execute(insert(ResolutionRecord), batch)
                    batch.clear()
            if batch:
                db.execute(insert(ResolutionRecord), batch)
            if file_rows == 0:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{filename} không có bản ghi")
            file_row.row_count = file_rows
            if r2 is not None:
                key = source_file_key(run.id, file_row.id, filename)
                uploaded_keys.append(key)
                try:
                    stream.seek(0)
                    r2.upload_stream(stream, key, "text/csv; charset=utf-8")
                    file_row.r2_key = key
                except Exception as error:
                    logging.error("SOURCE_UPLOAD_FAILED run_id=%s error_type=%s",
                                  run.id, type(error).__name__)
                    raise HTTPException(502, "Source object storage unavailable; import was not committed") from None
            file_profiles.append({"filename": filename, "row_count": file_rows, "sha256": digest,
                                  "mapping_report": row["mapping_provenance"]})
            stream.seek(0)
        run.record_count = total_rows
        db.add(ResolutionAuditEvent(
            run_id=run.id, actor_user_id=owner_user_id, action="SESSION_CREATED",
            details={"file_count": len(file_profiles), "record_count": total_rows,
                     "files": [{"filename": item["filename"], "row_count": item["row_count"]}
                               for item in file_profiles]},
        ))
        db.commit()
        db.refresh(run)
    except IntegrityError as exc:
        db.rollback()
        compensate_uploads()
        raise InputMappingError("IMPORT_CONSTRAINT_VIOLATION",
                                "ID bị lặp trong cùng nguồn hoặc file bị trùng trong phiên") from exc
    except DBAPIError as exc:
        db.rollback()
        compensate_uploads()
        if getattr(exc.orig, "sqlstate", None) == "53100":
            raise HTTPException(
                status.HTTP_507_INSUFFICIENT_STORAGE,
                "Cơ sở dữ liệu đã hết dung lượng lưu trữ nên chưa thể tạo phiên. "
                "Cần tăng dung lượng hoặc giải phóng dữ liệu trước khi thử lại. "
                + ("File đã tải lên vẫn được giữ; bạn không cần upload lại."
                   if upload_session_id else "Phiên chưa được tạo."),
            ) from exc
        raise
    except Exception:
        db.rollback()
        compensate_uploads()
        raise
    profile = {"row_count": total_rows, "file_count": len(files), "source_counts": source_counts,
               "files": file_profiles}
    return run, profile
