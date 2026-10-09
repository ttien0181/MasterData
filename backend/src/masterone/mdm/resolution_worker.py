import logging
import threading

from sqlalchemy.orm import sessionmaker

from masterone.db.base import now_utc
from masterone.mdm.models import ResolutionRun, ResolutionTask
from masterone.mdm.resolution_service import mark_failed, run_pipeline


class ResolutionWorker:
    """Local worker pool; queued tasks remain durable in the database.

    Runs up to ``concurrency`` pipelines in parallel. Each thread atomically
    claims the oldest QUEUED task (QUEUED -> RUNNING under a shared lock) before
    executing, so two threads never pick up the same run.
    """

    def __init__(self, factory: sessionmaker, concurrency: int = 1):
        self.factory = factory
        self.concurrency = max(1, concurrency)
        self.stop_event = threading.Event()
        self._claim_lock = threading.Lock()
        self.threads = [
            threading.Thread(target=self._work, name=f"masterone-resolution-{i}", daemon=True)
            for i in range(self.concurrency)
        ]

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
        for thread in self.threads:
            thread.start()

    def close(self):
        self.stop_event.set()
        for thread in self.threads:
            thread.join(timeout=3)

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

    def _claim_next(self) -> str | None:
        """Atomically move the oldest QUEUED task to RUNNING and return its run_id."""
        with self._claim_lock:
            with self.factory.begin() as db:
                task = db.query(ResolutionTask).filter_by(status="QUEUED").order_by(
                    ResolutionTask.created_at, ResolutionTask.id).first()
                if task is None:
                    return None
                task.status = "RUNNING"
                task.phase = "loading"
                task.updated_at = now_utc()
                run = db.get(ResolutionRun, task.run_id)
                if run is not None:
                    run.status = "RUNNING"
                    run.phase = "loading"
                return task.run_id

    def _work(self):
        while not self.stop_event.is_set():
            run_id = self._claim_next()
            if run_id is None:
                self.stop_event.wait(0.3)
                continue
            try:
                run_pipeline(run_id, self.factory,
                             lambda phase, done, total: self._progress(run_id, phase, done, total))
            except Exception:
                logging.exception("Pipeline failed with exception")
                mark_failed(run_id, self.factory)
