"""Attempt-local telemetry; never changes matching inputs, scores or pinned pipeline code."""
from contextvars import ContextVar
from contextlib import contextmanager
from time import monotonic

from masterone.mdm.pipeline.scoring_engine import ScoringEngine

_current = ContextVar('decision_progress', default=None)


@contextmanager
def track_decision(callback):
    token = _current.set({'callback': callback, 'last': 0.0, 'count': -1})
    try:
        yield
    finally:
        _current.reset(token)


def candidate_progress(count, *, force=False):
    state = _current.get()
    if state is None or count == state['count']:
        return
    now = monotonic()
    if force or now - state['last'] >= .4:
        state['callback']('candidate-generation', count, 0)
        state.update(last=now, count=count)


class ProgressScoringEngine(ScoringEngine):
    def _scored_batches(self, iterator, provider, expected_count):
        state = _current.get()
        count, last = 0, 0.0
        if state:
            state['callback']('pair-classification', 0, expected_count)
        for batch, scored in super()._scored_batches(iterator, provider, expected_count):
            yield batch, scored
            # The consumer validated and wrote this batch before resuming us.
            count += len(scored)
            now = monotonic()
            if state and (now - last >= .4 or count == expected_count):
                state['callback']('pair-classification', count, expected_count)
                last = now


def operation_progress(db, op):
    """Read-only compatibility for a DECIDE attempt started before telemetry rollout."""
    if op is None or op.operation_type != 'DECIDE' or 'classification_started' in op.progress:
        return op.progress if op else {}
    from masterone.mdm.workflow_models import WorkflowArtifact
    artifact = db.query(WorkflowArtifact.payload['candidate_count'].as_integer()).filter_by(
        run_id=op.run_id, phase='DECIDE').filter(WorkflowArtifact.created_at >= op.created_at).order_by(
            WorkflowArtifact.created_at.desc()).first()
    if artifact and artifact[0] is not None:
        count = artifact[0]
        return {**op.progress, 'candidate_count': count, 'classified_count': count, 'classification_started': True}
    return op.progress
