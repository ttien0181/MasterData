from fastapi import APIRouter, Depends, File, UploadFile, status
from sqlalchemy.orm import Session

from masterone.auth.security import CurrentSession, current_session, require_mutation
from masterone.db.session import get_session
from masterone.mdm.import_service import create_import
from masterone.mdm.matching import PrototypeMatcher
from masterone.mdm.models import Candidate, SourceRecord
from masterone.mdm.ownership import owned_import

router = APIRouter(prefix="/mdm/imports", tags=["MDM imports"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def post_import(
    crm_file: UploadFile = File(...), erp_file: UploadFile = File(...), db: Session = Depends(get_session),
    auth: CurrentSession = Depends(require_mutation),
):
    batch = await create_import(db, crm_file, erp_file, auth.user.id)
    suggestions = PrototypeMatcher().match(batch.records)
    for candidate in suggestions:
        db.add(candidate)
    db.commit()
    return {"import_id": batch.id, "profile": batch.profile, "candidate_count": len(suggestions),
            "engine_id": PrototypeMatcher.engine_id}


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
