from fastapi import HTTPException
from sqlalchemy.orm import Session

from masterone.mdm.models import Candidate, ImportBatch


def owned_import(db: Session, import_id: str, user_id: str) -> ImportBatch:
    batch = db.get(ImportBatch, import_id)
    if batch is None or batch.owner_user_id != user_id:
        raise HTTPException(404, "Import not found")
    return batch


def owned_candidate(db: Session, candidate_id: str, user_id: str) -> Candidate:
    candidate = db.get(Candidate, candidate_id)
    if candidate is None:
        raise HTTPException(404, "Candidate not found")
    batch = db.get(ImportBatch, candidate.import_id)
    if batch is None or batch.owner_user_id != user_id:
        raise HTTPException(404, "Candidate not found")
    return candidate
