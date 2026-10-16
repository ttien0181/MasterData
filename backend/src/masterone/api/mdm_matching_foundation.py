"""Owner-scoped M1 registry endpoints; mutation checks live in backend dependencies."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from masterone.auth.security import CurrentSession, require_capability, require_mutation_capability
from masterone.db.session import get_session
from masterone.mdm.matching_foundation_contracts import (
    BindingRead,
    DatasetRegister,
    DefaultApply,
    EvaluationRegister,
    FeedbackRead,
    LearningRequest,
    ProfileCreate,
    ProfileRead,
    ProfileTransition,
    RebuildRequest,
    StructureExecuteRequest,
    StructurePreviewRequest,
)
from masterone.mdm.matching_foundation_models import EvaluationReport, LabeledDataset, MatchingProfile, StewardFeedback
from masterone.mdm.matching_foundation_service import (
    SCOPE,
    apply_default,
    authorize,
    create_profile,
    governance_audit,
    latest_binding,
    owned,
    require_flag,
    safe_artifact_uri,
    transition_profile,
    verify_profile,
)
from masterone.mdm.ownership import owned_run

router = APIRouter(prefix='/mdm/matching', tags=['matching-foundation'])


@router.post('/runs/{run_id}/learn-thresholds')
def learn_thresholds(run_id: str, body: LearningRequest, db: Session = Depends(get_session),
                     auth: CurrentSession = Depends(require_mutation_capability('learn_match_profile'))):
    require_flag('advanced_evaluation_registry_enabled')
    require_flag('steward_feedback_capture_enabled')
    require_flag('matching_profile_registry_enabled')
    authorize(db, auth.user.id, 'learn_match_profile')
    from masterone.mdm.matching_learning import learning_status
    return learning_status(db, run_id, auth.user.id, body.dataset_id)


@router.get('/runs/{run_id}/evaluations')
def evaluations_read(run_id: str, db: Session = Depends(get_session),
                     auth: CurrentSession = Depends(require_capability('read'))):
    require_flag('advanced_evaluation_registry_enabled')
    owned_run(db, run_id, auth.user.id)
    rows = db.query(EvaluationReport).filter_by(run_id=run_id, owner_user_id=auth.user.id).order_by(EvaluationReport.created_at)
    return [{'id': r.id, 'status': r.status, 'evaluated_scope': r.evaluated_scope, 'label_support': r.label_support,
             'profile_id': r.profile_id, 'profile_digest': r.profile_digest, 'report_hash': r.report_hash} for r in rows]


@router.get('/defaults/{data_scope}')
def default_read(data_scope: str, db: Session = Depends(get_session),
                 auth: CurrentSession = Depends(require_capability('read'))):
    require_flag('matching_profile_registry_enabled')
    if data_scope != SCOPE:
        raise HTTPException(422, 'Unsupported owner scope')
    from masterone.mdm.matching_foundation_models import MatchingProfileDefault
    row = db.get(MatchingProfileDefault, (auth.user.id, data_scope))
    return {'profile_id': row.profile_id if row else None, 'revision': row.revision if row else 0, 'data_scope': data_scope}


@router.get('/runs/{run_id}/structure')
def structure_read(run_id: str, db: Session = Depends(get_session),
                   auth: CurrentSession = Depends(require_capability('read'))):
    owned_run(db, run_id, auth.user.id)
    from masterone.auth.security import ROLE_CAPABILITIES
    from masterone.config import get_settings
    from masterone.mdm.workflow_structure import active_constraints
    return {'enabled': get_settings().entity_structure_enabled,
            'can_manage': 'manage_entity_structure' in ROLE_CAPABILITIES.get(auth.user.role, frozenset()),
            'active_constraints': [{'id': r.id, 'kind': r.kind, 'originating_operation_id': r.originating_operation_id,
                'partitions': r.evidence_ref['partition_manifest']['partitions'],
                'manifest_hash': r.partition_manifest_hash} for r in active_constraints(db, run_id)]}


@router.get('/runs/{run_id}/structure/bridges')
def structure_bridges(run_id: str, left_cluster_id: str, right_cluster_id: str, db: Session = Depends(get_session),
                      auth: CurrentSession = Depends(require_capability('read'))):
    run = owned_run(db, run_id, auth.user.id)
    require_flag('entity_structure_enabled')
    from masterone.mdm.workflow_cluster_reads import current_generation
    from masterone.mdm.workflow_generation_reads import GenerationReader
    from masterone.mdm.workflow_group_approvals import group_snapshot
    generation = current_generation(db, run)
    reader = GenerationReader(db, run, generation)
    left = set(reader.group(left_cluster_id)['member_record_ids'])
    right = set(reader.group(right_cluster_id)['member_record_ids'])
    if left_cluster_id == right_cluster_id or left & right:
        raise HTTPException(422, 'Select two distinct current clusters')
    snapshot = group_snapshot(db, run, sorted(left | right))
    records = {r['id']: r for r in snapshot['records']}
    pairs = [p for p in snapshot['pairs'] if (p['left_id'] in left and p['right_id'] in right)
             or (p['right_id'] in left and p['left_id'] in right)]
    return {'total': len(pairs), 'limit': 100, 'items': [{'pair_id': p['id'],
        'left_name': records[p['left_id']]['raw_fields'].get('name') or p['left_id'],
        'right_name': records[p['right_id']]['raw_fields'].get('name') or p['right_id'],
        'decision': snapshot['overlays'].get(p['id'], 'UNDECIDED'),
        'rule_score': p['score'], 'score_unit': 'p119_rule_v1'} for p in pairs[:100]]}


@router.post('/runs/{run_id}/structure/{kind}/preview')
def structure_preview(run_id: str, kind: str, body: StructurePreviewRequest, db: Session = Depends(get_session),
                      auth: CurrentSession = Depends(require_mutation_capability('manage_entity_structure'))):
    if kind not in ('split', 'remerge'):
        raise HTTPException(422, 'Unsupported structure operation')
    if kind == 'split' and body.partitions is None:
        raise HTTPException(422, 'Two partitions are required')
    if not body.reason.strip():
        raise HTTPException(422, 'Reason is required')
    from masterone.mdm.workflow_structure import preview
    return preview(db, run_id, body.model_dump(), auth.user.id, kind.upper())


@router.post('/runs/{run_id}/structure/execute', status_code=202)
def structure_execute(run_id: str, body: StructureExecuteRequest, db: Session = Depends(get_session),
                      auth: CurrentSession = Depends(require_mutation_capability('manage_entity_structure'))):
    from masterone.mdm.workflow_structure import execute
    return execute(db, run_id, body.model_dump(), auth.user.id)


@router.post('/runs/{run_id}/rebuild-candidates', status_code=202)
def rebuild_candidates(run_id: str, body: RebuildRequest, db: Session = Depends(get_session),
                       auth: CurrentSession = Depends(require_mutation_capability('apply_match_profile'))):
    from masterone.mdm.workflow_candidate_rebuild import request_rebuild
    return request_rebuild(db, run_id, body, auth.user.id)


@router.get('/runs/{run_id}/agents')
def agents_read(run_id: str, offset: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=100),
                db: Session = Depends(get_session),
                auth: CurrentSession = Depends(require_capability('read'))):
    owned_run(db, run_id, auth.user.id)
    from masterone.mdm.matching_agents.workflow_agents import latest_result
    from masterone.mdm.workflow_artifacts import list_page, read_value
    result = latest_result(db, run_id)
    if not result:
        raise HTTPException(404, 'Agent evidence not found')
    fields = ('contract_version', 'status', 'warning', 'profile_id', 'profile_digest', 'binding_id',
              'binding_revision', 'input_hash', 'candidate_hash', 'rule_identity', 'encoder_identity',
              'preprocessing_identity', 'runtime', 'runtime_seconds', 'coverage', 'field_coverage', 'automation_allowed', 'error_code')
    metadata = {field: read_value(db, result['artifact_id'], run_id, 'MATCHING_AGENTS', [field]) for field in fields}
    page = list_page(db, result['artifact_id'], run_id, 'MATCHING_AGENTS', ['pairs'], offset, limit)
    return {**metadata, 'pairs': page['items'], 'total_pairs': page['total'], 'next_cursor': page['next_cursor']}


def commit(db):
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, 'Matching registry revision or reference conflict') from exc


def profile_read(row):
    verify_profile(row)
    return {'id': row.id, 'owner_user_id': row.owner_user_id, 'data_scope': row.data_scope,
        'version': row.version, 'config': row.config_json, 'digest': row.config_digest,
        'lifecycle': row.lifecycle, 'lifecycle_revision': row.lifecycle_revision, 'score_unit': row.score_unit}


@router.post('/profiles', response_model=ProfileRead, status_code=201)
def profile_create(body: ProfileCreate, db: Session = Depends(get_session),
                   auth: CurrentSession = Depends(require_mutation_capability('learn_match_profile'))):
    require_flag('matching_profile_registry_enabled')
    try:
        row = create_profile(db, auth.user.id, body)
        commit(db)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, 'Profile version already exists') from exc
    return profile_read(row)


@router.get('/profiles', response_model=list[ProfileRead])
def profiles(db: Session = Depends(get_session), auth: CurrentSession = Depends(require_capability('read'))):
    require_flag('matching_profile_registry_enabled')
    return [profile_read(row) for row in db.query(MatchingProfile).filter_by(owner_user_id=auth.user.id).order_by(MatchingProfile.version)]


@router.get('/profiles/{profile_id}', response_model=ProfileRead)
def profile_get(profile_id: str, db: Session = Depends(get_session), auth: CurrentSession = Depends(require_capability('read'))):
    require_flag('matching_profile_registry_enabled')
    return profile_read(owned(db, MatchingProfile, profile_id, auth.user.id))


@router.post('/profiles/{profile_id}/validate', response_model=ProfileRead)
def profile_validate(profile_id: str, body: ProfileTransition, db: Session = Depends(get_session),
                     auth: CurrentSession = Depends(require_mutation_capability('learn_match_profile'))):
    require_flag('matching_profile_registry_enabled')
    row = transition_profile(db, auth.user.id, profile_id, body.expected_revision, 'VALIDATED')
    commit(db)
    return profile_read(row)


@router.post('/profiles/{profile_id}/retire', response_model=ProfileRead)
def profile_retire(profile_id: str, body: ProfileTransition, db: Session = Depends(get_session),
                   auth: CurrentSession = Depends(require_mutation_capability('apply_match_profile'))):
    require_flag('matching_profile_registry_enabled')
    row = transition_profile(db, auth.user.id, profile_id, body.expected_revision, 'RETIRED')
    commit(db)
    return profile_read(row)


@router.put('/defaults/{data_scope}')
def default_apply(data_scope: str, body: DefaultApply, db: Session = Depends(get_session),
                  auth: CurrentSession = Depends(require_mutation_capability('apply_match_profile'))):
    require_flag('matching_profile_registry_enabled')
    if data_scope != SCOPE:
        raise HTTPException(422, 'M1 supports owner-private scope only')
    row = apply_default(db, auth.user.id, data_scope, body)
    commit(db)
    return {'profile_id': row.profile_id, 'revision': row.revision, 'data_scope': row.data_scope}


@router.get('/runs/{run_id}/binding', response_model=BindingRead | None)
def binding_get(run_id: str, db: Session = Depends(get_session), auth: CurrentSession = Depends(require_capability('read'))):
    require_flag('matching_profile_registry_enabled')
    owned_run(db, run_id, auth.user.id)
    row = latest_binding(db, run_id)
    return None if row is None else {key: getattr(row, key) for key in BindingRead.model_fields}


@router.get('/runs/{run_id}/feedback', response_model=list[FeedbackRead])
def feedback_get(run_id: str, db: Session = Depends(get_session), auth: CurrentSession = Depends(require_capability('read_audit'))):
    require_flag('steward_feedback_capture_enabled')
    owned_run(db, run_id, auth.user.id)
    rows = db.query(StewardFeedback).filter_by(run_id=run_id, owner_user_id=auth.user.id).order_by(StewardFeedback.created_at, StewardFeedback.id)
    return [{'id': r.id, 'run_id': r.run_id, 'pair_id': r.workflow_pair_id,
        'pair_revision': r.pair_revision, 'label': r.human_direct_label, 'origin': r.origin,
        'supersedes_feedback_id': r.supersedes_feedback_id, 'feature_snapshot_hash': r.feature_snapshot_hash,
        'assessment_id': r.assessment_id, 'profile_id': r.profile_id, 'profile_digest': r.profile_digest,
        'trainability_status': r.trainability_status, 'trainability_reason': r.trainability_reason} for r in rows]


@router.post('/datasets', status_code=201)
def dataset_register(body: DatasetRegister, db: Session = Depends(get_session),
                     auth: CurrentSession = Depends(require_mutation_capability('learn_match_profile'))):
    require_flag('advanced_evaluation_registry_enabled')
    authorize(db, auth.user.id, 'learn_match_profile')
    row = LabeledDataset(**body.model_dump(), owner_user_id=auth.user.id, creator_id=auth.user.id,
        truth_verification_status='NOT_VERIFIED', trainability_status='NOT_TRAINABLE',
        trainability_reasons=['MANIFEST_REGISTRATION_IS_NOT_LABEL_VERIFICATION'])
    safe_artifact_uri(row.manifest_uri, auth.user.id)
    db.add(row)
    try:
        db.flush()
        governance_audit(db, auth.user.id, row.data_scope, 'MATCH_DATASET_REGISTERED', 'LabeledDataset', row.id)
        commit(db)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, 'Dataset version already exists') from exc
    return {'id': row.id, 'manifest_hash': row.manifest_hash, 'truth_verification_status': row.truth_verification_status,
            'trainability_status': row.trainability_status}


@router.post('/evaluations', status_code=201)
def evaluation_register(body: EvaluationRegister, db: Session = Depends(get_session),
                        auth: CurrentSession = Depends(require_mutation_capability('learn_match_profile'))):
    require_flag('advanced_evaluation_registry_enabled')
    authorize(db, auth.user.id, 'learn_match_profile')
    dataset = owned(db, LabeledDataset, body.dataset_id, auth.user.id)
    profile = owned(db, MatchingProfile, body.profile_id, auth.user.id) if body.profile_id else None
    if profile and profile.data_scope != dataset.data_scope:
        raise HTTPException(409, 'Evaluation scope mismatch')
    binding = None
    if body.run_id:
        owned_run(db, body.run_id, auth.user.id)
        binding = latest_binding(db, body.run_id)
        if binding and (profile is None or binding.profile_id != profile.id or binding.data_scope != dataset.data_scope):
            raise HTTPException(409, 'Evaluation historical profile mismatch')
    safe_artifact_uri(body.report_uri, auth.user.id)
    values = body.model_dump()
    if dataset.truth_verification_status != 'VERIFIED':
        values['evaluated_scope'] = {**values['evaluated_scope'], 'diagnostic_only': True,
                                     'independent_truth_verified': False,
                                     'artifact_verification': 'NOT_PERFORMED_M1_METADATA_REGISTRY'}
    row = EvaluationReport(**values, owner_user_id=auth.user.id, creator_id=auth.user.id,
        data_scope=dataset.data_scope, profile_digest=profile.config_digest if profile else None,
        binding_id=binding.id if binding else None)
    db.add(row)
    db.flush()
    governance_audit(db, auth.user.id, row.data_scope, 'MATCH_EVALUATION_REGISTERED', 'EvaluationReport', row.id,
                     run_id=row.run_id, details={'diagnostic_only': True})
    commit(db)
    return {'id': row.id, 'status': row.status, 'report_hash': row.report_hash, 'evaluated_scope': row.evaluated_scope}
