"""The same required-review definition for queries, summaries and publication."""
from sqlalchemy import and_, or_

from masterone.mdm.workflow_assessment_reads import effective_column
from masterone.mdm.workflow_models import WorkflowPair

TERMINAL_BASELINES = ('MERGEABLE', 'NON_MATCH', 'NOT_MERGEABLE', 'BLOCKED')


def review_required(decision, baseline):
    return decision == 'DEFERRED' or (decision == 'UNDECIDED' and (
        baseline.get('decision') not in TERMINAL_BASELINES or
        baseline.get('decision') == 'MERGEABLE' and baseline.get('triage_status') == 'REVIEW'))


def pending_predicate():
    effective = effective_column()
    baseline, triage = effective['decision'].as_string(), effective['triage_status'].as_string()
    return or_(WorkflowPair.decision == 'DEFERRED', and_(WorkflowPair.decision == 'UNDECIDED',
        or_(baseline.is_(None), baseline.notin_(TERMINAL_BASELINES),
            and_(baseline == 'MERGEABLE', triage == 'REVIEW'))))
