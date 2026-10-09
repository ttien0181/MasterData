import re

with open('backend/src/masterone/mdm/upload_sessions.py', 'r', encoding='utf-8') as f:
    content = f.read()

target = '''def commit_upload_session(db: Session, session: ResolutionUploadSession,
                          owner_user_id: str) -> tuple[ResolutionRun, dict]:
    run = db.query(ResolutionRun).filter_by(upload_session_id=session.id).one_or_none()
    if run:
        files = db.query(ResolutionFile).filter_by(run_id=run.id).order_by(ResolutionFile.filename).all()
        source_counts = dict(db.query(ResolutionRecord.source_system, func.count(ResolutionRecord.id))
                             .filter_by(run_id=run.id).group_by(ResolutionRecord.source_system).all())
        session.status = "COMMITTED"
        db.commit()
        return run, {"row_count": run.record_count, "file_count": run.file_count,
                     "source_counts": source_counts,
                     "files": [{"filename": row.filename, "row_count": row.row_count,
                                "sha256": row.sha256} for row in files]}
    if session.status != "READY":
        raise HTTPException(status.HTTP_409_CONFLICT, "Ki?m tra và hoàn t?t t?i file tru?c khi t?o phiên")
    if session.owner_user_id != owner_user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Upload draft không t?n t?i")
    upload_files = db.query(ResolutionUploadFile).filter_by(session_id=session.id).order_by(
        ResolutionUploadFile.position).all()
    paths = [(file.filename, _get_or_materialize(db, session, file)) for file in upload_files]
    streams = []
    try:
        streams = [(filename, path.open("rb")) for filename, path in paths]
        hashes = [f.sha256 for f in upload_files]
        run, profile = create_resolution_run_from_streams(
            db, streams, owner_user_id, session.name, upload_session_id=session.id, known_hashes=hashes)
        session.status = "COMMITTED"
        db.commit()
    finally:
        for _, stream in streams:
            stream.close()
    cleanup_upload_session_storage(db, session)
    return run, profile'''

replacement = '''def prepare_commit_upload_session(db: Session, session: ResolutionUploadSession,
                                  owner_user_id: str) -> tuple[ResolutionRun, dict]:
    run = db.query(ResolutionRun).filter_by(upload_session_id=session.id).one_or_none()
    if run:
        files = db.query(ResolutionFile).filter_by(run_id=run.id).order_by(ResolutionFile.filename).all()
        source_counts = dict(db.query(ResolutionRecord.source_system, func.count(ResolutionRecord.id))
                             .filter_by(run_id=run.id).group_by(ResolutionRecord.source_system).all())
        session.status = "COMMITTED"
        db.commit()
        return run, {"row_count": run.record_count, "file_count": run.file_count,
                     "source_counts": source_counts,
                     "files": [{"filename": row.filename, "row_count": row.row_count,
                                "sha256": row.sha256} for row in files]}
    if session.status != "READY":
        raise HTTPException(status.HTTP_409_CONFLICT, "Ki?m tra và hoàn t?t t?i file tru?c khi t?o phiên")
    if session.owner_user_id != owner_user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Upload draft không t?n t?i")
    
    upload_files = db.query(ResolutionUploadFile).filter_by(session_id=session.id).order_by(
        ResolutionUploadFile.position).all()
    
    total_rows = sum((f.row_count or 0) for f in upload_files)
    file_count = len(upload_files)
    source_counts = {}
    file_profiles = []
    for f in upload_files:
        if f.source_counts:
            for s, c in f.source_counts.items():
                source_counts[s] = source_counts.get(s, 0) + c
        file_profiles.append({"filename": f.filename, "row_count": f.row_count, "sha256": f.sha256})

    run = ResolutionRun(owner_user_id=owner_user_id, upload_session_id=session.id,
                        name=session.name, record_count=total_rows,
                        file_count=file_count, policy_version="person-v1", status="IMPORTING", phase="importing")
    db.add(run)
    
    session.status = "COMMITTED"
    db.commit()
    db.refresh(run)
    
    profile = {"row_count": total_rows, "file_count": file_count, "source_counts": source_counts, "files": file_profiles}
    return run, profile

def execute_commit_upload_session(run_id: str, session_id: str, owner_user_id: str, session_factory):
    with session_factory() as db:
        try:
            session = db.get(ResolutionUploadSession, session_id)
            run = db.get(ResolutionRun, run_id)
            if not session or not run:
                return

            upload_files = db.query(ResolutionUploadFile).filter_by(session_id=session.id).order_by(
                ResolutionUploadFile.position).all()
            paths = [(file.filename, _get_or_materialize(db, session, file)) for file in upload_files]
            streams = []
            try:
                streams = [(filename, path.open("rb")) for filename, path in paths]
                hashes = [f.sha256 for f in upload_files]
                # We can reuse create_resolution_run_from_streams logic here
                # but create_resolution_run_from_streams expects to create the run itself.
                # Actually, if we pass upload_session_id, create_resolution_run_from_streams will return the existing run!
                # Let's check resolution_import.py. If upload_session_id is passed, it just returns the existing run without inserting records if it already exists?
                # No! If we already created it, create_resolution_run_from_streams will just return the existing run with its existing counts and skip inserting records!
                pass
            finally:
                for _, stream in streams:
                    stream.close()
        except Exception as exc:
            import logging
            logging.exception("Background commit failed for run %s", run_id)
'''

with open('scratch/refactor_commit.py', 'w', encoding='utf-8') as f:
    f.write('print("done")')
