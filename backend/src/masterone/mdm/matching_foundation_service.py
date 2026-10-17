"""M1 baseline configuration registry and immutable provenance services."""
import json
from copy import deepcopy
from dataclasses import asdict
from pathlib import PurePosixPath

from fastapi import HTTPException
from sqlalchemy import select

from masterone.auth.models import User
from masterone.auth.security import ROLE_CAPABILITIES
from masterone.config import get_settings
from masterone.db.base import now_utc
from masterone.mdm.generation_staging import config_identity, digest
from masterone.mdm.matching_foundation_models import MatchingBinding, MatchingProfile, MatchingProfileDefault
from masterone.mdm.models import ResolutionAuditEvent, ResolutionEngineSelection

SCOPE = 'owner-private'
FEATURE_SCHEMA_ID = digest({'version': 'rule_numeric_snapshot_v1',
                           'fields': ['rule_score', 'field_similarity', 'field_state', 'normalization_tags', 'matched_frequency']})


def require_flag(name):
    if not getattr(get_settings(), name):
        raise HTTPException(404, 'Matching foundation feature disabled')


def authorize(db, actor, capability, *, owner=None):
    user = db.scalar(select(User).where(User.id == actor).with_for_update().execution_options(populate_existing=True))
    if not user or not user.is_active or capability not in ROLE_CAPABILITIES.get(user.role, ()):
        raise HTTPException(403, 'Matching foundation capability required')
    if owner is not None and owner != actor:
        raise HTTPException(404, 'Matching resource not found')
    return user


def owned(db, model, identifier, actor):
    row = db.scalar(select(model).where(model.id == identifier, model.owner_user_id == actor))
    if row is None:
        raise HTTPException(404, 'Matching resource not found')
    return row


def governance_audit(db, actor, scope, action, subject_type, subject_id, *, run_id=None, details=None, pair_id=None):
    db.add(ResolutionAuditEvent(run_id=run_id, actor_user_id=actor, owner_user_id=actor,
        data_scope=scope, action=action, subject_type=subject_type, subject_id=subject_id,
        workflow_pair_id=pair_id, details=details or {}))


def baseline_config(engine_flags=None):
    from masterone.mdm.engine_adapter import CORE_FEATURES, RESUME_FEATURE, STREAMING_FEATURES, engine_context
    from masterone.mdm.pipeline.candidate_engine import CandidateConfig
    from masterone.mdm.pipeline.scoring_engine import ScoringConfig
    settings = get_settings()
    flags = {f: True if settings.mdm_engine_v2_enabled else getattr(settings, f) for f in CORE_FEATURES}
    flags.update({f: True for f in STREAMING_FEATURES if getattr(settings, f)})
    if settings.pipeline_resume_enabled:
        flags[RESUME_FEATURE] = True
    if settings.candidate_recall_v2:
        flags['candidate_recall_v2'] = True
    if engine_flags is not None:
        flags = deepcopy(engine_flags)
    with engine_context(flags):
        identity = config_identity('rule-only-registry-v1')
        blocking = asdict(CandidateConfig())
    config = {'config_schema_version': 'matching_profile_rule_v1', 'kind': 'RULE_ONLY',
        'score_unit': 'p119_rule_v1', 'encoder_identity': None,
        'feature_schema_identity': FEATURE_SCHEMA_ID, 'engine_flags': flags,
        'blocking': blocking, 'scoring': asdict(ScoringConfig()),
        'thresholds': {'mode': 'EXISTING_RULE_POLICY', 'policy_hashes': {
            p: h for p, h in identity['code'].items() if p.startswith('config/')}},
        'runtime_identity': identity, 'fallback_mode': 'RULE_ONLY',
        'validation_scope': 'BASELINE_CONFIGURATION_EQUIVALENCE_ONLY'}
    return json.loads(json.dumps(config, allow_nan=False))


def verify_profile(profile, *, current=False, pinned=False):
    if digest(profile.config_json) != profile.config_digest or profile.score_unit != 'p119_rule_v1':
        raise HTTPException(409, 'Matching profile integrity mismatch')
    if (profile.config_schema_version != profile.config_json.get('config_schema_version') or
            profile.feature_schema_identity != profile.config_json.get('feature_schema_identity')):
        raise HTTPException(409, 'Matching profile schema identity mismatch')
    hybrid = profile.config_json.get('kind') == 'RULE_HYBRID_DETERMINISTIC'
    advanced = hybrid or profile.config_json.get('kind') == 'RULE_WITH_EMBEDDING_EVIDENCE'
    if advanced:
        if profile.encoder_identity != profile.config_json.get('encoder_identity'):
            raise HTTPException(409, 'Encoder identity differs from immutable profile')
    elif profile.config_json.get('kind') != 'RULE_ONLY' or profile.encoder_identity is not None:
        raise HTTPException(409, 'Unsupported matching profile contract')
    expected = None
    if current:
        expected = baseline_config(profile.config_json['engine_flags'] if pinned else None)
        if advanced:
            from masterone.mdm.matching_agents.embedding_runtime import advanced_config
            if hybrid:
                from masterone.mdm.matching_agents.integration_profile import integration_config
                expected = integration_config(expected, profile.config_json['experiment'], profile.config_json['fallback_mode'],
                    global_lexical=profile.config_json.get('retrieval', {}).get('global_lexical', False),
                    person_recovery=profile.config_json.get('retrieval', {}).get('person_recovery', False))
            else:
                expected = advanced_config(expected, profile.config_json['fallback_mode'])
            if pinned:
                expected['assessment_auto_enabled'] = profile.config_json['assessment_auto_enabled']
    if current and profile.config_json != expected:
        raise HTTPException(409, 'Pinned baseline configuration differs from runtime')


def create_profile(db, actor, request):
    authorize(db, actor, 'learn_match_profile')
    config = baseline_config()
    if request.integration:
        require_flag('advanced_matching_integration_enabled')
        require_flag('advanced_matching_agents_enabled')
        from masterone.mdm.matching_agents.integration_profile import integration_config
        config = integration_config(config, request.integration_experiment, request.fallback_mode,
                                    global_lexical=request.global_lexical, person_recovery=request.person_recovery)
    elif request.advanced_agents:
        require_flag('advanced_matching_agents_enabled')
        from masterone.mdm.matching_agents.embedding_runtime import advanced_config
        config = advanced_config(config, request.fallback_mode)
    if request.config is not None and request.config != config:
        raise HTTPException(422, 'M1 accepts only the existing rule configuration snapshot')
    profile = MatchingProfile(owner_user_id=actor, creator_id=actor, data_scope=request.data_scope,
        version=request.version, config_schema_version=config['config_schema_version'], config_json=config,
        config_digest=digest(config), score_unit=config['score_unit'], encoder_identity=config['encoder_identity'],
        feature_schema_identity=config['feature_schema_identity'])
    db.add(profile)
    db.flush()
    governance_audit(db, actor, profile.data_scope, 'MATCH_PROFILE_CREATED', 'MatchingProfile', profile.id,
                     details={'digest': profile.config_digest, 'version': profile.version})
    return profile


def transition_profile(db, actor, profile_id, expected_revision, target):
    authorize(db, actor, 'learn_match_profile' if target == 'VALIDATED' else 'apply_match_profile')
    profile = owned(db, MatchingProfile, profile_id, actor)
    if profile.lifecycle_revision != expected_revision:
        raise HTTPException(409, 'Stale profile revision')
    allowed = {'VALIDATED': {'CANDIDATE'}, 'RETIRED': {'CANDIDATE', 'VALIDATED', 'ACTIVE'}}
    if profile.lifecycle not in allowed[target]:
        raise HTTPException(409, 'Invalid profile lifecycle transition')
    verify_profile(profile, current=target == 'VALIDATED')
    if target == 'RETIRED' and db.query(MatchingProfileDefault).filter_by(profile_id=profile.id).first():
        raise HTTPException(409, 'Replace the default pointer before retiring its profile')
    profile.lifecycle = target
    profile.lifecycle_revision += 1
    governance_audit(db, actor, profile.data_scope,
        'MATCH_PROFILE_VALIDATED' if target == 'VALIDATED' else 'MATCH_PROFILE_RETIRED',
        'MatchingProfile', profile.id, details={'scope': profile.config_json['validation_scope'],
                                               'revision': profile.lifecycle_revision})
    return profile


def apply_default(db, actor, scope, request):
    authorize(db, actor, 'apply_match_profile')
    profile = owned(db, MatchingProfile, request.profile_id, actor)
    if profile.data_scope != scope or profile.lifecycle not in {'VALIDATED', 'ACTIVE'}:
        raise HTTPException(409, 'Profile is not validated for the requested scope')
    verify_profile(profile, current=True)
    pointer = db.get(MatchingProfileDefault, (actor, scope))
    if (pointer.revision if pointer else 0) != request.expected_revision:
        raise HTTPException(409, 'Stale default revision')
    if pointer is None:
        pointer = MatchingProfileDefault(owner_user_id=actor, data_scope=scope,
            profile_id=profile.id, revision=1, changed_by=actor)
        db.add(pointer)
    else:
        pointer.profile_id, pointer.revision = profile.id, pointer.revision + 1
        pointer.changed_by, pointer.changed_at = actor, now_utc()
    if profile.lifecycle != 'ACTIVE':
        profile.lifecycle, profile.lifecycle_revision = 'ACTIVE', profile.lifecycle_revision + 1
    governance_audit(db, actor, scope, 'MATCH_PROFILE_APPLIED', 'MatchingProfile', profile.id,
        details={'default_revision': pointer.revision, 'digest': profile.config_digest, 'new_runs_only': True})
    return pointer


def latest_binding(db, run_id, *, operation_id=None, include_pending=False):
    from sqlalchemy import or_

    from masterone.mdm.workflow_models import WorkflowOperation
    query = db.query(MatchingBinding).filter_by(run_id=run_id)
    if not include_pending:
        query = query.join(WorkflowOperation, MatchingBinding.originating_operation_id == WorkflowOperation.id).filter(
            or_(WorkflowOperation.operation_type != 'REBUILD_CANDIDATES',
                WorkflowOperation.status.in_(('COMPLETED', 'DEGRADED')),
                WorkflowOperation.id == operation_id if operation_id else False))
    return query.order_by(MatchingBinding.revision.desc()).first()


def bind_new_run(db, run, op):
    if not get_settings().matching_profile_registry_enabled:
        return
    authorize(db, run.owner_user_id, 'create_run', owner=run.owner_user_id)
    pointer = db.get(MatchingProfileDefault, (run.owner_user_id, SCOPE))
    if pointer is None:
        return  # No implicit profile/backfill; existing rule path remains available.
    profile = owned(db, MatchingProfile, pointer.profile_id, run.owner_user_id)
    if profile.lifecycle != 'ACTIVE':
        raise HTTPException(409, 'Default profile is not active')
    verify_profile(profile, current=True)
    selection = db.get(ResolutionEngineSelection, run.id)
    if selection is not None:
        raise HTTPException(409, 'Run already has an engine selection')
    db.add(ResolutionEngineSelection(run_id=run.id,
        engine_version='p119_person_v2' if get_settings().mdm_engine_v2_enabled else 'p119_current',
        configuration=deepcopy(profile.config_json['engine_flags'])))
    binding = MatchingBinding(owner_user_id=run.owner_user_id, creator_id=run.owner_user_id,
        data_scope=SCOPE, run_id=run.id, revision=1, profile_id=profile.id,
        profile_digest=profile.config_digest, binding_kind='CONFIG_PINNED',
        originating_operation_id=op.id, config_identity=digest(profile.config_json['runtime_identity']))
    db.add(binding)
    db.flush()
    op.payload = {**op.payload, 'matching_binding_id': binding.id, 'matching_binding_revision': binding.revision,
                  'matching_profile_digest': binding.profile_digest}
    op.payload_hash = digest(op.payload)
    governance_audit(db, run.owner_user_id, SCOPE, 'MATCH_PROFILE_BOUND', 'MatchingBinding', binding.id,
                     run_id=run.id, details={'profile_id': profile.id, 'profile_digest': binding.profile_digest})


def pin_operation_binding(db, run, payload):
    if not get_settings().matching_profile_registry_enabled:
        from masterone.mdm.workflow_models import WorkflowOperation
        prepared = db.query(WorkflowOperation).filter_by(run_id=run.id, operation_type='PREPARE').first()
        if prepared is None or not prepared.payload.get('matching_binding_id'):
            return payload
    binding = latest_binding(db, run.id)
    if binding is None:
        return payload
    return {**payload, 'matching_binding_id': binding.id, 'matching_binding_revision': binding.revision,
            'matching_profile_digest': binding.profile_digest}


def verify_operation_binding(db, run, op):
    identifier = op.payload.get('matching_binding_id')
    if identifier is None:
        # Only query new tables when enabled; unmigrated/legacy flags-off path has no new reads.
        if get_settings().matching_profile_registry_enabled and latest_binding(db, run.id):
            raise ValueError('MATCHING_BINDING_MISSING')
        return None
    binding = db.get(MatchingBinding, identifier)
    if (binding is None or binding.run_id != run.id or binding.owner_user_id != run.owner_user_id or
            binding.revision != op.payload.get('matching_binding_revision') or
            binding.profile_digest != op.payload.get('matching_profile_digest')):
        raise ValueError('MATCHING_BINDING_IDENTITY_CHANGED')
    profile = db.get(MatchingProfile, binding.profile_id)
    if (profile is None or profile.owner_user_id != run.owner_user_id or profile.data_scope != binding.data_scope or
            profile.config_digest != binding.profile_digest):
        raise ValueError('MATCHING_PROFILE_IDENTITY_CHANGED')
    verify_profile(profile, current=True, pinned=True)
    selection = db.get(ResolutionEngineSelection, run.id)
    if selection is None or selection.configuration != profile.config_json['engine_flags']:
        raise ValueError('MATCHING_ENGINE_PROFILE_MISMATCH')
    latest = latest_binding(db, run.id, operation_id=op.id)
    if latest.id != binding.id and (latest.originating_operation_id != op.id or latest.profile_digest != binding.profile_digest):
        raise ValueError('MATCHING_BINDING_REVISION_CHANGED')
    return binding


def append_binding_stage(db, run, op, kind, artifact_id, *, assessment_id=None, input_hash=None):
    binding = verify_operation_binding(db, run, op)
    if binding is None:
        return
    parent = latest_binding(db, run.id, operation_id=op.id)
    if parent.originating_operation_id == op.id and parent.binding_kind == kind:
        return
    row = MatchingBinding(owner_user_id=parent.owner_user_id, creator_id=op.actor_user_id,
        data_scope=parent.data_scope, run_id=run.id, revision=latest_binding(db, run.id, include_pending=True).revision + 1,
        profile_id=parent.profile_id, profile_digest=parent.profile_digest, binding_kind=kind,
        parent_binding_id=parent.id, originating_operation_id=op.id,
        prepare_artifact_id=artifact_id if kind == 'PREPARED' else parent.prepare_artifact_id,
        candidate_artifact_id=artifact_id if kind == 'ASSESSED' else parent.candidate_artifact_id,
        assessment_id=assessment_id, input_snapshot_hash=input_hash or parent.input_snapshot_hash,
        config_identity=parent.config_identity)
    db.add(row)
    db.flush()
    governance_audit(db, run.owner_user_id, row.data_scope, 'MATCH_PROFILE_BOUND', 'MatchingBinding', row.id,
        run_id=run.id, details={'revision': row.revision, 'parent_binding_id': parent.id, 'kind': kind})


def safe_artifact_uri(value, owner):
    # Registry references are opaque owned keys; no arbitrary URL, absolute path or file access.
    path = PurePosixPath(value)
    if '\\' in value or ':' in value or path.is_absolute() or '..' in path.parts or not value.startswith(owner + '/'):
        raise HTTPException(422, 'Artifact reference must be an owner-scoped relative storage key')
    return value
