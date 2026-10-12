"""Validate and commit run in child processes, so a large import never holds an API request or its GIL.

The request records the state change (VALIDATING / COMMITTING) and returns 202. A
supervisor thread starts a spawned child, which opens its own engine and records the
outcome. If the child dies before recording one, the supervisor releases the draft.
"""

from __future__ import annotations

import logging
import threading
from multiprocessing import get_context

from fastapi import HTTPException, Request, status

from masterone.config import get_settings

_slots = threading.BoundedSemaphore(get_settings().import_jobs_concurrency)
_lock = threading.Lock()
_running: set[str] = set()


def inline() -> bool:
    return get_settings().import_jobs_inline


def database_url(request: Request) -> str:
    return request.app.state.session_factory.kw["bind"].url.render_as_string(hide_password=False)


def start(kind: str, session_id: str, owner_user_id: str, url: str, *, before_start=None) -> None:
    with _lock:
        if session_id in _running:
            raise HTTPException(status.HTTP_409_CONFLICT, "Phiên đang được xử lý; hãy đợi hoàn tất")
        # A previous child may have committed READY before its supervisor exits.
        # Reject that overlap before committing a new VALIDATING/COMMITTING state.
        if before_start is not None:
            before_start()
        _running.add(session_id)
    threading.Thread(target=_supervise, args=(kind, session_id, owner_user_id, url),
                     name=f"import-{kind}-{session_id[:8]}", daemon=True).start()


def _supervise(kind: str, session_id: str, owner_user_id: str, url: str) -> None:
    try:
        with _slots:
            child = get_context("spawn").Process(target=run_import_job,
                                                 args=(kind, session_id, owner_user_id, url),
                                                 name=f"import-{kind}")
            child.start()
            child.join()
        if child.exitcode != 0:
            _release_draft(url, session_id, kind, child.exitcode)
    except Exception:
        logging.exception("IMPORT_JOB_SUPERVISOR_FAILED kind=%s session=%s", kind, session_id)
    finally:
        with _lock:
            _running.discard(session_id)


def run_import_job(kind: str, session_id: str, owner_user_id: str, url: str) -> None:
    """Child-process entry point: one validate or commit, on its own engine."""
    from masterone.db.session import create_database
    from masterone.logging_config import configure_logging
    from masterone.mdm.models import ResolutionUploadSession
    from masterone.mdm.upload_sessions import commit_upload_session, validate_upload_session

    configure_logging()
    engine, factory = create_database(url)
    try:
        with factory() as db:
            session = db.get(ResolutionUploadSession, session_id)
            if session is None:
                return
            if kind == "validate":
                validate_upload_session(db, session, begun=True)
            else:
                commit_upload_session(db, session, owner_user_id, begun=True)
    finally:
        engine.dispose()


def _release_draft(url: str, session_id: str, kind: str, exitcode: int) -> None:
    """The child died without recording an outcome (for example, killed for memory)."""
    from masterone.db.session import create_database
    from masterone.mdm.models import ResolutionUploadSession

    expected, released = ("VALIDATING", "ERROR") if kind == "validate" else ("COMMITTING", "READY")
    engine, factory = create_database(url)
    try:
        with factory() as db:
            draft = db.get(ResolutionUploadSession, session_id)
            if draft is not None and draft.status == expected:
                draft.status = released
                draft.error_message = f"Tiến trình xử lý bị dừng đột ngột (mã {exitcode}); hãy thử lại."
                db.commit()
        logging.error("IMPORT_JOB_DIED kind=%s session=%s exitcode=%s", kind, session_id, exitcode)
    finally:
        engine.dispose()
