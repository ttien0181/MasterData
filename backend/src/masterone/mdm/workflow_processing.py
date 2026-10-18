"""One processing command; existing fenced jobs produce reviewable proposals."""
from masterone.config import get_settings
from masterone.mdm.workflow_contracts import transition


def begin_processing(db, run_id, request, actor):
    from masterone.mdm.workflow_service import begin_operation, enabled_run, conflict
    from masterone.mdm.matching_foundation_service import latest_binding, verify_profile
    from masterone.mdm.matching_foundation_models import MatchingProfile
    run = enabled_run(db, run_id, actor)
    # Receipt replay uses the choices pinned by the first accepted command.
    # Feature/default changes must not turn a completed request into new work.
    from masterone.mdm.workflow_models import WorkflowOperation
    from masterone.mdm.workflow_service import operation_read
    prior = db.query(WorkflowOperation).filter_by(run_id=run_id, request_id=request.request_id).one_or_none()
    if prior is not None:
        if (prior.operation_type != 'DECIDE' or not prior.payload.get('processing')
                or any(prior.payload.get(key) != value for key, value in request.model_dump().items())):
            conflict('REQUEST_ID_PAYLOAD_MISMATCH')
        return operation_read(prior)
    payload = processing_payload(db, run, request)
    return begin_operation(db, run_id, 'DECIDE', request.request_id, payload,
                           request.expected_workflow_revision, actor)


def processing_payload(db, run, request):
    from masterone.mdm.workflow_service import conflict
    from masterone.mdm.matching_foundation_service import latest_binding, verify_profile
    from masterone.mdm.matching_foundation_models import MatchingProfile
    if get_settings().ai_api_only and request.model_scope is not None:
        conflict('API_ONLY_LOCAL_MODEL_DENIED')
    binding = latest_binding(db, run.id)
    profile = db.get(MatchingProfile, binding.profile_id) if binding else None
    allowed = ('RULE_WITH_EMBEDDING_EVIDENCE', 'RULE_HYBRID_DETERMINISTIC') if run.use_embedding else ('RULE_ONLY',)
    if profile is None or profile.config_json.get('kind') not in allowed:
        conflict('MVP_EMBEDDING_PROFILE_REQUIRED')
    verify_profile(profile, current=True, pinned=True)
    if profile.config_json.get('experiment') in ('E0', 'E1', 'E2'):
        conflict('MVP_RULE_AND_EMBEDDING_PROFILE_REQUIRED')
    if run.use_embedding and not get_settings().advanced_matching_agents_enabled:
        conflict('MVP_EMBEDDING_FEATURE_DISABLED')
    payload = {**request.model_dump(), 'processing': True, 'use_embedding': run.use_embedding,
               'demo_fast_mode': get_settings().demo_fast_mode,
               'mvp_step2': True, 'mvp_contract_version': 'step2_mvp_v1',
               'mvp_coordinator': profile.config_json.get('coordinator') or {'version': 'r1_v1', 'high': .85, 'low': .50}}
    return payload


def queue_after_prepare(db, run, prepared):
    """Queue in the publication transaction: retries/closed browsers cannot lose continuation."""
    from fastapi import HTTPException
    from masterone.auth.models import DemoGrant, User
    from masterone.auth.security import ROLE_CAPABILITIES
    from masterone.mdm.workflow_contracts import ProcessRequest
    from masterone.mdm.workflow_service import audit, enqueue, new_operation
    if not (get_settings().workflow_auto_process or db.get(DemoGrant, run.owner_user_id)):
        return
    actor = db.get(User, run.owner_user_id)
    if not actor or not {'start_run', 'approve_pair'} <= ROLE_CAPABILITIES.get(actor.role, frozenset()):
        return
    request = ProcessRequest(request_id='auto-process:' + prepared.id,
                             expected_workflow_revision=run.workflow_revision)
    try:
        payload = processing_payload(db, run, request)
    except HTTPException as error:
        # Preserve valid prepared data when an administrator still needs to configure AI.
        prepared.result = {**prepared.result, 'auto_process': {'status': 'NEEDS_CONFIGURATION', 'detail': error.detail}}
        return
    child = new_operation(db, run, 'DECIDE', request.request_id, payload, run.owner_user_id)
    enqueue(db, run, child)
    run.workflow_state = transition(run.workflow_state, 'DECIDE')
    run.workflow_revision += 1
    prepared.result = {**prepared.result, 'auto_process': {'status': 'QUEUED', 'operation_id': child.id}}
    audit(db, run, run.owner_user_id, 'WORKFLOW_AUTO_PROCESS', {'operation_id': child.id})


def continue_processing(db, run, op):
    """Called once in the same transaction that finishes the parent operation.

    A child and its queue pointer commit together with the parent receipt. A retry
    cannot see a completed parent without its continuation. No review is written.
    """
    if op.operation_type == 'PREPARE' and op.status == 'COMPLETED':
        queue_after_prepare(db, run, op)
        return
    if not op.payload.get('processing') or op.status != 'COMPLETED':
        return
    if op.operation_type not in ('DECIDE', 'PAIR_ADVICE'):
        return
    if op.payload.get('mvp_step2') or op.payload.get('demo_fast_mode'):
        # Review only needs the published assessment. Explicit preview/cluster
        # commands still run their normal snapshot, revision and safety guards.
        op.result = {**op.result, 'processing': {'review_ready': True,
            'optional_advice': 'NOT_REQUESTED', 'optional_preview': 'NOT_REQUESTED', 'demo_fast_mode': bool(op.payload.get('demo_fast_mode')),
            'mvp_step2': bool(op.payload.get('mvp_step2'))}}
        return
    from masterone.mdm.workflow_jobs import authorize_job
    from masterone.mdm.workflow_service import audit, enqueue, new_operation
    settings = get_settings()
    advice = (op.operation_type == 'DECIDE' and op.payload.get('include_advice', True)
              and settings.llm_enabled and settings.llm_pair_assessment_enabled
              and settings.llm_max_pair_assessment_calls_per_run > 0)
    kind = 'PAIR_ADVICE' if advice else 'CLUSTER_PREVIEW'
    # The deterministic request ID is independent of task attempts and retries.
    request_id = 'processing:' + op.id + ':' + kind.lower()
    payload = {'request_id': request_id, 'expected_workflow_revision': run.workflow_revision,
               'processing': True, 'processing_parent_id': op.id,
               'processing_root_id': op.payload.get('processing_root_id', op.id)}
    if advice:
        payload['limit'] = min(op.payload.get('advice_limit', 20), settings.llm_max_pair_assessment_calls_per_run)
    else:
        payload['allow_partial'] = True
    child = new_operation(db, run, kind, request_id, payload, op.actor_user_id)
    authorize_job(db, run, child)
    run.workflow_state = transition(run.workflow_state, kind)
    enqueue(db, run, child)
    run.workflow_revision += 1
    op.result = {**op.result, 'processing': {'next_operation_id': child.id, 'next_operation_type': kind}}
    audit(db, run, op.actor_user_id, 'WORKFLOW_PROCESSING_CONTINUED',
          {'parent_operation_id': op.id, 'operation_id': child.id, 'operation_type': kind})
