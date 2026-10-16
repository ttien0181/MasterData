"""Explicit retained-universe rebuild, immutable history and fenced publication."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

from masterone.mdm.engine_adapter import engine_context
from masterone.mdm.generation_staging import config_identity, digest
from masterone.mdm.matching_foundation_models import MatchingBinding, MatchingProfile
from masterone.mdm.matching_foundation_service import (
    append_binding_stage,
    authorize,
    governance_audit,
    latest_binding,
    owned,
    require_flag,
    verify_profile,
)
from masterone.mdm.models import ResolutionEngineSelection, ResolutionRecord
from masterone.mdm.pipeline.decision_semantics import load_cannot_links
from masterone.mdm.pipeline.golden_v2 import record_payload
from masterone.mdm.workflow_artifacts import load, seal
from masterone.mdm.workflow_models import WorkflowPair


def request_rebuild(db, run_id, request, actor):
    from masterone.mdm.workflow_service import (
        active_operation,
        audit,
        check_revision,
        conflict,
        enabled_run,
        enqueue,
        new_operation,
        operation_read,
        replay,
    )
    require_flag('advanced_matching_integration_enabled')
    authorize(db, actor, 'apply_match_profile')
    run = enabled_run(db, run_id, actor)
    payload = request.model_dump()
    prior = replay(db, run_id, 'REBUILD_CANDIDATES', request.request_id, payload)
    if prior:
        return operation_read(prior)
    check_revision(run, payload)
    if active_operation(db, run_id):
        conflict('ACTIVE_OPERATION')
    if run.workflow_state != 'DECIDE_REVIEW':
        conflict('WORKFLOW_INVALID_STATE')
    profile = owned(db, MatchingProfile, request.profile_id, actor)
    verify_profile(profile, current=True, pinned=True)
    if profile.lifecycle not in ('VALIDATED', 'ACTIVE') or profile.config_json['kind'] != 'RULE_HYBRID_DETERMINISTIC':
        conflict('REBUILD_PROFILE_NOT_VALIDATED')
    selection = db.get(ResolutionEngineSelection, run.id)
    if selection is None or selection.configuration != profile.config_json['engine_flags']:
        conflict('REBUILD_NORMALIZATION_PROFILE_CHANGED')
    plan = digest({'profile_digest': profile.config_digest, 'budget': profile.config_json['execution_budget']})
    if plan != request.workload_plan_hash:
        conflict('REBUILD_WORKLOAD_PLAN_CHANGED')
    parent = latest_binding(db, run.id)
    if parent is None or not parent.prepare_artifact_id:
        conflict('REBUILD_PREPARE_BINDING_REQUIRED')
    op = new_operation(db, run, 'REBUILD_CANDIDATES', request.request_id, payload, actor)
    # Existing CONFIG_PINNED kind supports an explicit new profile revision without DDL.
    binding = MatchingBinding(owner_user_id=actor, creator_id=actor, data_scope=profile.data_scope,
        run_id=run.id, revision=latest_binding(db, run.id, include_pending=True).revision + 1, profile_id=profile.id, profile_digest=profile.config_digest,
        binding_kind='CONFIG_PINNED', parent_binding_id=parent.id, originating_operation_id=op.id,
        prepare_artifact_id=parent.prepare_artifact_id, candidate_artifact_id=parent.candidate_artifact_id,
        input_snapshot_hash=parent.input_snapshot_hash, config_identity=digest(profile.config_json['runtime_identity']))
    db.add(binding)
    db.flush()
    op.payload = {**op.payload, 'matching_binding_id': binding.id, 'matching_binding_revision': binding.revision,
                  'matching_profile_digest': binding.profile_digest}
    enqueue(db, run, op)
    run.workflow_revision += 1
    audit(db, run, actor, 'WORKFLOW_REBUILD_CANDIDATES', {'operation_id': op.id, 'parent_binding_id': parent.id})
    governance_audit(db, actor, profile.data_scope, 'MATCH_PROFILE_BOUND', 'MatchingBinding', binding.id,
                     run_id=run.id, details={'revision': binding.revision, 'explicit_rebuild': True})
    db.commit()
    return operation_read(op)


def run_rebuild(operation_id, factory, progress):
    from masterone.mdm.matching_agents.candidate_union import build_union
    from masterone.mdm.matching_agents.workflow_agents import (
        config_for_operation,
        coordinated_payload,
        run_agents,
        seal_agent,
        vectors_for_operation,
    )
    from masterone.mdm.pipeline.stage_runner import StageRunner
    from masterone.mdm.workflow_assessment import compute_assessment, publish_assessment
    from masterone.mdm.workflow_jobs import finish, job_identity
    with factory.begin() as db:
        run, op, _ = job_identity(db, operation_id)
        config = config_for_operation(db, run, op)
        if config is None or config['kind'] != 'RULE_HYBRID_DETERMINISTIC':
            raise ValueError('REBUILD_PROFILE_IDENTITY_MISMATCH')
        run_id, revision, decisions, policy = run.id, run.workflow_revision, run.decision_revision, run.policy_version
        if decisions != op.payload['expected_decision_revision']:
            raise ValueError('REBUILD_REVISION_CHANGED')
        binding = latest_binding(db, run.id, operation_id=op.id)
        prepared = load(db, binding.prepare_artifact_id, run.id, 'PREPARE')
        current = [record_payload(r) for r in db.query(ResolutionRecord).filter_by(run_id=run.id).order_by(ResolutionRecord.id)]
        records = prepared['records']
        if digest(current) != prepared['input_hash']:
            raise ValueError('REBUILD_INPUT_CHANGED')
        originals = [deepcopy(p.baseline) for p in db.query(WorkflowPair).filter_by(run_id=run.id).order_by(WorkflowPair.id)]
        by_key = {tuple(sorted((p['left_id'], p['right_id']))): p for p in originals}
        links = load_cannot_links(db, run.id)
        input_hash, links_hash = digest(records), digest(sorted(links))
    with engine_context(config['engine_flags']), TemporaryDirectory(prefix='workflow-rebuild-') as directory:
        if prepared['profile_identity'] != digest(config['engine_flags']) or prepared['execution_identity'] != digest(config_identity(policy)):
            raise ValueError('REBUILD_PREPARE_IDENTITY_CHANGED')
        with_agents = config['experiment'] in ('E2', 'E3')
        vectors = vectors_for_operation(factory, run_id, operation_id, records, progress) if with_agents else None
        union = build_union(records, config, vectors, originals, progress)
        provenance_id = seal_agent(factory, run_id, operation_id, 'CANDIDATE_PROVENANCE',
            {'observations': union.metrics.pop('provenance'), 'config_digest': digest(config)})
        union.metrics['provenance_artifact_id'] = provenance_id
        runner = StageRunner(run_id, operation_id, output_dir=Path(directory))
        try:
            output = runner.run_decide(records, policy, links, integration_candidates=union)
            scored = list(output['pairs']) + output['insufficient_pairs']
            baselines = [by_key.get(tuple(sorted((p['left_id'], p['right_id']))), p) for p in scored]
            payload = {key: output[key] for key in ('candidate_count', 'candidate_metrics', 'scoring_metrics', 'insufficient_count')}
            payload.update(pairs=baselines, input_hash=input_hash, profile_digest=digest(config),
                           contract_version='candidate_generation_v1', retained_history=len(originals))
        finally:
            if 'output' in locals() and hasattr(output['pairs'], 'close'):
                output['pairs'].close()
            runner.close_snapshot()
            runner.con.close()
        artifact_id = seal_agent(factory, run_id, operation_id, 'CANDIDATE_GENERATION', payload)
        assessed = compute_assessment(records, baselines, config['engine_flags'], links, progress)
        agents = None
        if with_agents:
            agents = run_agents(factory, run_id, operation_id, records, baselines, output['scoring_metrics'], progress,
                                source_artifact_id=artifact_id, effective_pairs=assessed['pairs'])
            assessed = coordinated_payload(factory, run_id, assessed, agents, config)
        assessment_artifact = seal(factory, run_id, 'PAIR_ASSESSMENT', assessed)
    with factory.begin() as db:
        run, op, task = job_identity(db, operation_id)
        if (run.workflow_revision, run.decision_revision) != (revision, decisions):
            raise ValueError('REBUILD_REVISION_CHANGED')
        current = [record_payload(r) for r in db.query(ResolutionRecord).filter_by(run_id=run.id).order_by(ResolutionRecord.id)]
        if digest(current) != input_hash or digest(sorted(load_cannot_links(db, run.id))) != links_hash:
            raise ValueError('REBUILD_INPUT_CHANGED')
        for pair in baselines:
            if db.get(WorkflowPair, pair['id']) is None:
                db.add(WorkflowPair(id=pair['id'], run_id=run.id, baseline=pair))
        db.flush()
        op.artifact_id = artifact_id
        assessment = publish_assessment(db, run, op, assessed, assessment_artifact)
        op.result = {**op.result, 'matching_agents': agents, 'candidate_generation_id': artifact_id,
                     'candidate_count': payload['candidate_count'], 'retrieved_current': union.metrics['retrieved_current'],
                     'retained_history': len(originals)}
        run.candidate_count = payload['candidate_count']
        append_binding_stage(db, run, op, 'ASSESSED', artifact_id, assessment_id=assessment.id, input_hash=input_hash)
        finish(db, run, op, task, 'DECIDE_REVIEW')
