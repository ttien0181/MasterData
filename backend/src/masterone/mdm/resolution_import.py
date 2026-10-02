"""Validate and persist Linh's PERSON CSV files as an isolated resolution run."""

import csv
import hashlib
import io
from dataclasses import dataclass

from fastapi import HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from masterone.mdm.models import ResolutionFile, ResolutionRecord, ResolutionRun
from masterone.mdm.pipeline.standardization import standardize

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_ROWS_PER_FILE = 5000
MAX_FILES_PER_RUN = 10
REQUIRED_COLUMNS = {"id", "source_system", "full_name"}
FIELD_COLUMNS = {
    "full_name": "name",
    "phone_number": "phone",
    "email_address": "email",
    "raw_address": "address",
}
ALLOWED_SOURCES = {"CRM", "ERP", "BILL"}


@dataclass(frozen=True)
class ParsedFile:
    filename: str
    sha256: str
    rows: list[dict[str, str]]
    source_counts: dict[str, int]


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
    if "pairs.csv" in filename.lower() or not REQUIRED_COLUMNS <= headers:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"{filename} không đúng schema records.csv; cần các cột {', '.join(sorted(REQUIRED_COLUMNS))}",
        )
    rows: list[dict[str, str]] = []
    source_counts: dict[str, int] = {}
    seen: set[str] = set()
    for line, source_row in enumerate(reader, start=2):
        if line > MAX_ROWS_PER_FILE + 1:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, f"{filename} vượt quá 5.000 dòng")
        if None in source_row or any(value is None for value in source_row.values()):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{filename} sai số cột ở dòng {line}")
        external_id = (source_row.get("id") or "").strip()
        source = (source_row.get("source_system") or "").strip().upper()
        name = (source_row.get("full_name") or "").strip()
        if not external_id or not name or source not in ALLOWED_SOURCES:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{filename} có dữ liệu bắt buộc sai ở dòng {line}")
        if external_id in seen:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{filename} lặp id {external_id} ở dòng {line}")
        seen.add(external_id)
        raw = {target: (source_row.get(column) or "").strip() for column, target in FIELD_COLUMNS.items()}
        rows.append({"external_id": external_id, "source_system": source, "raw_fields": raw})
        source_counts[source] = source_counts.get(source, 0) + 1
    if not rows:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{filename} không có bản ghi")
    return ParsedFile(filename, hashlib.sha256(content).hexdigest(), rows, source_counts)


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

    run = ResolutionRun(owner_user_id=owner_user_id, record_count=total_rows, file_count=len(parsed_files))
    db.add(run)
    db.flush()
    source_counts: dict[str, int] = {}
    for parsed in parsed_files:
        file_row = ResolutionFile(run_id=run.id, filename=parsed.filename[:255], sha256=parsed.sha256,
                                  row_count=len(parsed.rows))
        db.add(file_row)
        db.flush()
        for row in parsed.rows:
            raw = row["raw_fields"]
            normalized, quality_flags = standardize(raw)
            db.add(ResolutionRecord(run_id=run.id, file_id=file_row.id, external_id=row["external_id"],
                                    source_system=row["source_system"], raw_fields=raw,
                                    normalized_fields=normalized, quality_flags=quality_flags))
            source = row["source_system"]
            source_counts[source] = source_counts.get(source, 0) + 1
    db.commit()
    db.refresh(run)
    profile = {"row_count": total_rows, "file_count": len(parsed_files), "source_counts": source_counts,
               "files": [{"filename": item.filename, "row_count": len(item.rows), "sha256": item.sha256}
                         for item in parsed_files]}
    return run, profile
