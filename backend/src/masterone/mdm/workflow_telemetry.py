"""Run-scoped numeric telemetry. Never returns records, prompts or credentials."""
import time
from collections import defaultdict
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from functools import wraps

_timer = ContextVar('workflow_stage_timer', default=None)


class StageTimer:
    def __init__(self, clock=time.perf_counter):
        self.clock = clock
        self.started = self.changed = clock()
        self.stage = 'worker-setup'
        self.stages = defaultdict(float)
        self.pinned = 0

    def progress(self, stage):
        if stage != self.stage:
            now = self.clock()
            self.stages[self.stage] += now - self.changed
            self.changed, self.stage = now, stage

    def snapshot(self):
        now = self.clock()
        stages = dict(self.stages)
        stages[self.stage] = stages.get(self.stage, 0.) + now - self.changed
        return {'total_seconds': now - self.started, 'stages': stages}


def measured_operation(function):
    @wraps(function)
    def wrapped(operation_id, factory, progress):
        from masterone.mdm.job_queue import current_claim, utc
        from masterone.mdm.models import ResolutionJobAttempt, ResolutionTask
        from masterone.mdm.workflow_models import WorkflowOperation
        timer = StageTimer()
        claim = current_claim()
        started_at = datetime.now(UTC)
        with factory() as db:
            op = db.get(WorkflowOperation, operation_id)
            retries = db.query(WorkflowOperation.id).filter(
                WorkflowOperation.run_id == op.run_id, WorkflowOperation.operation_type == 'RETRY_OPERATION',
                WorkflowOperation.payload['operation_id'].as_string() == op.id).count()
            attempt = db.get(ResolutionJobAttempt, claim.attempt_id) if claim else None
            task = db.get(ResolutionTask, claim.task_id) if claim else None
            retry = bool(retries or task and task.attempt_count > 1)
            claimed_at = utc(attempt.started_at) if attempt else started_at
            timer.metadata = {'attempt_id': claim.attempt_id if claim else operation_id,
                'started_at': started_at.isoformat(),
                'queue_wait_seconds': None if retry else max(0., (claimed_at - utc(op.created_at)).total_seconds()),
                'operation_age_at_claim_seconds': max(0., (claimed_at - utc(op.created_at)).total_seconds()),
                'worker_startup_seconds': max(0., (started_at - claimed_at).total_seconds())}
        token = _timer.set(timer)
        def report(phase, done, total):
            if not timer.pinned:
                timer.progress(phase)
            return progress(phase, done, total)
        try:
            return function(operation_id, factory, report)
        except Exception:
            # Preserve numeric partial timings if we still own the lease. A killed
            # process cannot run this block; absence is explicitly reported below.
            from masterone.mdm.job_queue import OwnershipLostError, guard
            try:
                with factory.begin() as db:
                    op = db.get(WorkflowOperation, operation_id)
                    guard(db, op.run_id)
                    record_timing(db, op, 'FAILED')
            except OwnershipLostError:
                pass
            raise
        finally:
            _timer.reset(token)
    return wrapped


@contextmanager
def timed_stage(name):
    """Explicit phase boundaries win over progress emitted at batch completion."""
    timer = _timer.get()
    if timer is None:
        yield
        return
    previous = timer.stage
    timer.progress(name)
    timer.pinned += 1
    try:
        yield
    finally:
        timer.progress(previous)
        timer.pinned -= 1


def record_timing(db, op, status='COMPLETED'):
    timer = _timer.get()
    if timer is None:
        return
    from masterone.mdm.generation_staging import digest
    from masterone.mdm.workflow_models import WorkflowArtifact
    row = {**timer.metadata, **timer.snapshot(), 'status': status, 'operation_id': op.id}
    # Operation results participate in sealed preview/automation contracts. Keep
    # instrumentation separate so those exact integrity projections stay valid.
    db.add(WorkflowArtifact(run_id=op.run_id, phase='WORKFLOW_TIMING', content_hash=digest(row), payload=row))


def _embedding_totals():
    return {'http_attempts': 0, 'responses': 0, 'failed_responses': 0,
        'unresolved_intents': 0, 'known_input_tokens': 0, 'unknown_usage_attempts': 0,
        'known_api_cost_usd': 0., 'unknown_cost_attempts': 0,
        'known_estimated_cost_usd': 0., 'latency_seconds': 0.}


def _summarize_embedding(receipts, *, breakdown=False):
    """One row per physical HTTP intent, regardless of batch summary receipts."""
    intents, terminals = {}, {}
    for row in receipts:
        identifier = row.get('request_id')
        if identifier and row.get('event') == 'HTTP_INTENT':
            intents[identifier] = row
        elif identifier and row.get('event') in ('HTTP_RESPONSE', 'HTTP_FAILED'):
            terminals[identifier] = row
    groups = {} if breakdown else {'retrieval': _embedding_totals(), 'evidence': _embedding_totals()}
    for identifier, intent in intents.items():
        key = (tuple(intent.get(field, 'unknown') for field in ('position', 'provider', 'model', 'field'))
               if breakdown else intent.get('position', 'unknown'))
        group = groups.setdefault(key, _embedding_totals())
        row = terminals.get(identifier, {})
        group['http_attempts'] += 1
        group['responses'] += bool(row)
        group['failed_responses'] += row.get('event') == 'HTTP_FAILED'
        group['unresolved_intents'] += not bool(row)
        group['known_input_tokens'] += row.get('input_tokens') or 0
        group['unknown_usage_attempts'] += row.get('input_tokens') is None
        group['known_api_cost_usd'] += row.get('api_cost_usd') or 0.
        group['unknown_cost_attempts'] += row.get('api_cost_usd') is None
        group['known_estimated_cost_usd'] += row.get('estimated_cost_usd') or 0.
        group['latency_seconds'] += row.get('latency_seconds') or 0.
    for group in groups.values():
        group['input_tokens'] = None if group['unknown_usage_attempts'] else group['known_input_tokens']
        group['api_cost_usd'] = None if group['unknown_cost_attempts'] else group['known_api_cost_usd']
    return groups


def summarize_embedding(receipts):
    return _summarize_embedding(receipts)


def embedding_breakdown(receipts):
    return [dict(zip(('position', 'provider', 'model', 'field'), key), **totals)
            for key, totals in sorted(_summarize_embedding(receipts, breakdown=True).items())]


def usage_report(db, run_id):
    from masterone.config import get_settings
    from masterone.mdm.models import LLMRequest, LLMReservation, ResolutionJobAttempt
    from masterone.mdm.workflow_artifacts import load
    from masterone.mdm.workflow_models import WorkflowArtifact, WorkflowOperation
    operations = db.query(WorkflowOperation).filter_by(run_id=run_id).order_by(WorkflowOperation.created_at, WorkflowOperation.id).all()
    timings = defaultdict(dict)
    for artifact in db.query(WorkflowArtifact).filter_by(run_id=run_id, phase='WORKFLOW_TIMING').order_by(WorkflowArtifact.created_at, WorkflowArtifact.id):
        row = load(db, artifact.id, run_id, 'WORKFLOW_TIMING')
        timings[row['operation_id']][row['attempt_id']] = row
    receipts = [load(db, row.id, run_id, 'AGENT_ENCODER_USAGE') for row in
                db.query(WorkflowArtifact).filter_by(run_id=run_id, phase='AGENT_ENCODER_USAGE')]
    llm = {}
    for request, reservation in db.query(LLMRequest, LLMReservation).join(
            LLMReservation, LLMRequest.request_hash == LLMReservation.id).filter(LLMReservation.run_id == run_id):
        key = (request.request_type, request.provider, request.model)
        group = llm.setdefault(key, {'purpose': key[0], 'provider': key[1], 'model': key[2],
            'http_attempts': 0, 'successes': 0, 'known_input_tokens': 0, 'known_output_tokens': 0,
            'unknown_usage_attempts': 0, 'latency_seconds': 0., 'statuses': {}, 'logical_request_ids': set()})
        group['http_attempts'] += 1
        group['successes'] += request.status == 'SUCCESS'
        group['known_input_tokens'] += request.input_tokens or 0
        group['known_output_tokens'] += request.output_tokens or 0
        group['unknown_usage_attempts'] += request.input_tokens is None or request.output_tokens is None
        group['latency_seconds'] += (request.latency_ms or 0) / 1000
        group['statuses'][request.status] = group['statuses'].get(request.status, 0) + 1
        group['logical_request_ids'].add(reservation.id)
    for group in llm.values():
        group['logical_requests'] = len(group.pop('logical_request_ids'))
        group['input_tokens'] = None if group['unknown_usage_attempts'] else group['known_input_tokens']
        group['output_tokens'] = None if group['unknown_usage_attempts'] else group['known_output_tokens']
    local = [{'operation_id': op.id, 'model': 'splink', 'external_requests': 0,
              'status': op.result['model'].get('status'), 'scope': op.result['model'].get('scope'),
              'evaluated_pairs': sum(op.result.get('groups', {}).values())
                  if op.result['model'].get('status') == 'EVALUATED' else 0}
             for op in operations if op.result.get('model')]
    return {'run_id': run_id, 'schema_version': 'workflow_telemetry_v1',
        'operations': [{'operation_id': op.id, 'operation_type': op.operation_type, 'status': op.status,
                        'telemetry': {'attempts': list(timings[op.id].values()),
                            'timing_basis': 'Monotonic worker time through fenced publication; excludes final commit and supervisor startup.'}
                            if timings[op.id] else None, 'timing_available': bool(timings[op.id])}
                       for op in operations],
        'worker_attempts': [{'attempt_id': row.id, 'status': row.status,
            'started_at': row.started_at.isoformat(), 'finished_at': row.finished_at.isoformat() if row.finished_at else None}
            for row in db.query(ResolutionJobAttempt).filter_by(run_id=run_id).order_by(ResolutionJobAttempt.started_at)],
        'embedding_by_position': summarize_embedding(receipts), 'llm_by_purpose': list(llm.values()),
        'embedding_by_model_field': embedding_breakdown(receipts),
        'ai_execution_mode': 'api_only' if get_settings().ai_api_only else 'hybrid_local',
        'local_model_evaluations': local,
        'notes': ['HTTP intents are conservative attempts; an interrupted intent may not have reached the provider.',
                  'Zero external calls for Rule, Coordinator, Splink, clustering and Golden selection.',
                  'Provider cost and configured estimate are separate; unknown usage remains null.',
                  'Historical or forcibly terminated operations may have no stage timing; worker attempt timestamps remain available.',
                  'Retry queue time is unavailable without an enqueue timestamp; operation age at claim is reported separately.']}
