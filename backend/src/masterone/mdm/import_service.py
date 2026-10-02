import csv
import io
from collections import Counter

from fastapi import HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from masterone.mdm.models import ImportBatch, SourceRecord

MAX_FILE_MIB = 10
MAX_FILE_BYTES = MAX_FILE_MIB * 1024 * 1024
MAX_ROWS = 10000
REQUIRED = {"name"}
OPTIONAL = {"phone", "email", "address", "tax_id"}


async def read_csv(upload: UploadFile, source: str) -> tuple[list[SourceRecord], dict]:
    content = await upload.read(MAX_FILE_BYTES + 1)
    if len(content) > MAX_FILE_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, f"{source} file exceeds {MAX_FILE_MIB} MiB")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{source} CSV must be UTF-8") from exc
    reader = csv.DictReader(io.StringIO(text, newline=""))
    headers = [header.strip() if header else "" for header in (reader.fieldnames or [])]
    reader.fieldnames = headers
    if "source_record_id" in headers and "record_id" in headers:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            f"{source} CSV must use only one ID header: record_id or source_record_id")
    id_column = "source_record_id" if "source_record_id" in headers else "record_id"
    missing = REQUIRED - set(headers)
    if id_column not in headers:
        missing.add("record_id (or source_record_id)")
    if missing:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{source} CSV missing headers: {', '.join(sorted(missing))}")
    rows = []
    identifiers = set()
    for line, raw in enumerate(reader, start=2):
        if line > MAX_ROWS + 1:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, f"{source} CSV exceeds {MAX_ROWS} rows")
        if None in raw:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{source} CSV has too many columns at row {line}")
        if any(value is None for value in raw.values()):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{source} CSV has too few columns at row {line}")
        raw_fields = {key: (value or "") for key, value in raw.items() if key}
        record_id = raw_fields.get(id_column, "").strip()
        raw_fields["source_record_id"] = record_id
        if not record_id or not raw_fields.get("name", "").strip():
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{source} CSV has an empty required value at row {line}")
        if record_id in identifiers:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{source} CSV repeats source_record_id at row {line}")
        identifiers.add(record_id)
        rows.append(SourceRecord(source=source, source_record_id=record_id, raw_fields=raw_fields,
            normalized_fields={key: value.strip().casefold() if key == "email" else value.strip()
                               for key, value in raw_fields.items()}))
    if not rows:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{source} CSV contains no data rows")
    missing_counts = Counter(key for row in rows for key in OPTIONAL if not row.raw_fields.get(key, "").strip())
    return rows, {"row_count": len(rows), "accepted_count": len(rows), "rejected_count": 0,
                  "missing_optional": dict(missing_counts), "validation_issue_count": 0}


async def create_import(
    db: Session, crm_file: UploadFile, erp_file: UploadFile, owner_user_id: str,
    bill_file: UploadFile | None = None,
) -> ImportBatch:
    crm_records, crm_profile = await read_csv(crm_file, "CRM")
    erp_records, erp_profile = await read_csv(erp_file, "ERP")
    records = crm_records + erp_records
    profile = {"CRM": crm_profile, "ERP": erp_profile}
    if bill_file is not None and bill_file.filename:
        bill_records, bill_profile = await read_csv(bill_file, "BILL")
        records += bill_records
        profile["BILL"] = bill_profile
    batch = ImportBatch(profile=profile, owner_user_id=owner_user_id)
    for record in records:
        batch.records.append(record)
    db.add(batch)
    db.commit()
    db.refresh(batch)
    return batch


