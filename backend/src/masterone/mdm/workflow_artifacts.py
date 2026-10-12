"""Sealed, hash-verified DB artifacts. No local file is a readiness signal."""
from masterone.mdm.generation_staging import digest
from masterone.mdm.workflow_models import WorkflowArtifact


def profile_artifact(parent, profile):
    payload = {'prepare_artifact_id': parent.id, 'prepare_content_hash': parent.content_hash,
               'profile': profile}
    return WorkflowArtifact(run_id=parent.run_id, phase='PROFILE', payload=payload, content_hash=digest(payload))


def seal(factory, run_id, phase, payload):
    with factory.begin() as db:
        artifact = WorkflowArtifact(run_id=run_id, phase=phase, payload=payload, content_hash=digest(payload))
        db.add(artifact)
        db.flush()
        if phase == 'PREPARE' and all(key in payload.get('profile', {}) for key in
                                      ('field_completeness', 'records_with_quality_flags', 'schema')):
            # Persist a small independently verified summary in the same transaction.
            db.add(profile_artifact(artifact, payload['profile']))
        return artifact.id


def load(db, artifact_id, run_id, phase):
    artifact = db.get(WorkflowArtifact, artifact_id)
    if artifact is None or artifact.run_id != run_id or artifact.phase != phase or digest(artifact.payload) != artifact.content_hash:
        raise ValueError('WORKFLOW_ARTIFACT_INVALID')
    return artifact.payload


def warm_profile(factory, run_id):
    """Explicit deployment backfill; GET never writes or rewrites a PREPARE artifact."""
    from masterone.mdm.data_profile import build_profile, record_read
    from masterone.mdm.models import ResolutionRecord

    with factory.begin() as db:
        parent = db.query(WorkflowArtifact).filter_by(run_id=run_id, phase='PREPARE').order_by(WorkflowArtifact.created_at.desc()).first()
        if parent is None:
            return
        existing = db.query(WorkflowArtifact).filter_by(run_id=run_id, phase='PROFILE').filter(
            WorkflowArtifact.payload['prepare_artifact_id'].as_string() == parent.id).first()
        if existing:
            summary = load(db, existing.id, run_id, 'PROFILE')
            if summary['prepare_content_hash'] != parent.content_hash:
                raise ValueError('WORKFLOW_ARTIFACT_INVALID')
            return
        prepared = load(db, parent.id, run_id, 'PREPARE')
        profile = prepared['profile']
        if not all(key in profile for key in ('field_completeness', 'records_with_quality_flags', 'schema')):
            records = prepared.get('records')
            if records is None:
                records = (record_read(row) for row in db.query(ResolutionRecord).filter_by(run_id=run_id).yield_per(512))
            profile = {**profile, **build_profile(records)}
        db.add(profile_artifact(parent, profile))
