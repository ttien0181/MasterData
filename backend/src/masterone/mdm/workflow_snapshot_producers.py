"""DB cursors freeze owned decision universes without full row collections."""
from masterone.mdm.engine_adapter import engine_context
from masterone.mdm.generation_staging import config_identity, digest, digest_sequence
from masterone.mdm.models import ResolutionEngineSelection, ResolutionNegativeEdge, ResolutionPair, ResolutionRecord
from masterone.mdm.pipeline.golden_v2 import record_payload
from masterone.mdm.pipeline.pair_assessment import assessment_record
from masterone.mdm.workflow_assessment_reads import active_assessment, effective_result
from masterone.mdm.workflow_models import WorkflowArtifact, WorkflowConflict, WorkflowOperation, WorkflowPair


def record_stream(db, run_id, *, assessed=False):
    for row in db.query(ResolutionRecord).filter_by(run_id=run_id).order_by(ResolutionRecord.id).yield_per(512):
        payload = record_payload(row)
        yield assessment_record(payload) if assessed else payload


def negative_stream(db, run_id):
    """Unique canonical links; merge two sorted DB streams without Python sets."""
    from heapq import merge

    from sqlalchemy import case
    def ordered(model, **filters):
        left, right = model.left_record_id, model.right_record_id
        a, b = case((left <= right, left), else_=right), case((left <= right, right), else_=left)
        return db.query(a, b).filter_by(run_id=run_id, **filters).distinct().order_by(a, b).yield_per(512)
    current = ordered(ResolutionNegativeEdge, active=True)
    legacy = ordered(ResolutionPair, user_decision='NON_MATCH')
    prior = None
    from masterone.mdm.workflow_structure import structure_links
    for pair in merge((tuple(p) for p in current), (tuple(p) for p in legacy), sorted(structure_links(db, run_id))):
        canonical = tuple(sorted(pair))
        if canonical != prior:
            yield {'left_id': canonical[0], 'right_id': canonical[1]}
        prior = canonical


def snapshot_streams(db, run, *, allow_partial=False):
    from masterone.mdm.workflow_assessment import assessment_code_identity
    assessment = active_assessment(db, run.id)
    selected = db.get(ResolutionEngineSelection, run.id)
    if selected is None:
        raise ValueError('WORKFLOW_PROFILE_CHANGED')
    source_hash = digest_sequence(record_stream(db, run.id))
    if assessment:
        configuration = assessment.configuration
        if configuration['source_input_hash'] != source_hash:
            raise ValueError('ASSESSMENT_INPUT_CHANGED')
        if (configuration['source_profile_identity'] != digest(selected.configuration)
                or configuration['assessment_code_identity'] != assessment_code_identity()):
            raise ValueError('ASSESSMENT_CONFIG_CHANGED')
        profile, policy = configuration['profile'], configuration['policy_version']
        input_hash = digest_sequence(record_stream(db, run.id, assessed=True))
        if input_hash != configuration['input_hash']:
            raise ValueError('ASSESSMENT_INPUT_CHANGED')
    else:
        fields = db.query(WorkflowArtifact.payload['input_hash'].as_string(),
            WorkflowArtifact.payload['profile_identity'].as_string(),
            WorkflowArtifact.payload['execution_identity'].as_string()).filter_by(run_id=run.id, phase='DECIDE').order_by(
                WorkflowArtifact.created_at.desc()).first()
        if fields is None or fields[0] != source_hash or fields[1] != digest(selected.configuration):
            raise ValueError('WORKFLOW_INPUT_CHANGED')
        profile, policy, input_hash = selected.configuration, run.policy_version, source_hash
    with engine_context(profile):
        execution_identity = digest(config_identity(policy))
    expected_execution = assessment.configuration['execution_identity'] if assessment else fields[2]
    if execution_identity != expected_execution:
        raise ValueError('WORKFLOW_CONFIG_CHANGED')
    def pairs():
        return db.query(WorkflowPair).filter_by(run_id=run.id).order_by(WorkflowPair.id).yield_per(512)
    metadata = {'input_hash': input_hash, 'profile_identity': digest(profile), 'execution_identity': execution_identity,
                'profile': dict(profile), 'policy_version': policy, 'workflow_revision': run.workflow_revision,
                'decision_revision': run.decision_revision, 'assessment_id': assessment.id if assessment else None,
                'source_input_hash': source_hash, 'source_profile_identity': digest(selected.configuration),
                'negative_pairs_hash': digest_sequence((r['left_id'], r['right_id']) for r in negative_stream(db, run.id)),
                'allow_partial': allow_partial}
    streams = {'records': record_stream(db, run.id, assessed=assessment is not None),
        'pairs': (effective_result(row) for row in pairs()),
        'overlays': ({'id': row.id, 'value': row.decision} for row in db.query(
            WorkflowPair.id, WorkflowPair.decision).filter_by(run_id=run.id).order_by(WorkflowPair.id).yield_per(512)),
        'review_reasons': ({'id': row.id, 'value': row.review_reason} for row in db.query(
            WorkflowPair.id, WorkflowPair.review_reason).filter(WorkflowPair.run_id == run.id,
                WorkflowPair.review_reason.is_not(None), WorkflowPair.review_reason != '').order_by(
                    WorkflowPair.id).yield_per(512)),
        'negative_pairs': negative_stream(db, run.id),
        'group_approvals': ({'id': op.id, **op.result} for op in db.query(WorkflowOperation).filter_by(
            run_id=run.id, operation_type='GROUP_APPROVAL', status='COMPLETED').order_by(WorkflowOperation.created_at).yield_per(512))}
    if allow_partial:
        streams['known_conflicts'] = ({**row.payload, 'id': row.id, 'pair_id': row.pair_id} for row in db.query(
            WorkflowConflict).filter_by(run_id=run.id, resolved=False).order_by(WorkflowConflict.id).yield_per(512))
    return metadata, streams


def validate_streamed_snapshot(db, run, metadata):
    assessment = active_assessment(db, run.id)
    if (assessment.id if assessment else None) != metadata.get('assessment_id'):
        raise ValueError('ASSESSMENT_CHANGED')
    current, _streams = snapshot_streams(db, run, allow_partial=metadata.get('allow_partial', False))
    for key, code in [('source_input_hash', 'WORKFLOW_INPUT_CHANGED'), ('input_hash', 'WORKFLOW_INPUT_CHANGED'),
                      ('source_profile_identity', 'WORKFLOW_PROFILE_CHANGED'), ('profile_identity', 'WORKFLOW_PROFILE_CHANGED'),
                      ('execution_identity', 'WORKFLOW_CONFIG_CHANGED'), ('policy_version', 'WORKFLOW_CONFIG_CHANGED'),
                      ('negative_pairs_hash', 'WORKFLOW_NEGATIVE_LINKS_CHANGED')]:
        if current[key] != metadata[key]:
            raise ValueError(code)


def ensure_streamed_snapshot(operation_id, factory, progress):
    from masterone.mdm.models import ResolutionRun
    from masterone.mdm.workflow_jobs import job_identity
    from masterone.mdm.workflow_snapshot_storage import seal_snapshot
    with factory.begin() as db:
        run, op, _ = job_identity(db, operation_id)
        if op.artifact_id:
            return op.artifact_id
        run_id, revisions, partial = run.id, (run.workflow_revision, run.decision_revision), op.payload.get('allow_partial', False)
    progress('snapshot', 0, 1)
    with factory() as db:
        metadata, streams = snapshot_streams(db, db.get(ResolutionRun, run_id), allow_partial=partial)
        artifact_id = seal_snapshot(factory, run_id, 'DECISION_SNAPSHOT', metadata, streams, operation_id=operation_id)
    with factory.begin() as db:
        run, op, _ = job_identity(db, operation_id)
        if (run.workflow_revision, run.decision_revision) != revisions or metadata['decision_revision'] != revisions[1]:
            raise ValueError('WORKFLOW_REVISION_CHANGED')
        op.artifact_id = artifact_id
    progress('snapshot', 1, 1)
    return artifact_id
