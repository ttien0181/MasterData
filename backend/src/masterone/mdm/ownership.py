from fastapi import HTTPException
from sqlalchemy.orm import Session

from masterone.mdm.models import ResolutionRun


def owned_run(db: Session, run_id: str, user_id: str) -> ResolutionRun:
    run = db.get(ResolutionRun, run_id)
    if run is None or run.owner_user_id != user_id:
        raise HTTPException(404, "Run not found")
    return run
