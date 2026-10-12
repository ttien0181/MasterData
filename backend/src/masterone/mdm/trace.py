"""Step-level, process-safe timing for a CSV resolution run.

Each process appends one JSON object per line to its own file. Trace failures
must never change an import or resolution result.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

_context: ContextVar[dict] = ContextVar("mdm_trace_context", default={})
_parent: ContextVar[str | None] = ContextVar("mdm_trace_parent", default=None)
_write_lock = threading.Lock()
_logger = logging.getLogger(__name__)
_SAFE = frozenset({
    "trace_id", "upload_session_id", "run_id", "task_id", "attempt_id",
    "file_id", "part_number", "byte_count", "file_count", "record_count",
    "candidate_count", "insufficient_count", "pair_count", "review_pair_count",
    "auto_pair_count", "auto_proposal_count", "auto_record_count",
    "review_case_count", "blocked_pair_count", "batch_count", "branch",
    "storage_backend", "reason", "phase", "status", "queue_wait_ms",
    "elapsed_since_upload_ms", "elapsed_since_run_ms", "duration_ms",
    "span_id", "parent_span_id", "error_type", "worker_count",
})


def enabled() -> bool:
    from masterone.config import get_settings

    return get_settings().mdm_trace_enabled


def _directory() -> Path:
    from masterone.config import get_settings

    return Path(get_settings().mdm_trace_dir)


@contextmanager
def trace_context(**values):
    token = _context.set({**_context.get(), **{key: value for key, value in values.items() if value is not None}})
    try:
        yield
    finally:
        _context.reset(token)


def emit(step: str, event: str, **values) -> None:
    if not enabled():
        return
    fields = {**_context.get(), **values}
    fields = {key: value for key, value in fields.items() if key in _SAFE and value is not None}
    fields.setdefault("trace_id", fields.get("upload_session_id") or fields.get("run_id"))
    payload = {
        "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
        "pid": os.getpid(), "step": step, "event": event, **fields,
    }
    try:
        directory = _directory()
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"mdm-{os.getpid()}.jsonl"
        line = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        with _write_lock:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.write(fd, line)
            finally:
                os.close(fd)
    except OSError as error:
        _logger.warning("MDM_TRACE_WRITE_FAILED error_type=%s", type(error).__name__)
    if step == "run.summary" and event == "completed":
        _logger.info("MDM_RUN_SUMMARY %s", json.dumps(payload, ensure_ascii=False))


@contextmanager
def span(step: str, **values):
    span_id = uuid4().hex
    parent_id = _parent.get()
    token = _parent.set(span_id)
    details = dict(values)
    started = time.perf_counter()
    emit(step, "started", span_id=span_id, parent_span_id=parent_id, **details)
    try:
        yield details
    except BaseException as error:
        emit(step, "failed", span_id=span_id, parent_span_id=parent_id,
             duration_ms=round((time.perf_counter() - started) * 1000, 3),
             error_type=type(error).__name__, **details)
        raise
    else:
        emit(step, "completed", span_id=span_id, parent_span_id=parent_id,
             duration_ms=round((time.perf_counter() - started) * 1000, 3), **details)
    finally:
        _parent.reset(token)
