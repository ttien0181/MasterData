import threading
from concurrent.futures import Future, ThreadPoolExecutor

from sqlalchemy.orm import sessionmaker

from masterone.db.base import now_utc
from masterone.mdm.models import ResolutionRun, ResolutionTask
from masterone.mdm.resolution_service import mark_failed, run_pipeline

_MAX_PARALLEL = 3


class ResolutionWorker:
    """Multi-threaded local worker; up to _MAX_PARALLEL runs execute in parallel."""

    def __init__(self, factory: sessionmaker, max_workers: int = _MAX_PARALLEL):
        self.factory = factory
        self.max_workers = max_workers
        self.stop_event = threading.Event()
        self._lock = threading.Lock()
        self._active: dict[str, Future] = {}  # run_id -> Future
        self.thread = threading.Thread(target=self._dispatch_loop, name="masterone-resolution-dispatcher", daemon=True)
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="masterone-pipeline")

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
        self.thread.join(timeout=5)
        self._executor.shutdown(wait=False)

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

    def _run_one(self, run_id: str):
        try:
            run_pipeline(run_id, self.factory,
                         lambda phase, done, total: self._progress(run_id, phase, done, total))
        except Exception:
            mark_failed(run_id, self.factory)
        finally:
            with self._lock:
                self._active.pop(run_id, None)

    def _dispatch_loop(self):
        while not self.stop_event.is_set():
            with self._lock:
                slots = self.max_workers - len(self._active)

            if slots <= 0:
                self.stop_event.wait(0.3)
                continue

            with self.factory() as db:
                tasks = (db.query(ResolutionTask)
                         .filter_by(status="QUEUED")
                         .order_by(ResolutionTask.created_at, ResolutionTask.id)
                         .limit(slots)
                         .all())
                run_ids = [t.run_id for t in tasks]

            if not run_ids:
                self.stop_event.wait(0.3)
                continue

            with self._lock:
                for run_id in run_ids:
                    if run_id not in self._active:
                        future = self._executor.submit(self._run_one, run_id)
                        self._active[run_id] = future
