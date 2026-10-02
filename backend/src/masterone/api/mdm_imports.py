from hashlib import sha256
import json
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, File, UploadFile, status
from sqlalchemy.orm import Session

from masterone.auth.security import CurrentSession, current_session, require_mutation
from masterone.config import get_settings
from masterone.db.session import get_session
from masterone.mdm.import_service import create_import
from masterone.mdm.matching import PrototypeMatcher
from masterone.mdm.models import Candidate, SourceRecord
from masterone.mdm.ownership import owned_import
from masterone.storage.checkpoint import save_stage_checkpoint
from masterone.storage.paths import JobPaths
from masterone.storage.repository import StorageJob, upsert_stage

router = APIRouter(prefix="/mdm/imports", tags=["MDM imports"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def post_import(
    crm_file: UploadFile = File(...), erp_file: UploadFile = File(...),
    bill_file: UploadFile | None = File(None), db: Session = Depends(get_session),
    auth: CurrentSession = Depends(require_mutation),
):
    batch = await create_import(db, crm_file, erp_file, auth.user.id, bill_file)
    settings = get_settings()
    job_id = batch.id
    source_digest = sha256()
    for record in batch.records:
        source_digest.update(json.dumps({
            "source": record.source,
            "source_record_id": record.source_record_id,
            "raw_fields": record.raw_fields,
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
    source_fingerprint = source_digest.hexdigest()
    job = StorageJob(
        job_id=job_id,
        owner_user_id=auth.user.id,
        status="running",
        current_stage="normalize",
        files=batch.profile,
        config={"candidate_limit": 1000, "engine_id": PrototypeMatcher.engine_id},
        row_count=len(batch.records),
    )
    db.add(job)
    db.flush()
    normalized_rows = [
        {
            "record_id": record.id,
            "source": record.source,
            "source_record_id": record.source_record_id,
            "raw_fields": json.dumps(record.raw_fields, ensure_ascii=False, sort_keys=True),
            "normalized_fields": json.dumps(record.normalized_fields, ensure_ascii=False, sort_keys=True),
        }
        for record in batch.records
    ]
    if settings.r2_bucket:
        manifest = save_stage_checkpoint(
            settings, job_id=job_id, stage="normalize", rows=normalized_rows,
            schema={"record_id": "string", "source": "string", "source_record_id": "string", "raw_fields": "json-string", "normalized_fields": "json-string"},
            input_uri="client://multipart", input_fingerprint=source_fingerprint,
            output_key=JobPaths(job_id).normalized(), config_hash="mdm-exact-signal-v1",
        )
        upsert_stage(
            db, job_id, "normalize", status="done", started_at=datetime.now(UTC), finished_at=datetime.now(UTC),
            input_uri=manifest.input_uri, output_uri=manifest.output_uri, row_count=manifest.row_count,
            bytes=manifest.bytes, schema=manifest.schema, input_fingerprint=manifest.input_fingerprint,
            config_hash=manifest.config_hash, code_version=manifest.code_version,
        )

    # Keep the existing matcher call and its input order unchanged.
    suggestions = PrototypeMatcher().match(batch.records)
    candidate_rows = [
        {"left_record_id": item.left_record_id, "right_record_id": item.right_record_id,
         "engine_id": item.engine_id, "evidence": json.dumps(item.evidence, ensure_ascii=False, sort_keys=True)}
        for item in suggestions
    ]
    if settings.r2_bucket:
        manifest = save_stage_checkpoint(
            settings, job_id=job_id, stage="exact_match", rows=candidate_rows,
            schema={"left_record_id": "string", "right_record_id": "string", "engine_id": "string", "evidence": "json-string"},
            input_uri=f"r2://{settings.r2_bucket}/{JobPaths(job_id).normalized()}", input_fingerprint=source_fingerprint,
            output_key=JobPaths(job_id).matching().replace(".jsonl", ".parquet"), config_hash="mdm-exact-signal-v1",
        )
        upsert_stage(
            db, job_id, "exact_match", status="done", started_at=datetime.now(UTC), finished_at=datetime.now(UTC),
            input_uri=manifest.input_uri, output_uri=manifest.output_uri, row_count=manifest.row_count,
            bytes=manifest.bytes, schema=manifest.schema, input_fingerprint=manifest.input_fingerprint,
            config_hash=manifest.config_hash, code_version=manifest.code_version,
        )
    for candidate in suggestions:
        db.add(candidate)
    if settings.r2_bucket:
        manifest = save_stage_checkpoint(
            settings, job_id=job_id, stage="result", rows=candidate_rows,
            schema={"left_record_id": "string", "right_record_id": "string", "engine_id": "string", "evidence": "json-string"},
            input_uri=f"r2://{settings.r2_bucket}/{JobPaths(job_id).matching().replace('.jsonl', '.parquet')}",
            input_fingerprint=source_fingerprint,
            output_key=JobPaths(job_id).result("result.parquet"), config_hash="mdm-exact-signal-v1",
        )
        upsert_stage(
            db, job_id, "result", status="done", started_at=datetime.now(UTC), finished_at=datetime.now(UTC),
            input_uri=manifest.input_uri, output_uri=manifest.output_uri, row_count=manifest.row_count,
            bytes=manifest.bytes, schema=manifest.schema, input_fingerprint=manifest.input_fingerprint,
            config_hash=manifest.config_hash, code_version=manifest.code_version,
        )
    job.status = "done"
    job.current_stage = "result"
    db.commit()
    return {"import_id": batch.id, "profile": batch.profile, "candidate_count": len(suggestions),
            "engine_id": PrototypeMatcher.engine_id, "job_id": job_id}


@router.get("/{import_id}")
def get_import(import_id: str, db: Session = Depends(get_session), auth: CurrentSession = Depends(current_session)):
    batch = owned_import(db, import_id, auth.user.id)
    return {"import_id": batch.id, "profile": batch.profile, "created_at": batch.created_at}


def record_summary(record: SourceRecord) -> dict:
    allowed = {"source_record_id", "name", "phone", "email", "address", "tax_id"}
    raw = {key: value for key, value in record.raw_fields.items() if key in allowed}
    normalized = {key: value for key, value in record.normalized_fields.items() if key in allowed}
    return {"id": record.id, "source": record.source, "source_record_id": record.source_record_id,
            "raw_fields": raw, "normalized_fields": normalized}


@router.get("/{import_id}/candidates")
def get_candidates(import_id: str, db: Session = Depends(get_session), auth: CurrentSession = Depends(current_session)):
    owned_import(db, import_id, auth.user.id)
    candidates = db.query(Candidate).filter_by(import_id=import_id).all()
    record_ids = {record_id for item in candidates for record_id in (item.left_record_id, item.right_record_id)}
    records = {record.id: record for record in db.query(SourceRecord).filter(SourceRecord.id.in_(record_ids)).all()} if record_ids else {}
    return [{"id": item.id, "engine_id": item.engine_id, "evidence": item.evidence, "decision": item.decision,
             "left": record_summary(records[item.left_record_id]), "right": record_summary(records[item.right_record_id])}
            for item in candidates]
