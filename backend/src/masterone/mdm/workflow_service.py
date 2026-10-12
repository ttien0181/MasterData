"""Workflow commands serialize on the engine's shared run lock."""
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import aliased

from masterone.config import get_settings
from masterone.mdm.generation_staging import digest, lock_run
from masterone.mdm.models import ResolutionAuditEvent, ResolutionRun, ResolutionTask
from masterone.mdm.ownership import owned_run
from masterone.mdm.workflow_contracts import transition
from masterone.mdm.workflow_models import WorkflowConflict, WorkflowOperation, WorkflowPair

ACTIVE = ('QUEUED', 'RUNNING')
_UNSET = object()


def conflict(code, **details):
    raise HTTPException(409, {'code': code, **details})


def operation_read(op):
    return dict(operation_id=op.id, run_id=op.run_id, operation_type=op.operation_type,
                status=op.status, stage=op.stage, progress=op.progress,
                error_code=op.error_code, result=op.result)


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


def retryable_operation(db, run, latest=_UNSET):
    if latest is _UNSET:
        latest = db.query(WorkflowOperation).filter(WorkflowOperation.run_id == run.id,
        WorkflowOperation.operation_type.in_(('PREPARE', 'DECIDE', 'BULK_DECISIONS', 'CLUSTER', 'GOLDEN_RESOLVE', 'ASSESS_PAIRS', 'PAIR_ADVICE'))
        ).order_by(WorkflowOperation.created_at.desc(), WorkflowOperation.id.desc()).first()
    states = {'PREPARE': ('DATA_PREPARING',), 'DECIDE': ('DECIDE_RUNNING',),
              'BULK_DECISIONS': ('DECIDE_REVIEW',), 'CLUSTER': ('CLUSTER_QUEUED', 'CLUSTER_RUNNING'),
              'GOLDEN_RESOLVE': ('DECIDE_REVIEW', 'GOLDEN_REVIEW'),
              'ASSESS_PAIRS': ('DECIDE_REVIEW',), 'PAIR_ADVICE': ('DECIDE_REVIEW',)}
    if (latest and latest.status in ('FAILED', 'RETRYABLE_FAILED', 'CANCELLED')
            and run.workflow_state in states[latest.operation_type]):
        return latest
    return None


def replay(db, run_id, kind, request_id, payload):
    row = db.query(WorkflowOperation).filter_by(run_id=run_id, request_id=request_id).one_or_none()
    if row is not None and (row.operation_type != kind or row.payload_hash != digest(payload)):
        conflict('REQUEST_ID_PAYLOAD_MISMATCH')
    return row


def check_revision(run, payload):
    if payload.get('expected_assessment_id') is not None:
        from sqlalchemy.orm import object_session
        from masterone.mdm.workflow_assessment_reads import active_assessment
        current = active_assessment(object_session(run), run.id)
        if current is None or current.id != payload['expected_assessment_id']:
            conflict('STALE_ASSESSMENT')
    for key, column in (('expected_workflow_revision', 'workflow_revision'),
                        ('expected_decision_revision', 'decision_revision'),
                        ('expected_cluster_revision', 'cluster_revision'),
                        ('expected_golden_revision', 'golden_revision')):
        if key in payload and payload[key] != getattr(run, column):
            conflict('STALE_REVISION', revision=column, current=getattr(run, column))


def audit(db, run, actor, action, details):
    db.add(ResolutionAuditEvent(run_id=run.id, actor_user_id=actor, action=action, details=details))


def new_operation(db, run, kind, request_id, payload, actor):
    op = WorkflowOperation(id=str(uuid4()), run_id=run.id, request_id=request_id,
        operation_type=kind, payload_hash=digest(payload), payload=payload, actor_user_id=actor)
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
    check_revision(run, {**payload, 'expected_workflow_revision': expected_workflow_revision})
    if active_operation(db, run_id):
        conflict('ACTIVE_OPERATION')
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
        enqueue(db, run, op)


def pending_predicate():
    from masterone.mdm.workflow_assessment_reads import effective_column
    baseline = effective_column()['decision'].as_string()
    return or_(WorkflowPair.decision == 'DEFERRED', and_(WorkflowPair.decision == 'UNDECIDED',
        or_(baseline.is_(None), baseline.notin_(('MERGEABLE', 'NON_MATCH', 'NOT_MERGEABLE', 'BLOCKED')))))


def completion_blockers(db, run_id, *, run=None, operation=_UNSET, pending=None, conflicts=None):
    run = run if run is not None else db.get(ResolutionRun, run_id)
    blockers = []
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


def get_workflow(db, run_id, actor):
    run = owned_run(db, run_id, actor)
    conflict_count = select(func.count(WorkflowConflict.id)).where(WorkflowConflict.run_id == run_id,
                        WorkflowConflict.resolved.is_(False)).scalar_subquery()
    pair_count, pending, conflicts = db.query(func.count(WorkflowPair.id),
        func.count(WorkflowPair.id).filter(pending_predicate()), conflict_count).filter(WorkflowPair.run_id == run_id).one()
    latest_row = aliased(WorkflowOperation)
    latest_id = select(latest_row.id).where(latest_row.run_id == run_id,
        latest_row.operation_type.in_(('PREPARE', 'DECIDE', 'BULK_DECISIONS', 'CLUSTER', 'GOLDEN_RESOLVE', 'ASSESS_PAIRS', 'PAIR_ADVICE'))
        ).order_by(latest_row.created_at.desc(), latest_row.id.desc()).limit(1).scalar_subquery()
    operations = db.query(WorkflowOperation).filter(WorkflowOperation.run_id == run_id,
        or_(WorkflowOperation.status.in_(ACTIVE), WorkflowOperation.id == latest_id)).all()
    op = next((o for o in operations if o.status in ACTIVE), None)
    latest = next((o for o in operations if o is not op), None)
    retryable = retryable_operation(db, run, latest) if not op else None
    blockers = []
    if run.workflow_version == 2:
        if run.workflow_state in ('GOLDEN_REVIEW', 'FINALIZED'):
            if run.workflow_state == 'GOLDEN_REVIEW':
                from masterone.mdm.workflow_models import WorkflowCluster, WorkflowGeneration
                generation = db.query(WorkflowGeneration).filter_by(run_id=run_id, revision=run.cluster_revision).one_or_none()
                missing = db.query(WorkflowCluster).filter_by(generation_id=generation.id, confirmed=False).count() if generation else 0
                if generation is None:
                    blockers.append({'code': 'CLUSTERS_NOT_READY'})
                elif missing:
                    blockers.append({'code': 'UNCONFIRMED_GOLDEN', 'count': missing, 'unit': 'clusters'})
        else:
            blockers = completion_blockers(db, run_id, run=run, operation=op, pending=pending, conflicts=conflicts)
    actions = []
    if run.workflow_version == 2 and get_settings().session_workflow_enabled and not op and not run.archived_at:
        if run.workflow_state == 'DATA_READY':
            actions = ['start-decide'] if run.record_count else []
        elif run.workflow_state == 'DECIDE_REVIEW':
            actions = ['decide-pair', 'bulk-decisions', 'reassess-pairs', 'assess-with-ai'] + ([] if blockers else ['finalize-decisions'])
            if run.cluster_revision:
                actions += ['save-golden', 'auto-select', 'confirm-golden', 'resolve-golden']
        elif run.workflow_state == 'GOLDEN_REVIEW':
            actions = ['save-golden', 'auto-select', 'confirm-golden', 'resolve-golden', 'reopen-decisions'] + ([] if blockers else ['finalize-golden'])
        elif run.workflow_state == 'FINALIZED':
            actions = ['export', 'reopen-decisions']
        if retryable:
            actions.append('retry-operation')
    if (run.workflow_version == 2 and get_settings().session_workflow_enabled and not run.archived_at
            and run.workflow_state == 'DECIDE_REVIEW' and op and op.operation_type == 'BULK_DECISIONS'):
        # Permission to collect UI intentions, not permission for concurrent database writes.
        actions = ['queue-decisions']
    from masterone.mdm.workflow_assessment_reads import active_assessment
    assessment = active_assessment(db, run_id)
    latest_advice = db.query(WorkflowOperation.id).filter_by(run_id=run_id, operation_type='PAIR_ADVICE', status='COMPLETED').order_by(WorkflowOperation.created_at.desc()).first()
    return dict(run_id=run_id, workflow_version=run.workflow_version, assessment_id=assessment.id if assessment else None,
        advice_revision=latest_advice[0] if latest_advice else None,
        workflow_state=run.workflow_state if run.workflow_version == 2 else 'LEGACY_READ_ONLY',
        **{k: getattr(run, k) for k in ('workflow_revision', 'decision_revision', 'cluster_revision', 'golden_revision')},
        counts={'records': {'value': run.record_count, 'unit': 'records'},
                'candidates': {'value': run.candidate_count, 'unit': 'pairs'},
                'pairs': {'value': pair_count, 'unit': 'pairs'},
                'insufficient': {'value': run.insufficient_count, 'unit': 'pairs'}},
        allowed_actions=actions, blockers=blockers, active_operation=operation_read(op) if op else None,
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
