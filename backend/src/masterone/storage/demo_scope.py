"""Request/job local routing: demo artifacts never enter object storage."""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

_local = ContextVar('demo_local_storage', default=False)


def local_only():
    return _local.get()


@contextmanager
def local_storage(enabled=True):
    token = _local.set(enabled)
    try:
        yield
    finally:
        _local.reset(token)


def is_demo_run(db, run_id):
    from masterone.auth.models import DemoGrant
    return db.query(DemoGrant.user_id).filter_by(run_id=run_id).first() is not None


def demo_job(function):
    @wraps(function)
    def wrapped(operation_id, factory, progress):
        from masterone.mdm.workflow_models import WorkflowOperation
        with factory() as db:
            op = db.get(WorkflowOperation, operation_id)
            demo = bool(op and is_demo_run(db, op.run_id))
        with local_storage(demo):
            result = function(operation_id, factory, progress)
            if demo:
                from masterone.mdm.demo_embeddings import warm
                warm(factory, operation_id)
            return result
    return wrapped
