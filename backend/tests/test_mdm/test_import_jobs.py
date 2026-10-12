"""Draft state machine for background validate/commit, and the child-process job path."""

import time
from datetime import timedelta

import pytest
from fastapi import HTTPException
from masterone.auth.models import User
from masterone.config import get_settings
from masterone.db.base import Base, now_utc
from masterone.db.session import create_database
from masterone.mdm import import_jobs
from masterone.mdm.models import ResolutionUploadSession
from masterone.mdm.upload_sessions import (
    begin_commit,
    begin_validation,
    create_upload_session,
)
from sqlalchemy import update


@pytest.fixture
def file_db(tmp_path, monkeypatch):
    """A file-backed database: child processes must be able to open the same data."""
    for field in ("r2_endpoint_url", "r2_access_key_id", "r2_secret_access_key", "r2_bucket"):
        monkeypatch.setattr(get_settings(), field, "")
    monkeypatch.setattr(get_settings(), "upload_staging_dir", str(tmp_path / "staging"))
    # Spawned children read their settings from the environment, not from this process's objects.
    monkeypatch.setenv("UPLOAD_STAGING_DIR", str(tmp_path / "staging"))
    for name in ("R2_ENDPOINT_URL", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET"):
        monkeypatch.setenv(name, "")
    url = f"sqlite:///{(tmp_path / 'jobs.db').as_posix()}"
    engine, factory = create_database(url)
    Base.metadata.create_all(engine)
    with factory.begin() as db:
        user = User(email="job@example.com", email_normalized="job@example.com", password_hash="unused",
                    role="STEWARD")
        db.add(user)
        db.flush()
        owner = user.id
    yield url, factory, owner
    engine.dispose()


def _draft(factory, owner, status="READY"):
    with factory() as db:
        session = create_upload_session(db, owner, "draft", [{"filename": "a.csv", "size": 10}])
        session_id = session.id
        db.execute(update(ResolutionUploadSession).where(ResolutionUploadSession.id == session_id)
                   .values(status=status))
        db.commit()
    return session_id


def test_busy_validation_is_refused_until_the_job_is_stale(file_db):
    _, factory, owner = file_db
    session_id = _draft(factory, owner, status="VALIDATING")
    with factory() as db:
        with pytest.raises(HTTPException) as refused:
            begin_validation(db, db.get(ResolutionUploadSession, session_id))
        assert refused.value.status_code == 409
        # A job that stopped hours ago must not lock the draft forever.
        db.execute(update(ResolutionUploadSession).where(ResolutionUploadSession.id == session_id)
                   .values(updated_at=now_utc() - timedelta(hours=3)))
        db.commit()
        begin_validation(db, db.get(ResolutionUploadSession, session_id))
        assert db.get(ResolutionUploadSession, session_id).status == "VALIDATING"


def test_commit_is_marked_once_and_cannot_start_twice(file_db):
    _, factory, owner = file_db
    session_id = _draft(factory, owner, status="READY")
    with factory() as db:
        begin_commit(db, db.get(ResolutionUploadSession, session_id), owner)
        assert db.get(ResolutionUploadSession, session_id).status == "COMMITTING"
        with pytest.raises(HTTPException) as refused:
            begin_commit(db, db.get(ResolutionUploadSession, session_id), owner)
        assert refused.value.status_code == 409


def test_draft_is_released_when_its_child_process_dies(file_db):
    url, factory, owner = file_db
    session_id = _draft(factory, owner, status="COMMITTING")
    import_jobs._release_draft(url, session_id, "commit", exitcode=-9)
    with factory() as db:
        draft = db.get(ResolutionUploadSession, session_id)
        assert draft.status == "READY"
        assert "dừng đột ngột" in draft.error_message


def test_child_process_runs_the_validation_and_records_the_outcome(file_db):
    """Real spawn: the child opens its own engine. A draft with no uploaded parts ends in ERROR, not stuck."""
    url, factory, owner = file_db
    session_id = _draft(factory, owner, status="READY")
    with factory() as db:
        begin_validation(db, db.get(ResolutionUploadSession, session_id))
    import_jobs.start("validate", session_id, owner, url)
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        with factory() as db:
            if db.get(ResolutionUploadSession, session_id).status != "VALIDATING" and session_id not in import_jobs._running:
                break
        time.sleep(0.5)
    with factory() as db:
        draft = db.get(ResolutionUploadSession, session_id)
        assert draft.status == "ERROR"
        assert draft.error_message
    assert session_id not in import_jobs._running


def test_validate_and_commit_answer_202_and_refuse_a_second_start(client, monkeypatch):
    """With jobs enabled, the endpoints only mark the draft and hand the work to import_jobs."""
    monkeypatch.setattr(get_settings(), "import_jobs_inline", False)
    started = []
    def fake_start(kind, session_id, owner, url, *, before_start=None):
        if before_start:
            before_start()
        started.append((kind, session_id))
    monkeypatch.setattr(import_jobs, "start", fake_start)
    created = client.post("/api/v1/mdm/upload-sessions",
                          json={"name": "async", "files": [{"filename": "a.csv", "size": 10}]})
    assert created.status_code == 201
    session_id = created.json()["session_id"]
    prefix = f"/api/v1/mdm/upload-sessions/{session_id}"

    validating = client.post(f"{prefix}/validate")
    assert validating.status_code == 202
    assert validating.json()["status"] == "VALIDATING"
    assert client.post(f"{prefix}/validate").status_code == 409

    with client.session_factory.begin() as db:
        db.query(ResolutionUploadSession).filter_by(id=session_id).update({"status": "READY"})
    committing = client.post(f"{prefix}/commit")
    assert committing.status_code == 202
    assert committing.json() == {"run_id": None, "status": "COMMITTING", "profile": None}
    assert client.post(f"{prefix}/commit").status_code == 409
    assert started == [("validate", session_id), ("commit", session_id)]


@pytest.mark.parametrize('action', ['validate','commit'])
def test_start_conflict_does_not_leave_draft_marked_running(client, monkeypatch, action):
    monkeypatch.setattr(get_settings(), 'import_jobs_inline', False)
    draft = client.post('/api/v1/mdm/upload-sessions',json={'files':[{'filename':'a.csv','size':1}]}).json()
    sid = draft['session_id']
    with client.session_factory.begin() as db:
        db.get(ResolutionUploadSession,sid).status = 'READY'
    monkeypatch.setattr(import_jobs, '_running', {sid})
    assert client.post(f'/api/v1/mdm/upload-sessions/{sid}/{action}').status_code == 409
    with client.session_factory() as db:
        assert db.get(ResolutionUploadSession,sid).status == 'READY'
