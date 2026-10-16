"""The same required-review definition for queries, summaries and publication."""
from sqlalchemy import and_, or_

from masterone.mdm.workflow_assessment_reads import effective_column
from masterone.mdm.workflow_models import WorkflowPair

# Insufficient evidence is retained for inspection/reassessment, not mandatory
# work. An explicit human deferral still requires a decision.
TERMINAL_BASELINES = ('MERGEABLE', 'NON_MATCH', 'NOT_MERGEABLE', 'BLOCKED', 'INSUFFICIENT_EVIDENCE')


def review_required(decision, baseline):
    return decision == 'DEFERRED' or (decision == 'UNDECIDED' and (
        baseline.get('decision') not in TERMINAL_BASELINES or
        baseline.get('decision') == 'MERGEABLE' and baseline.get('triage_status') == 'REVIEW'))


def pending_predicate(*, baseline=None, triage=None):
    effective = effective_column()
    if baseline is None:
        baseline = effective['decision'].as_string()
    if triage is None:
        triage = effective['triage_status'].as_string()
    return or_(WorkflowPair.decision == 'DEFERRED', and_(WorkflowPair.decision == 'UNDECIDED',
        or_(baseline.is_(None), baseline.notin_(TERMINAL_BASELINES),
            and_(baseline == 'MERGEABLE', triage == 'REVIEW'))))
