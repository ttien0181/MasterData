"""Workflow commands serialize on the engine's shared run lock."""
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import String, column, func, or_, select, true
from sqlalchemy.orm import aliased

from masterone.config import get_settings
from masterone.mdm.generation_staging import digest, lock_run
from masterone.mdm.models import ResolutionAuditEvent, ResolutionRun, ResolutionTask
from masterone.mdm.ownership import owned_run
from masterone.mdm.workflow_contracts import transition
from masterone.mdm.workflow_models import WorkflowConflict, WorkflowOperation, WorkflowPair
from masterone.mdm.workflow_review_rules import pending_predicate

ACTIVE = ('QUEUED', 'RUNNING')
_UNSET = object()


def conflict(code, **details):
    raise HTTPException(409, {'code': code, **details})


def operation_read(op, *, summary=False):
    result = op.result
    if summary and op.operation_type == 'CLUSTER':
        fields = ('publication_status', 'clusters', 'multi_record_groups', 'singletons',
                  'covered_records', 'unresolved_records', 'total_records')
        result = {field: result[field] for field in fields if field in result}
        result['blocker_count'] = op.result.get('blocker_count', len(op.result.get('blockers', [])))
    return dict(operation_id=op.id, run_id=op.run_id, operation_type=op.operation_type,
                status=op.status, stage=op.stage, progress=op.progress,
                error_code=op.error_code, result=result)


def enabled_run(db, run_id, actor):
    owned_run(db, run_id, actor)
    run = lock_run(db, run_id)
    db.refresh(run)  # A waiter must not retain the pre-lock identity-map revision.
    if run.workflow_version != 2:
        conflict('LEGACY_READ_ONLY')
    if not get_settings().session_workflow_enabled:
        conflict('SESSION_WORKFLOW_DISABLED')
    if run.archived_at is not None:
        conflict('RUN_ARCHIVED')
    return run


def active_operation(db, run_id):
    return db.query(WorkflowOperation).filter(WorkflowOperation.run_id == run_id,
             WorkflowOperation.status.in_(ACTIVE)).one_or_none()


def review_blocking_operation(db, run_id):
    op = active_operation(db, run_id)
    return op if op and op.operation_type != 'CLUSTER_PREVIEW' else None


def retryable_operation(db, run, latest=_UNSET):
    if latest is _UNSET:
        latest = db.query(WorkflowOperation).filter(WorkflowOperation.run_id == run.id, WorkflowOperation.status != 'WAITING',
        WorkflowOperation.operation_type.in_(('PREPARE', 'DECIDE', 'BULK_DECISIONS', 'CLUSTER', 'CLUSTER_PREVIEW', 'GOLDEN_RESOLVE', 'ASSESS_PAIRS', 'REBUILD_CANDIDATES', 'PAIR_ADVICE', 'GROUP_ANNOTATIONS', 'AUTOMATION_SHADOW', 'AUTOMATION_APPLY', 'FINALIZE_GOLDEN'))
        ).order_by(WorkflowOperation.created_at.desc(), WorkflowOperation.id.desc()).first()
    states = {'PREPARE': ('DATA_PREPARING',), 'DECIDE': ('DECIDE_RUNNING',),
              'BULK_DECISIONS': ('DECIDE_REVIEW',), 'CLUSTER': ('CLUSTER_QUEUED', 'CLUSTER_RUNNING'),
              'GOLDEN_RESOLVE': ('DECIDE_REVIEW', 'GOLDEN_REVIEW'),
              'ASSESS_PAIRS': ('DECIDE_REVIEW',), 'REBUILD_CANDIDATES': ('DECIDE_REVIEW',), 'PAIR_ADVICE': ('DECIDE_REVIEW',), 'CLUSTER_PREVIEW': ('DECIDE_REVIEW',)}
    states.update(AUTOMATION_SHADOW=('DECIDE_REVIEW',),AUTOMATION_APPLY=('DECIDE_REVIEW',))
    states['GROUP_ANNOTATIONS'] = ('DECIDE_REVIEW', 'GOLDEN_REVIEW')
    states['FINALIZE_GOLDEN'] = ('GOLDEN_REVIEW',)
    if (latest and latest.status in ('FAILED', 'RETRYABLE_FAILED', 'CANCELLED')
            and run.workflow_state in states[latest.operation_type]):
        return latest
    return None


def replay(db, run_id, kind, request_id, payload):
    row = db.query(WorkflowOperation).filter_by(run_id=run_id, request_id=request_id).one_or_none()
    if row is not None:
        matches = row.payload_hash == digest(payload)
        historical_payload = {k: v for k, v in row.payload.items() if k not in
            ('matching_binding_id', 'matching_binding_revision', 'matching_profile_digest')}
        if not matches and kind in ('PAIR_DECISION', 'BULK_DECISIONS') and row.payload_hash == digest(historical_payload):
            # An omitted optional reason and an explicit null have identical semantics.
            def normalize(p):
                return {k: v for k, v in p.items() if k not in
                        ('reason', 'review_group_token', 'review_group_record_ids') or v is not None}
            matches = normalize(historical_payload) == normalize(payload)
        if row.operation_type != kind or not matches:
            conflict('REQUEST_ID_PAYLOAD_MISMATCH')
    return row


def check_revision(run, payload):
    if payload.get('expected_assessment_id') is not None:
        from sqlalchemy.orm import object_session

        from masterone.mdm.workflow_assessment_reads import active_assessment
        current = active_assessment(object_session(run), run.id)
        if current is None or current.id != payload['expected_assessment_id']:
            conflict('STALE_ASSESSMENT')
    for key, revision_column in (('expected_workflow_revision', 'workflow_revision'),
                        ('expected_decision_revision', 'decision_revision'),
                        ('expected_cluster_revision', 'cluster_revision'),
                        ('expected_golden_revision', 'golden_revision')):
        if key in payload and payload[key] != getattr(run, revision_column):
            conflict('STALE_REVISION', revision=revision_column, current=getattr(run, revision_column))


def audit(db, run, actor, action, details):
    db.add(ResolutionAuditEvent(run_id=run.id, actor_user_id=actor, action=action, details=details))


def new_operation(db, run, kind, request_id, payload, actor):
    from masterone.mdm.matching_foundation_service import pin_operation_binding
    pinned_payload = pin_operation_binding(db, run, payload)
    if kind == 'ASSESS_PAIRS':
        from masterone.mdm.workflow_assessment_reads import active_assessment
        assessment = active_assessment(db, run.id)
        if assessment and assessment.configuration.get('mvp_step2'):
            pinned_payload = {**pinned_payload, 'mvp_step2': True,
                'mvp_contract_version': assessment.configuration['mvp_contract_version'],
                'mvp_coordinator': assessment.configuration['coordinator']}
    op = WorkflowOperation(id=str(uuid4()), run_id=run.id, request_id=request_id,
        operation_type=kind, payload_hash=digest(payload), payload=pinned_payload, actor_user_id=actor)
    db.add(op)
    db.flush()
    return op


def enqueue(db, run, op):
    task = db.query(ResolutionTask).filter_by(run_id=run.id).one_or_none()
    if task is not None and task.status in ACTIVE:
        conflict('ACTIVE_OPERATION')
    if task is None:
        task = ResolutionTask(run_id=run.id)
        db.add(task)
    task.operation_id = op.id
    task.status, task.phase = 'QUEUED', op.operation_type.lower()
    task.attempt_id, task.attempt_count = None, 0
    task.cancel_requested, task.error_code = False, None
    task.processed_count, task.total_count = 0, 0
    run.status, run.phase = 'QUEUED', task.phase


def begin_operation(db, run_id, operation_type, request_id, payload, expected_workflow_revision, actor):
    run = enabled_run(db, run_id, actor)
    prior = replay(db, run_id, operation_type, request_id, payload)
    if prior:
        return operation_read(prior)
    if get_settings().ai_api_only and payload.get('model_scope') is not None:
        conflict('API_ONLY_LOCAL_MODEL_DENIED')
    check_revision(run, {**payload, 'expected_workflow_revision': expected_workflow_revision})
    if active_operation(db, run_id):
        conflict('ACTIVE_OPERATION')
    if operation_type == 'GROUP_ANNOTATIONS':
        from masterone.mdm.workflow_cluster_reads import current_generation
        current_generation(db, run)
        if not get_settings().llm_enabled or not get_settings().semantic_address_enabled:
            conflict('GROUP_ANNOTATIONS_DISABLED')
    if operation_type == 'BULK_DECISIONS':
        from masterone.mdm.workflow_bulk import validate_soft_group
        from masterone.mdm.workflow_pair_reads import validate_bulk_preview
        validate_soft_group(db, run, payload)
        validate_bulk_preview(db, run, payload)
    try:
        state = transition(run.workflow_state, operation_type)
    except ValueError:
        conflict('WORKFLOW_INVALID_STATE')
    op = new_operation(db, run, operation_type, request_id, payload, actor)
    enqueue(db, run, op)
    run.workflow_state = state
    run.workflow_revision += 1
    audit(db, run, actor, 'WORKFLOW_' + operation_type, {'operation_id': op.id})
    db.commit()
    return operation_read(op)


def initialize_workflow(db, run):
    if run.workflow_version == 2:
        run.workflow_state = 'DATA_PREPARING'
        op = new_operation(db, run, 'PREPARE', 'prepare:' + run.id, {}, run.owner_user_id)
        from masterone.mdm.matching_foundation_service import bind_new_run
        bind_new_run(db, run, op)
        enqueue(db, run, op)


def completion_blockers(db, run_id, *, run=None, operation=_UNSET, pending=None, conflicts=None):
    run = run if run is not None else db.get(ResolutionRun, run_id)
    blockers = []
    if run.archived_at is not None:
        blockers.append({'code': 'RUN_ARCHIVED'})
    if not get_settings().session_workflow_enabled:
        blockers.append({'code': 'SESSION_WORKFLOW_DISABLED'})
    if run.workflow_state != 'DECIDE_REVIEW':
        blockers.append({'code': 'DECIDE_NOT_READY'})
    if (active_operation(db, run_id) if operation is _UNSET else operation):
        blockers.append({'code': 'ACTIVE_OPERATION'})
    if not run.record_count:
        blockers.append({'code': 'NO_VALID_RECORDS'})
    if pending is None:
        pending = db.query(func.count(WorkflowPair.id)).filter(WorkflowPair.run_id == run_id, pending_predicate()).scalar()
    if pending:
        blockers.append({'code': 'PENDING_DECISIONS', 'count': pending, 'unit': 'pairs'})
    count = conflicts if conflicts is not None else db.query(func.count(WorkflowConflict.id)).filter_by(run_id=run_id, resolved=False).scalar()
    if count:
        blockers.append({'code': 'CLUSTER_CONFLICT', 'count': count, 'unit': 'pairs'})
    return blockers


def get_workflow(db, run_id, actor, *, cache_reads=False):
    from masterone.mdm.workflow_assessment_reads import effective_column
    run = owned_run(db, run_id, actor)
    pair_query = db.query(WorkflowPair).filter(WorkflowPair.run_id == run_id)
    baseline, triage = effective_column()['decision'].as_string(), effective_column()['triage_status'].as_string()
    if db.bind.dialect.name == 'postgresql':
        fields = func.json_to_record(effective_column()).table_valued(
            column('decision', String), column('triage_status', String)
        ).render_derived(with_types=True).lateral('status_evaluation')
        pair_query = pair_query.join(fields, true())
        baseline, triage = fields.c.decision, fields.c.triage_status
    def read_counts():
        return list(pair_query.with_entities(func.count(WorkflowPair.id),
            func.count(WorkflowPair.id).filter(pending_predicate(baseline=baseline, triage=triage)),
            func.count(WorkflowPair.id).filter(baseline == 'INSUFFICIENT_EVIDENCE')).one())
    conflicts = db.query(func.count(WorkflowConflict.id)).filter_by(run_id=run_id, resolved=False).scalar()
    latest_row = aliased(WorkflowOperation)
    latest_id = select(latest_row.id).where(latest_row.run_id == run_id, latest_row.status != 'WAITING',
        latest_row.operation_type.in_(('PREPARE', 'DECIDE', 'BULK_DECISIONS', 'CLUSTER', 'CLUSTER_PREVIEW', 'GOLDEN_RESOLVE', 'ASSESS_PAIRS', 'REBUILD_CANDIDATES', 'PAIR_ADVICE', 'GROUP_ANNOTATIONS', 'AUTOMATION_SHADOW', 'AUTOMATION_APPLY', 'FINALIZE_GOLDEN'))
        ).order_by(latest_row.created_at.desc(), latest_row.id.desc()).limit(1).scalar_subquery()
    cluster_id = select(latest_row.id).where(latest_row.run_id == run_id, latest_row.operation_type == 'CLUSTER'
        ).order_by(latest_row.created_at.desc(), latest_row.id.desc()).limit(1).scalar_subquery()
    advice_id = select(latest_row.id).where(latest_row.run_id == run_id, latest_row.operation_type == 'PAIR_ADVICE',
        latest_row.status == 'COMPLETED').order_by(latest_row.created_at.desc(), latest_row.id.desc()).limit(1).scalar_subquery()
    annotation_id = select(latest_row.id).where(latest_row.run_id == run_id, latest_row.operation_type == 'GROUP_ANNOTATIONS',
        latest_row.status == 'COMPLETED').order_by(latest_row.created_at.desc(), latest_row.id.desc()).limit(1).scalar_subquery()
    automation_id = select(latest_row.id).where(latest_row.run_id==run_id,
        latest_row.operation_type.in_(('AUTOMATION_SHADOW','AUTOMATION_APPLY'))).order_by(
        latest_row.created_at.desc(),latest_row.id.desc()).limit(1).scalar_subquery()
    stop_id = select(latest_row.id).where(latest_row.run_id==run_id,latest_row.operation_type=='AUTOMATION_STOP',
        latest_row.status=='COMPLETED').limit(1).scalar_subquery()
    agents_id = select(latest_row.id).where(latest_row.run_id == run_id,
        latest_row.operation_type.in_(('DECIDE', 'ASSESS_PAIRS', 'REBUILD_CANDIDATES')),
        latest_row.status.in_(('COMPLETED', 'DEGRADED'))).order_by(
            latest_row.created_at.desc(), latest_row.id.desc()).limit(1).scalar_subquery()
    operations = db.query(WorkflowOperation).filter(WorkflowOperation.run_id == run_id,
        or_(WorkflowOperation.status.in_(ACTIVE), WorkflowOperation.id == latest_id,
            WorkflowOperation.id == cluster_id, WorkflowOperation.id == advice_id, WorkflowOperation.id == annotation_id,
            WorkflowOperation.id == automation_id, WorkflowOperation.id == stop_id, WorkflowOperation.id == agents_id)
        ).order_by(WorkflowOperation.created_at.desc(), WorkflowOperation.id.desc()).all()
    op = next((o for o in operations if o.status in ACTIVE), None)
    if cache_reads:
        from masterone.mdm.pair_read_cache import cached_read
        pair_count, pending, insufficient = cached_read(db, run, 'status-counts', {}, read_counts,
                                                      operation_active=op is not None)
    else:
        pair_count, pending, insufficient = read_counts()
    latest = next((o for o in operations if o is not op and o.status != 'WAITING'), None)
    retryable = retryable_operation(db, run, latest) if not op else None
    blockers = []
    if run.workflow_version == 2:
        if run.workflow_state in ('GOLDEN_REVIEW', 'FINALIZED'):
            if run.workflow_state == 'GOLDEN_REVIEW':
                from masterone.mdm.workflow_generation_reads import GenerationReader
                from masterone.mdm.workflow_models import WorkflowGeneration
                generation = db.query(WorkflowGeneration).filter_by(run_id=run_id, revision=run.cluster_revision).one_or_none()
                missing = GenerationReader(db, run, generation).unconfirmed_count() if generation else 0
                if generation is None:
                    blockers.append({'code': 'CLUSTERS_NOT_READY'})
                elif missing:
                    blockers.append({'code': 'UNCONFIRMED_GOLDEN', 'count': missing, 'unit': 'clusters'})
        else:
            blockers = completion_blockers(db, run_id, run=run, operation=op, pending=pending, conflicts=conflicts)
    actions = []
    from masterone.mdm.workflow_cluster_reads import generation_metadata
    cluster_result = generation_metadata(db, run) if run.cluster_revision else None
    if cluster_result and cluster_result['stale'] and run.workflow_state == 'GOLDEN_REVIEW':
        blockers.append({'code': 'CLUSTERS_NOT_READY'})
    if run.workflow_version == 2 and get_settings().session_workflow_enabled and not op and not run.archived_at:
        if run.workflow_state == 'DATA_READY':
            actions = ['start-decide'] if run.record_count else []
        elif run.workflow_state == 'DECIDE_REVIEW':
            actions = ['decide-pair', 'amend-pair', 'bulk-decisions', 'reassess-pairs', 'assess-with-ai',
                       'approve-group', 'preview-clusters'] + ([] if blockers else ['finalize-decisions'])
            if not any(b['code'] not in ('PENDING_DECISIONS', 'CLUSTER_CONFLICT') for b in blockers):
                actions += ['finalize-partial']
            if cluster_result and not cluster_result['read_only']:
                actions += ['save-golden', 'auto-select', 'confirm-golden', 'resolve-golden']
        elif run.workflow_state == 'GOLDEN_REVIEW':
            actions = ['reopen-decisions','decide-pair','bulk-decisions','amend-pair','approve-group']
            if cluster_result and not cluster_result['stale']:
                actions += ['save-golden', 'auto-select', 'confirm-golden', 'resolve-golden'] + ([] if blockers else ['finalize-golden'])
        elif run.workflow_state == 'FINALIZED':
            actions = ['export', 'reopen-decisions']
        if run.workflow_state in ('DECIDE_REVIEW','GOLDEN_REVIEW'):
            actions.append('build-cluster-queue')
        if retryable:
            actions.append('retry-operation')
    if (actions and not op and cluster_result and not cluster_result['stale'] and
            run.workflow_state in ('DECIDE_REVIEW', 'GOLDEN_REVIEW') and
            get_settings().llm_enabled and get_settings().semantic_address_enabled):
        actions.append('annotate-groups')
    if (run.workflow_version == 2 and get_settings().session_workflow_enabled and not run.archived_at
            and op and (run.workflow_state == 'DECIDE_REVIEW' and op.operation_type == 'BULK_DECISIONS'
                or op.operation_type == 'CLUSTER' and op.payload.get('automatic_cluster_refresh'))):
        # Permission to collect UI intentions, not permission for concurrent database writes.
        actions = ['queue-decisions']
    if (run.workflow_version == 2 and get_settings().session_workflow_enabled and not run.archived_at
            and run.workflow_state == 'DECIDE_REVIEW' and op and op.operation_type == 'CLUSTER_PREVIEW'):
        actions = ['decide-pair', 'amend-pair', 'approve-group']
    from masterone.mdm.workflow_assessment_reads import active_assessment
    assessment = active_assessment(db, run_id)
    automation_stopped=any(o.operation_type=='AUTOMATION_STOP' for o in operations)
    agents_op = next((o for o in operations if o.operation_type in ('DECIDE', 'ASSESS_PAIRS', 'REBUILD_CANDIDATES')
                      and o.status in ('COMPLETED', 'DEGRADED')), None)
    matching_agents = agents_op.result.get('matching_agents') if agents_op else None
    if ('decide-pair' in actions and assessment and not matching_agents):
        actions.append('automation-shadow')
        if not automation_stopped:
            actions.append('automation-stop')
            if get_settings().pair_automation_enabled:
                actions.append('automation-apply')
    if op and op.operation_type in {'AUTOMATION_SHADOW','AUTOMATION_APPLY'} and not automation_stopped and not run.archived_at:
        actions.append('automation-stop')
    latest_advice = next((o for o in operations if o.operation_type == 'PAIR_ADVICE' and o.status == 'COMPLETED'), None)
    latest_annotations = next((o for o in operations if o.operation_type == 'GROUP_ANNOTATIONS' and o.status == 'COMPLETED'), None)
    latest_cluster = next((o for o in operations if o.operation_type == 'CLUSTER'), None)
    active_read = operation_read(op, summary=True) if op else None
    if active_read and op.operation_type == 'DECIDE':
        from masterone.mdm.decision_progress import operation_progress
        active_read['progress'] = operation_progress(db, op)
    return dict(run_id=run_id, workflow_version=run.workflow_version, assessment_id=assessment.id if assessment else None,
        automation_stopped=automation_stopped, matching_agents=matching_agents,
        latest_automation_operation=operation_read(next((o for o in operations if o.operation_type in {'AUTOMATION_SHADOW','AUTOMATION_APPLY'}),None)) if any(o.operation_type in {'AUTOMATION_SHADOW','AUTOMATION_APPLY'} for o in operations) else None,
        advice_revision=latest_advice.id if latest_advice else None,
        annotation_revision=latest_annotations.id if latest_annotations else None,
        workflow_state=run.workflow_state if run.workflow_version == 2 else 'LEGACY_READ_ONLY',
        **{k: getattr(run, k) for k in ('workflow_revision', 'decision_revision', 'cluster_revision', 'golden_revision')},
        counts={'records': {'value': run.record_count, 'unit': 'records'},
                'candidates': {'value': run.candidate_count, 'unit': 'pairs'},
                'pairs': {'value': pair_count, 'unit': 'pairs'},
                'review_required': {'value': pending, 'unit': 'pairs'},
                'insufficient': {'value': insufficient if run.workflow_version == 2 else run.insufficient_count, 'unit': 'pairs'}},
        cluster_result=cluster_result, latest_cluster_operation=operation_read(latest_cluster, summary=True) if latest_cluster else None,
        allowed_actions=actions, blockers=blockers, active_operation=active_read,
        retryable_operation=operation_read(retryable) if retryable else None)


def require_legacy(db, run_id):
    run = db.get(ResolutionRun, run_id)
    if run is not None and run.workflow_version == 2:
        conflict('USE_WORKFLOW_API')


def validate_version(version):
    if version not in (1, 2):
        raise HTTPException(422, 'workflow_version must be 1 or 2')
    if version == 2 and not get_settings().session_workflow_enabled:
        conflict('SESSION_WORKFLOW_DISABLED')


def retry_operation(db, run_id, operation_id, request, actor):
    payload = {**request.model_dump(), 'operation_id': operation_id}
    run = enabled_run(db, run_id, actor)
    prior = replay(db, run_id, 'RETRY_OPERATION', request.request_id, payload)
    if prior:
        return prior.result
    check_revision(run, payload)
    op = db.query(WorkflowOperation).filter_by(id=operation_id, run_id=run_id).one_or_none()
    if op is None:
        raise HTTPException(404, 'Operation not found')
    if op.operation_type == 'AUTOMATION_APPLY':
        from masterone.mdm.workflow_automation import stopped
        if stopped(db,run_id):
            conflict('AUTOMATION_STOPPED')
    if retryable_operation(db, run) is not op or active_operation(db, run_id):
        conflict('OPERATION_NOT_RETRYABLE')
    command = new_operation(db, run, 'RETRY_OPERATION', request.request_id, payload, actor)
    op.status, op.error_code, op.stage = 'QUEUED', None, 'queued'
    enqueue(db, run, op)
    run.workflow_revision += 1
    if op.operation_type == 'CLUSTER':
        run.workflow_state = 'CLUSTER_QUEUED'
    command.status, command.stage, command.result = 'COMPLETED', 'completed', operation_read(op)
    audit(db, run, actor, 'WORKFLOW_RETRY_OPERATION', {'operation_id': op.id})
    db.commit()
    return command.result
