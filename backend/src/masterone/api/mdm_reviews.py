from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from masterone.auth.security import CurrentSession, current_session, require_mutation
from masterone.db.base import now_utc
from masterone.db.session import get_session
from masterone.mdm.models import AuditEvent, Candidate, GoldenRecordDraft, SourceRecord
from masterone.mdm.ownership import owned_candidate
from masterone.mdm.schemas import FIELD_NAMES, DecisionRequest, GoldenRecordRequest

router = APIRouter(prefix="/mdm/candidates", tags=["MDM review"])
TRANSITIONS = {"pending": {"accepted", "rejected", "needs_review"},
               "needs_review": {"accepted", "rejected"}, "accepted": set(), "rejected": set()}


@router.patch("/{candidate_id}/decision")
def update_decision(candidate_id: str, request: DecisionRequest, db: Session = Depends(get_session),
                    auth: CurrentSession = Depends(require_mutation)):
    candidate = owned_candidate(db, candidate_id, auth.user.id)
    if request.decision not in TRANSITIONS[candidate.decision]:
        raise HTTPException(409, f"Decision cannot change from {candidate.decision} to {request.decision}")
    previous = candidate.decision
    candidate.decision = request.decision
    db.add(AuditEvent(candidate_id=candidate.id, action="decision_changed",
                      details={"previous": previous, "decision": request.decision, "note": request.note}, actor=auth.user.id))
    db.commit()
    return {"candidate_id": candidate.id, "decision": candidate.decision}


def get_pair(db: Session, candidate: Candidate) -> dict[str, SourceRecord]:
    records = db.query(SourceRecord).filter(SourceRecord.id.in_([candidate.left_record_id, candidate.right_record_id])).all()
    return {record.id: record for record in records}


@router.get("/{candidate_id}/golden-record")
def get_draft(candidate_id: str, db: Session = Depends(get_session), auth: CurrentSession = Depends(current_session)):
    owned_candidate(db, candidate_id, auth.user.id)
    draft = db.query(GoldenRecordDraft).filter_by(candidate_id=candidate_id).first()
    return {"candidate_id": candidate_id, "state": "draft" if draft else None,
            "fields": draft.fields if draft else {}}


@router.put("/{candidate_id}/golden-record")
def save_draft(candidate_id: str, request: GoldenRecordRequest, db: Session = Depends(get_session),
               auth: CurrentSession = Depends(require_mutation)):
    candidate = owned_candidate(db, candidate_id, auth.user.id)
    if candidate.decision != "accepted":
        raise HTTPException(409, "Accept the candidate before saving a Golden Record draft")
    records = get_pair(db, candidate)
    if set(request.fields) - set(FIELD_NAMES):
        raise HTTPException(422, "Unknown Golden Record field")
    for field, choice in request.fields.items():
        record = records.get(choice.source_record_id)
        if record is None or record.raw_fields.get(field, "") != choice.value:
            raise HTTPException(422, f"Field {field} must use a value from the reviewed pair")
    required_fields = {field for field in FIELD_NAMES if any(record.raw_fields.get(field, "") for record in records.values())}
    missing_fields = required_fields - request.fields.keys()
    if missing_fields:
        raise HTTPException(422, f"Choose a source for every populated field: {', '.join(sorted(missing_fields))}")
    draft = db.query(GoldenRecordDraft).filter_by(candidate_id=candidate_id).first()
    if draft is None:
        draft = GoldenRecordDraft(candidate_id=candidate_id, fields={})
        db.add(draft)
    draft.fields = {key: value.model_dump() for key, value in request.fields.items()}
    db.add(AuditEvent(candidate_id=candidate_id, action="golden_record_draft_saved",
                      details={"fields": draft.fields}, actor=auth.user.id))
    draft.updated_at = now_utc()
    db.commit()
    return {"candidate_id": candidate_id, "state": "draft", "fields": draft.fields}
