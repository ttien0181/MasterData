"""One processing command; existing fenced jobs produce reviewable proposals."""
from masterone.config import get_settings
from masterone.mdm.workflow_contracts import transition


def begin_processing(db, run_id, request, actor):
    from masterone.mdm.workflow_service import begin_operation
    payload = {**request.model_dump(), 'processing': True, 'demo_fast_mode': get_settings().demo_fast_mode}
    return begin_operation(db, run_id, 'DECIDE', request.request_id, payload,
                           request.expected_workflow_revision, actor)


def continue_processing(db, run, op):
    """Called once in the same transaction that finishes the parent operation.

    A child and its queue pointer commit together with the parent receipt. A retry
    cannot see a completed parent without its continuation. No review is written.
    """
    if not op.payload.get('processing') or op.status != 'COMPLETED':
        return
    if op.operation_type not in ('DECIDE', 'PAIR_ADVICE'):
        return
    if op.payload.get('demo_fast_mode'):
        # Review only needs the published assessment. Explicit preview/cluster
        # commands still run their normal snapshot, revision and safety guards.
        op.result = {**op.result, 'processing': {'review_ready': True,
            'optional_advice': 'NOT_REQUESTED', 'optional_preview': 'NOT_REQUESTED', 'demo_fast_mode': True}}
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
