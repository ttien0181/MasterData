"""Validate and persist PERSON CSV files as an isolated resolution run.

Two entry points
----------------
* ``create_resolution_run``      — legacy multipart upload (UploadFile).
  Still works for dev/testing without R2.
* ``create_resolution_run_from_r2`` — production path: frontend uploads
  directly to R2 via presigned URL; backend only receives object keys.
  Files are streamed from R2 and optionally deleted after processing.
"""

import csv
import hashlib
import io
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from masterone.mdm.models import ResolutionFile, ResolutionRecord, ResolutionRun
from masterone.mdm.pipeline.standardization import standardize

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_ROWS_PER_FILE = 10_000
MAX_FILES_PER_RUN = 10

LEGACY_REQUIRED_COLUMNS = {"id", "source_system", "full_name"}
CURRENT_REQUIRED_COLUMNS = {"record_id", "source", "name"}
LEGACY_FIELD_COLUMNS = {
    "full_name": "name",
    "phone_number": "phone",
    "email_address": "email",
    "raw_address": "address",
}
CURRENT_FIELD_COLUMNS = {
    "name": "name",
    "phone": "phone",
    "email": "email",
    "address": "address",
}
ALLOWED_SOURCES = {"CRM", "ERP", "BILL"}

# Prefix used for R2 objects belonging to this pipeline.
R2_RUN_PREFIX = "runs-incoming/"


@dataclass(frozen=True)
class ParsedFile:
    filename: str
    sha256: str
    rows: list[dict[str, str]]
    source_counts: dict[str, int]


# ─────────────────────────────────────────────────────────────────────────────
# Internal CSV parser — shared by both upload paths
# ─────────────────────────────────────────────────────────────────────────────

def _parse_csv_bytes(content: bytes, filename: str) -> ParsedFile:
    """Parse and validate raw CSV bytes.  Raises HTTPException on any error."""
    sha256_hex = hashlib.sha256(content).hexdigest()
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"{filename} phải dùng UTF-8",
        ) from exc

    reader = csv.DictReader(io.StringIO(text, newline=""))
    normalized_headers = [
        header.strip() if header else "" for header in (reader.fieldnames or [])
    ]
    reader.fieldnames = normalized_headers
    headers = set(normalized_headers)

    is_legacy  = LEGACY_REQUIRED_COLUMNS  <= headers
    is_current = CURRENT_REQUIRED_COLUMNS <= headers

    if "pairs.csv" in filename.lower() or not (is_legacy or is_current):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"{filename} không đúng schema; cần một trong các bộ cột "
            f"[{', '.join(sorted(CURRENT_REQUIRED_COLUMNS))}] hoặc "
            f"[{', '.join(sorted(LEGACY_REQUIRED_COLUMNS))}]",
        )

    if is_current:
        id_col, src_col, _name_col = "record_id", "source", "name"
        field_columns = CURRENT_FIELD_COLUMNS
    else:
        id_col, src_col, _name_col = "id", "source_system", "full_name"
        field_columns = LEGACY_FIELD_COLUMNS

    rows: list[dict[str, str]] = []
    source_counts: dict[str, int] = {}
    seen: set[str] = set()

    for line, source_row in enumerate(reader, start=2):
        if line > MAX_ROWS_PER_FILE + 1:
            raise HTTPException(
                status.HTTP_413_CONTENT_TOO_LARGE,
                f"{filename} vượt quá {MAX_ROWS_PER_FILE:,} dòng",
            )
        if None in source_row or any(v is None for v in source_row.values()):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"{filename} sai số cột ở dòng {line}",
            )
        external_id = (source_row.get(id_col)  or "").strip()
        source      = (source_row.get(src_col) or "").strip().upper()
        name        = (source_row.get(_name_col) or "").strip()
        if not external_id or not name or source not in ALLOWED_SOURCES:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"{filename} có dữ liệu bắt buộc sai ở dòng {line}",
            )
        if external_id in seen:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"{filename} lặp id {external_id} ở dòng {line}",
            )
        seen.add(external_id)
        raw = {target: (source_row.get(col) or "").strip() for col, target in field_columns.items()}
        rows.append({"external_id": external_id, "source_system": source, "raw_fields": raw})
        source_counts[source] = source_counts.get(source, 0) + 1

    if not rows:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"{filename} không có bản ghi",
        )
    return ParsedFile(filename, sha256_hex, rows, source_counts)


# ─────────────────────────────────────────────────────────────────────────────
# Public parse helpers
# ─────────────────────────────────────────────────────────────────────────────

async def parse_upload(upload: UploadFile) -> ParsedFile:
    """Parse a multipart UploadFile — reads entirely into RAM."""
    content  = await upload.read(MAX_FILE_BYTES + 1)
    filename = upload.filename or "records.csv"
    if len(content) > MAX_FILE_BYTES:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"{filename} vượt quá 5 MiB",
        )
    return _parse_csv_bytes(content, filename)


def parse_from_r2(r2_client, bucket: str, object_key: str) -> ParsedFile:
    """Stream a CSV from R2 and parse it.

    The file content is fetched via a single R2 ``get_object`` call and read
    into memory (bounded by ``MAX_FILE_BYTES``).  This avoids multipart
    overhead on the API server while keeping the same 5 MiB / 10 k-row limits.
    """
    filename = Path(object_key).name
    try:
        response = r2_client.get_object(Bucket=bucket, Key=object_key)
        body     = response["Body"]
        try:
            content = body.read(MAX_FILE_BYTES + 1)
        finally:
            body.close()
    except Exception as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"Không đọc được {object_key} từ R2",
        ) from exc

    if len(content) > MAX_FILE_BYTES:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"{filename} vượt quá 5 MiB",
        )
    return _parse_csv_bytes(content, filename)


# ─────────────────────────────────────────────────────────────────────────────
# DB persistence — shared logic
# ─────────────────────────────────────────────────────────────────────────────

def _persist_parsed_files(
    db: Session,
    parsed_files: list[ParsedFile],
    owner_user_id: str,
) -> tuple[ResolutionRun, dict]:
    """Write a list of ParsedFile objects to the database and return the run."""
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

    run = ResolutionRun(
        owner_user_id=owner_user_id,
        record_count=total_rows,
        file_count=len(parsed_files),
    )
    db.add(run)
    db.flush()

    source_counts: dict[str, int] = {}
    for parsed in parsed_files:
        file_row = ResolutionFile(
            run_id=run.id,
            filename=parsed.filename[:255],
            sha256=parsed.sha256,
            row_count=len(parsed.rows),
        )
        db.add(file_row)
        db.flush()
        for row in parsed.rows:
            raw = row["raw_fields"]
            normalized, quality_flags = standardize(raw)
            db.add(ResolutionRecord(
                run_id=run.id,
                file_id=file_row.id,
                external_id=row["external_id"],
                source_system=row["source_system"],
                raw_fields=raw,
                normalized_fields=normalized,
                quality_flags=quality_flags,
            ))
            source_counts[row["source_system"]] = source_counts.get(row["source_system"], 0) + 1

    db.commit()
    db.refresh(run)
    profile = {
        "row_count": total_rows,
        "file_count": len(parsed_files),
        "source_counts": source_counts,
        "files": [
            {"filename": p.filename, "row_count": len(p.rows), "sha256": p.sha256}
            for p in parsed_files
        ],
    }
    return run, profile


# ─────────────────────────────────────────────────────────────────────────────
# Public entry points
# ─────────────────────────────────────────────────────────────────────────────

async def create_resolution_run(
    db: Session,
    uploads: list[UploadFile],
    owner_user_id: str,
) -> tuple[ResolutionRun, dict]:
    """Legacy multipart path — reads files directly from the HTTP request."""
    if not uploads:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Chọn ít nhất một file records.csv",
        )
    if len(uploads) > MAX_FILES_PER_RUN:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"Mỗi run tối đa {MAX_FILES_PER_RUN} file",
        )
    parsed_files = [await parse_upload(upload) for upload in uploads]
    return _persist_parsed_files(db, parsed_files, owner_user_id)


def create_resolution_run_from_r2(
    db: Session,
    r2_client,
    bucket: str,
    object_keys: list[str],
    owner_user_id: str,
    cleanup_sources: bool = True,
) -> tuple[ResolutionRun, dict]:
    """R2 path — frontend uploaded files directly; backend streams from R2.

    Args:
        db:              SQLAlchemy session.
        r2_client:       Boto3 S3-compatible client pointing at R2.
        bucket:          R2 bucket name.
        object_keys:     List of R2 object keys (must be under ``runs-incoming/``).
        owner_user_id:   ID of the authenticated user.
        cleanup_sources: Delete R2 objects after successful parse (default True).

    Returns:
        (ResolutionRun, profile dict)
    """
    if not object_keys:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Chọn ít nhất một object key",
        )
    if len(object_keys) > MAX_FILES_PER_RUN:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"Mỗi run tối đa {MAX_FILES_PER_RUN} file",
        )
    if any(not key.startswith(R2_RUN_PREFIX) for key in object_keys):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Object keys phải nằm trong '{R2_RUN_PREFIX}'",
        )

    # Stream and parse each file from R2.
    parsed_files = [parse_from_r2(r2_client, bucket, key) for key in object_keys]

    # Persist to DB.
    run, profile = _persist_parsed_files(db, parsed_files, owner_user_id)

    # Clean up R2 objects after successful save.
    if cleanup_sources:
        for key in object_keys:
            try:
                r2_client.delete_object(Bucket=bucket, Key=key)
            except Exception:
                pass  # Best-effort; do not fail the run over cleanup errors.

    return run, profile
