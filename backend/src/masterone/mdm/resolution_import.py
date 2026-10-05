"""Validate and persist CSV files as an isolated resolution run."""

import csv
import hashlib
import io
from dataclasses import dataclass

from fastapi import HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from masterone.config import get_settings
from masterone.mdm.models import ResolutionFile, ResolutionRecord, ResolutionRun
from masterone.mdm.pipeline.matching import POLICY_VERSION
from masterone.mdm.pipeline.standardization import standardize
from masterone.storage.r2 import get_store, source_file_key

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_ROWS_PER_FILE = 10000
MAX_FILES_PER_RUN = 10

# Core fields that must be present in every supported CSV format
LINH_COLUMNS = {"record_id", "source", "name", "phone", "email", "address"}
LEGACY_COLUMNS = {"id", "source_system", "full_name"}
LEGACY_FIELDS = {"full_name": "name", "phone_number": "phone",
                 "email_address": "email", "raw_address": "address"}
ALLOWED_SOURCES = {"CRM", "ERP", "BILL"}

# Fields extracted into raw_fields for standardization
CORE_RAW_FIELDS = {"name", "phone", "email", "address", "dob", "government_id"}

# Benchmark / metadata columns that are passed through as extra_fields (not standardized)
BENCHMARK_COLS = {"noise_type", "difficulty", "temporal_case"}


@dataclass(frozen=True)
class ParsedFile:
    filename: str
    sha256: str
    rows: list[dict]
    source_counts: dict[str, int]
    raw_bytes: bytes  # original upload bytes — uploaded to R2 when configured


async def parse_upload(upload: UploadFile) -> ParsedFile:
    content = await upload.read(MAX_FILE_BYTES + 1)
    filename = upload.filename or "records.csv"
    if len(content) > MAX_FILE_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, f"{filename} vượt quá 5 MiB")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{filename} phải dùng UTF-8") from exc
    reader = csv.DictReader(io.StringIO(text, newline=""))
    headers = set(reader.fieldnames or [])
    is_linh_file = LINH_COLUMNS <= headers
    if "pairs.csv" in filename.lower() or not (is_linh_file or LEGACY_COLUMNS <= headers):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"{filename} cần các cột {', '.join(sorted(LINH_COLUMNS))}",
        )

    # All columns not used as core identifiers are eligible as extra_fields
    reserved = {"record_id", "id", "source", "source_system"} | CORE_RAW_FIELDS | {"entity_id"}
    extra_col_names = [h for h in (reader.fieldnames or []) if h not in reserved]

    rows: list[dict] = []
    source_counts: dict[str, int] = {}
    seen: set[str] = set()
    for line, source_row in enumerate(reader, start=2):
        if line > MAX_ROWS_PER_FILE + 1:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, f"{filename} vượt quá {MAX_ROWS_PER_FILE:,} dòng")
        if None in source_row or any(value is None for value in source_row.values()):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{filename} sai số cột ở dòng {line}")

        external_id = (source_row.get("record_id" if is_linh_file else "id") or "").strip()
        source = (source_row.get("source" if is_linh_file else "source_system") or "").strip().upper()
        name = (source_row.get("name" if is_linh_file else "full_name") or "").strip()
        if not external_id or not name or source not in ALLOWED_SOURCES:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                                f"{filename} có dữ liệu bắt buộc sai ở dòng {line}")
        if external_id in seen:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                                f"{filename} lặp id {external_id} ở dòng {line}")
        seen.add(external_id)

        if is_linh_file:
            # Always include the 4 primary fields; include optional fields only when present in headers
            raw = {field: (source_row.get(field) or "").strip() for field in ("name", "phone", "email", "address")}
            for opt in ("dob", "government_id"):
                if opt in headers:
                    raw[opt] = (source_row.get(opt) or "").strip()
        else:
            raw = {target: (source_row.get(col) or "").strip() for col, target in LEGACY_FIELDS.items()}

        # Benchmark ground-truth label (entity_id column = cluster label for evaluation)
        benchmark_entity_id = (source_row.get("entity_id") or "").strip() or None

        # All remaining columns stored verbatim for observability / benchmark metadata
        extra_fields = {col: (source_row.get(col) or "").strip() for col in extra_col_names}

        rows.append({
            "external_id": external_id,
            "source_system": source,
            "raw_fields": raw,
            "benchmark_entity_id": benchmark_entity_id,
            "extra_fields": extra_fields,
        })
        source_counts[source] = source_counts.get(source, 0) + 1

    if not rows:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{filename} không có bản ghi")
    return ParsedFile(filename, hashlib.sha256(content).hexdigest(), rows, source_counts, content)


async def create_resolution_run(db: Session, uploads: list[UploadFile], owner_user_id: str) -> tuple[ResolutionRun, dict]:
    if not uploads:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Chọn ít nhất một file records.csv")
    if len(uploads) > MAX_FILES_PER_RUN:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, f"Mỗi run tối đa {MAX_FILES_PER_RUN} file")
    parsed_files = [await parse_upload(upload) for upload in uploads]
    identifiers: set[str] = set()
    total_rows = 0
    for parsed in parsed_files:
        for row in parsed.rows:
            if row["external_id"] in identifiers:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    f"ID {row['external_id']} bị lặp giữa các file trong cùng run",
                )
            identifiers.add(row["external_id"])
            total_rows += 1

    run = ResolutionRun(owner_user_id=owner_user_id, record_count=total_rows,
                        file_count=len(parsed_files), policy_version=POLICY_VERSION)
    db.add(run)
    db.flush()
    settings = get_settings()
    r2 = get_store(settings)
    source_counts: dict[str, int] = {}
    for parsed in parsed_files:
        file_row = ResolutionFile(run_id=run.id, filename=parsed.filename[:255], sha256=parsed.sha256,
                                  row_count=len(parsed.rows))
        db.add(file_row)
        db.flush()
        # Upload original CSV to R2 for audit/replay (best-effort — never block on failure)
        if r2 is not None:
            try:
                key = source_file_key(run.id, file_row.id, parsed.filename)
                r2.upload_bytes(parsed.raw_bytes, key, "text/csv; charset=utf-8")
                file_row.r2_key = key
            except Exception as e:
                import logging
                logging.error(f"R2 upload failed: {e}", exc_info=True)
                pass  # R2 unavailable → continue without upload
        for row in parsed.rows:
            raw = row["raw_fields"]
            normalized, quality_flags = standardize(raw)
            db.add(ResolutionRecord(
                run_id=run.id,
                file_id=file_row.id,
                external_id=row["external_id"],
                source_system=row["source_system"],
                benchmark_entity_id=row["benchmark_entity_id"],
                raw_fields=raw,
                normalized_fields=normalized,
                quality_flags=quality_flags,
                extra_fields=row["extra_fields"],
            ))
            source = row["source_system"]
            source_counts[source] = source_counts.get(source, 0) + 1
    db.commit()
    db.refresh(run)
    profile = {"row_count": total_rows, "file_count": len(parsed_files), "source_counts": source_counts,
               "files": [{"filename": item.filename, "row_count": len(item.rows), "sha256": item.sha256}
                         for item in parsed_files]}
    return run, profile
