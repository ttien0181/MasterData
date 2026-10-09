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

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import func, insert
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from masterone.config import get_settings
from masterone.mdm.models import ResolutionAuditEvent, ResolutionFile, ResolutionRecord, ResolutionRun
from masterone.mdm.pipeline.matching import POLICY_VERSION
from masterone.mdm.pipeline.standardization import standardize
from masterone.storage.r2 import get_store, source_file_key

import os
from concurrent.futures import ProcessPoolExecutor

MAX_FILES_PER_RUN = 10
INSERT_BATCH_SIZE = 5000
# Python's csv default field limit is 128 KiB. File and row counts are not capped;
# rows are streamed into bounded insert batches below.
csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

LINH_COLUMNS = {"record_id", "source", "name", "phone", "email", "address"}
LEGACY_COLUMNS = {"id", "source_system", "full_name"}
LEGACY_FIELDS = {"full_name": "name", "phone_number": "phone",
                 "email_address": "email", "raw_address": "address"}
CRM_COLUMNS = {"contact_id", "source_system", "contact_name"}
CRM_FIELDS = {"contact_name": "name", "mobile": "phone", 
              "email": "email", "mailing_address": "address",
              "birth_date": "dob", "government_id": "government_id",
              "government_id_type": "government_id_type"}
BILL_COLUMNS = {"debtor_code", "source_system", "legal_name"}
BILL_FIELDS = {"legal_name": "name", "contact_number": "phone",
               "email": "email", "invoice_address": "address",
               "dob": "dob", "government_id": "government_id",
               "government_id_type": "government_id_type"}
ERP_COLUMNS = {"MA_KH", "source_system", "HO_TEN"}
ERP_FIELDS = {"HO_TEN": "name", "DIEN_THOAI": "phone",
              "email": "email", "DIA_CHI": "address",
              "NGAY_SINH": "dob", "government_id": "government_id",
              "government_id_type": "government_id_type"}
ALLOWED_SOURCES = {"CRM", "ERP", "BILL", "CSV"}
CORE_RAW_FIELDS = {"name", "phone", "email", "address", "dob", "government_id", "government_id_type"}


def _standardize_batch(rows):
    """Worker function cho multiprocessing."""
    results = []
    for row in rows:
        raw = row["raw_fields"]
        normalized, quality_flags = standardize(raw, entity_type=row["entity_type"])
        results.append({**row, "normalized_fields": normalized, "quality_flags": quality_flags})
    return results


@dataclass(frozen=True)
class ParsedFile:
    """Compatibility representation for small callers and parser tests."""

    filename: str
    sha256: str
    rows: list[dict]
    source_counts: dict[str, int]
    raw_bytes: bytes


def _validate_headers(filename: str, fieldnames: list[str] | None) -> tuple[set[str], str, list[str]]:
    headers = set(fieldnames or [])
    schema_type = ""
    if LINH_COLUMNS <= headers:
        schema_type = "LINH"
    elif CRM_COLUMNS <= headers:
        schema_type = "CRM"
    elif BILL_COLUMNS <= headers:
        schema_type = "BILL"
    elif ERP_COLUMNS <= headers:
        schema_type = "ERP"
    elif LEGACY_COLUMNS <= headers:
        schema_type = "LEGACY"

    if (not filename.lower().endswith(".csv") or "pairs.csv" in filename.lower()
            or not schema_type):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"{filename} cần các cột {', '.join(sorted(LINH_COLUMNS))} hoặc cấu trúc chuẩn tương đương",
        )
    reserved = {"record_id", "id", "contact_id", "debtor_code", "MA_KH", "source", "source_system"} | CORE_RAW_FIELDS | {"entity_id"}
    extra_columns = [column for column in (fieldnames or []) if column not in reserved]
    return headers, schema_type, extra_columns


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
            
        reader = csv.DictReader(text, dialect=dialect)
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


def _normalized_row(filename: str, line: int, source_row: dict, schema_type: str,
                    extra_columns: list[str]) -> dict:
    if None in source_row or any(value is None for value in source_row.values()):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{filename} sai số cột ở dòng {line}")
        
    if schema_type == "LINH":
        external_id = source_row.get("record_id")
        source = source_row.get("source")
        name = source_row.get("name")
    elif schema_type == "CRM":
        external_id = source_row.get("contact_id")
        source = source_row.get("source_system")
        name = source_row.get("contact_name")
    elif schema_type == "BILL":
        external_id = source_row.get("debtor_code")
        source = source_row.get("source_system")
        name = source_row.get("legal_name")
    elif schema_type == "ERP":
        external_id = source_row.get("MA_KH")
        source = source_row.get("source_system")
        name = source_row.get("HO_TEN")
    else:
        external_id = source_row.get("id")
        source = source_row.get("source_system")
        name = source_row.get("full_name")

    external_id = (external_id or "").strip()
    source = (source or "").strip().upper()
    name = (name or "").strip()
    entity_type = (source_row.get("entity_type") or "PERSON").strip().upper()
    if not external_id or not name or source not in ALLOWED_SOURCES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            f"{filename} có dữ liệu bắt buộc sai ở dòng {line}")

    if schema_type == "LINH":
        raw = {field: (source_row.get(field) or "").strip() for field in ("name", "phone", "email", "address")}
        for optional in ("dob", "government_id", "government_id_type"):
            if optional in source_row:
                raw[optional] = (source_row.get(optional) or "").strip()
    elif schema_type == "CRM":
        raw = {target: (source_row.get(column) or "").strip()
               for column, target in CRM_FIELDS.items() if column in source_row}
    elif schema_type == "BILL":
        raw = {target: (source_row.get(column) or "").strip()
               for column, target in BILL_FIELDS.items() if column in source_row}
    elif schema_type == "ERP":
        raw = {target: (source_row.get(column) or "").strip()
               for column, target in ERP_FIELDS.items() if column in source_row}
    else:
        raw = {target: (source_row.get(column) or "").strip()
               for column, target in LEGACY_FIELDS.items() if column in source_row}
               
    return {
        "external_id": external_id,
        "source_system": source,
        "entity_type": entity_type,
        "raw_fields": raw,
        "benchmark_entity_id": (source_row.get("entity_id") or "").strip() or None,
        "extra_fields": {column: (source_row.get(column) or "").strip() for column in extra_columns},
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
    seen: set[str] = set()
    for row in _iter_normalized(upload.file, filename):
        if row["external_id"] in seen:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                                f"{filename} lặp id {row['external_id']}")
        seen.add(row["external_id"])
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
                                       upload_session_id: str | None = None,
                                       known_hashes: list[str] | None = None,
                                       existing_run: ResolutionRun | None = None) -> tuple[ResolutionRun, dict]:
    if not files:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Chọn ít nhất một file records.csv")
    if len(files) > MAX_FILES_PER_RUN:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, f"Mỗi run tối đa {MAX_FILES_PER_RUN} file")
    normalized_name = (name or "").strip() or None
    if normalized_name and len(normalized_name) > 255:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Tên phiên tối đa 255 ký tự")

    if upload_session_id and existing_run is None:
        existing = db.query(ResolutionRun).filter_by(upload_session_id=upload_session_id).one_or_none()
        if existing and existing.status != "IMPORTING":
            stored_files = db.query(ResolutionFile).filter_by(run_id=existing.id).order_by(
                ResolutionFile.filename).all()
            counts = dict(db.query(ResolutionRecord.source_system, func.count(ResolutionRecord.id))
                          .filter_by(run_id=existing.id).group_by(ResolutionRecord.source_system).all())
            return existing, {"row_count": existing.record_count, "file_count": existing.file_count,
                              "source_counts": counts,
                              "files": [{"filename": row.filename, "row_count": row.row_count,
                                         "sha256": row.sha256} for row in stored_files]}

    run = existing_run or ResolutionRun(owner_user_id=owner_user_id, upload_session_id=upload_session_id,
                        name=normalized_name, record_count=0,
                        file_count=len(files), policy_version=POLICY_VERSION)
    if existing_run is None:
        db.add(run)
    total_rows = 0
    source_counts: dict[str, int] = {}
    file_profiles = []
    try:
        db.flush()
        r2 = get_store(get_settings())
        for i, (original_name, stream) in enumerate(files):
            filename = original_name.replace("\\", "/").rsplit("/", 1)[-1] or "records.csv"
            if known_hashes and i < len(known_hashes) and known_hashes[i]:
                digest = known_hashes[i]
            else:
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

            WORKER_BATCH = 2000
            workers = min(os.cpu_count() or 4, 4)

            def _iter_chunks(s, fname, chunk_size):
                chunk = []
                for row in _iter_normalized(s, fname):
                    chunk.append(row)
                    if len(chunk) >= chunk_size:
                        yield chunk
                        chunk = []
                if chunk:
                    yield chunk

            with ProcessPoolExecutor(max_workers=workers) as pool:
                for result_batch in pool.map(_standardize_batch, _iter_chunks(stream, filename, WORKER_BATCH)):
                    for row in result_batch:
                        batch.append({
                            "run_id": run.id, "file_id": file_row.id, "external_id": row["external_id"],
                            "source_system": row["source_system"], "entity_type": row["entity_type"],
                            "benchmark_entity_id": row["benchmark_entity_id"],
                            "raw_fields": row["raw_fields"], "normalized_fields": row["normalized_fields"], "quality_flags": row["quality_flags"],
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
                try:
                    stream.seek(0)
                    key = source_file_key(run.id, file_row.id, filename)
                    r2.upload_stream(stream, key, "text/csv; charset=utf-8")
                    file_row.r2_key = key
                except Exception:
                    logging.exception("R2 source upload failed for run %s file %s", run.id, filename)
            file_profiles.append({"filename": filename, "row_count": file_rows, "sha256": digest})
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
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            "ID bản ghi bị trùng trong cùng một phiên") from exc
    except DBAPIError as exc:
        db.rollback()
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
        raise
    profile = {"row_count": total_rows, "file_count": len(files), "source_counts": source_counts,
               "files": file_profiles}
    return run, profile
