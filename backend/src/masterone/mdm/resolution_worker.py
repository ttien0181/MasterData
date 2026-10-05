import threading

from sqlalchemy.orm import sessionmaker

from masterone.db.base import now_utc
from masterone.mdm.models import ResolutionRun, ResolutionTask
from masterone.mdm.resolution_service import mark_failed, run_pipeline


class ResolutionWorker:
    """Single-process local worker; queued tasks remain durable in SQLite."""

    def __init__(self, factory: sessionmaker):
        self.factory = factory
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._work, name="masterone-resolution", daemon=True)

    def start(self):
        with self.factory.begin() as db:
            interrupted = db.query(ResolutionTask).filter_by(status="RUNNING").all()
            for task in interrupted:
                task.status = "RETRYABLE_FAILED"
                task.phase = "interrupted"
                task.error_code = "WORKER_RESTARTED"
                task.updated_at = now_utc()
                run = db.get(ResolutionRun, task.run_id)
                if run is not None:
                    run.status = "RETRYABLE_FAILED"
                    run.phase = "interrupted"
        self.thread.start()

    def close(self):
        self.stop_event.set()
        self.thread.join(timeout=3)

    def _progress(self, run_id: str, phase: str, processed: int, total: int):
        with self.factory.begin() as db:
            task = db.query(ResolutionTask).filter_by(run_id=run_id).one()
            task.phase = phase
            task.processed_count = processed
            task.total_count = total
            task.updated_at = now_utc()
            run = db.get(ResolutionRun, run_id)
            if run is not None:
                run.phase = phase

    def _work(self):
        while not self.stop_event.is_set():
            with self.factory() as db:
                task = db.query(ResolutionTask).filter_by(status="QUEUED").order_by(
                    ResolutionTask.created_at, ResolutionTask.id).first()
                run_id = task.run_id if task else None
            if run_id is None:
                self.stop_event.wait(0.3)
                continue
            try:
                run_pipeline(run_id, self.factory,
                             lambda phase, done, total: self._progress(run_id, phase, done, total))
            except Exception:
                mark_failed(run_id, self.factory)
