"""One effective evaluation shared by SQL filtering, review and clustering."""
from sqlalchemy import JSON, func

from masterone.mdm.workflow_models import WorkflowAssessment, WorkflowPair


def effective_result(pair):
    return pair.assessment if pair.assessment is not None else pair.baseline


def effective_column():
    return func.coalesce(WorkflowPair.assessment, WorkflowPair.baseline, type_=JSON)


def active_assessment(db, run_id):
    return db.query(WorkflowAssessment).filter_by(run_id=run_id, active=True).one_or_none()
