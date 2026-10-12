"""Aggregate-only explanations bound to workflow revisions and gateway accounting."""
from collections import Counter

from fastapi import HTTPException
from sqlalchemy.orm import sessionmaker

from masterone.config import get_settings
from masterone.llm.explanation import explain_cohort, template_explanation
from masterone.llm.policy import digest, fence
from masterone.mdm.models import ResolutionRun
from masterone.mdm.workflow_models import WorkflowPair
from masterone.mdm.workflow_assessment_reads import effective_result
from masterone.mdm.workflow_service import check_revision, enabled_run


def explain_pair(db, run_id, pair_id, request, actor):
    run = enabled_run(db, run_id, actor)
    check_revision(run, request.model_dump())
    pair = db.query(WorkflowPair).filter_by(run_id=run_id, id=pair_id).one_or_none()
    if pair is None:
        raise HTTPException(404, 'Pair not found')
    settings = get_settings()
    if not settings.llm_enabled or not settings.llm_explanation_enabled:
        return template_explanation('LLM_DISABLED')
    fields = {}
    result = effective_result(pair)
    for item in result.get('field_evidence', []):
        if item.get('field') in ('name', 'phone', 'email', 'address'):
            state = item.get('state', 'MI')
            band = 'missing' if state in ('MI', 'NA', 'IV') else 'exact' if state == 'EX' else 'different' if state in ('DF', 'CF') else 'ambiguous' if state in ('SH', 'NC') else 'similar'
            fields[item['field']] = dict(Counter([band]))
    payload = {'pair_count': 1, 'unique_record_count': 2, 'field_signal_counts': fields,
               'group': result.get('primary_group') or 'UNKNOWN',
               'subgroup': result.get('subgroup') or 'UNKNOWN', 'score_is_probability': False}
    context = {'run_id': run_id, 'user_id': actor, 'fence': fence(db, run_id),
               'input_hash': digest(result), 'review_hash': digest([pair.decision, pair.revision, run.decision_revision, pair.assessment_id]),
               'reference_version': 'workflow-pair-v1', 'reference_hash': digest(fields)}
    expected = (run.workflow_revision, pair.revision)
    factory = sessionmaker(db.get_bind(), expire_on_commit=False)
    db.rollback()  # Never retain the run lock during a remote request.
    result = explain_cohort(payload, settings, context=context, factory=factory)
    db.expire_all()
    run = db.get(ResolutionRun, run_id)
    pair = db.get(WorkflowPair, pair_id)
    if (run.workflow_revision, pair.revision) != expected:
        return {'status': 'STALE', 'explanation': None, 'error_code': 'STALE'}
    return result
