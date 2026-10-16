"""W1 publication-time resource gate and pinned scorer process count."""
import os
import time
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from pathlib import Path

_scope = ContextVar('matching_integration_budget', default=None)


def check_resources(config, *, elapsed, rss, output_bytes):
    if elapsed > config['execution_budget']['run_seconds']:
        raise ValueError('MATCHING_WORKER_TIMEOUT')
    if rss > config['resource_budget']['worker_rss_bytes']:
        raise ValueError('MATCHING_WORKER_RSS_BUDGET')
    if output_bytes > config['execution_budget']['disk_bytes']:
        raise ValueError('MATCHING_WORKER_DISK_BUDGET')


@contextmanager
def scoring_scope(config):
    previous = os.environ.get('MDM_SCORE_WORKERS')
    os.environ['MDM_SCORE_WORKERS'] = str(config['execution_budget']['rule_workers'])
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop('MDM_SCORE_WORKERS', None)
        else:
            os.environ['MDM_SCORE_WORKERS'] = previous


def check_publication(db):
    scope = _scope.get()
    if scope is None:
        return
    import psutil
    from sqlalchemy import String, cast, func

    from masterone.mdm.workflow_models import WorkflowArtifact
    config, started, run_id = scope
    process = psutil.Process()
    rss = 0
    for child in [process] + process.children(recursive=True):
        try:
            rss += child.memory_info().rss
        except psutil.NoSuchProcess:
            pass
    database = db.get_bind().url.database
    if db.get_bind().dialect.name == 'sqlite' and database and database != ':memory:':
        # Isolated QA SQLite: physical DB plus journal/WAL, including retained history.
        output_bytes = sum(p.stat().st_size for p in (Path(database), Path(database + '-wal'), Path(database + '-journal')) if p.exists())
    else:
        length = func.octet_length if db.get_bind().dialect.name == 'postgresql' else func.length
        output_bytes = db.query(func.sum(length(cast(WorkflowArtifact.payload, String)))).filter_by(run_id=run_id).scalar() or 0
    check_resources(config, elapsed=time.monotonic() - started, rss=rss, output_bytes=output_bytes)


def integration_job(function):
    @wraps(function)
    def wrapped(operation_id, factory, progress):
        from masterone.mdm.models import ResolutionRun
        from masterone.mdm.workflow_models import WorkflowOperation

        from .workflow_agents import config_for_operation
        with factory() as db:
            op = db.get(WorkflowOperation, operation_id)
            config = config_for_operation(db, db.get(ResolutionRun, op.run_id), op) if op and op.payload.get('matching_binding_id') else None
        if not config or config['kind'] != 'RULE_HYBRID_DETERMINISTIC':
            return function(operation_id, factory, progress)
        token = _scope.set((config, time.monotonic(), op.run_id))
        try:
            with scoring_scope(config):
                return function(operation_id, factory, progress)
        finally:
            _scope.reset(token)
    return wrapped
