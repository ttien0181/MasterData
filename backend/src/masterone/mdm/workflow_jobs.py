"""Workers compute outside the run-lock transaction, then publish under a fence."""
from pathlib import Path
from tempfile import TemporaryDirectory

from masterone.mdm.engine_adapter import engine_context, select_engine
from masterone.mdm.generation_staging import config_identity, digest
from masterone.mdm.job_queue import completed, current_claim, guard
from masterone.mdm.models import ResolutionRecord, ResolutionTask
from masterone.mdm.pipeline.golden_v2 import record_payload
from masterone.mdm.workflow_artifacts import load, seal
from masterone.mdm.workflow_models import WorkflowArtifact, WorkflowOperation, WorkflowPair
from masterone.mdm.matching_agents.execution_budget import integration_job
from masterone.mdm.workflow_telemetry import measured_operation, record_timing, timed_stage


def authorize_job(db, run, op):
    from masterone.auth.models import User
    from masterone.auth.security import ROLE_CAPABILITIES
    from masterone.config import get_settings
    actor = db.query(User).filter_by(id=op.actor_user_id).with_for_update().populate_existing().one_or_none()
    required = {'PREPARE': {'create_run'}, 'DECIDE': {'start_run'},
                'BULK_DECISIONS': {'approve_pair' if op.payload.get('decision') == 'merge' else 'reject_pair'},
                'CLUSTER': {'approve_pair'}, 'CLUSTER_PREVIEW': {'approve_pair'}, 'GOLDEN_RESOLVE': {'edit_golden'},
                'ASSESS_PAIRS': {'approve_pair'}, 'PAIR_ADVICE': {'approve_pair'}, 'GROUP_ANNOTATIONS': {'approve_pair'},
                'AUTOMATION_SHADOW': {'approve_pair'}, 'AUTOMATION_APPLY': {'approve_pair'},
                'FINALIZE_GOLDEN': {'edit_golden'}, 'REBUILD_CANDIDATES': {'apply_match_profile'}}[op.operation_type]
    if op.operation_type == 'CLUSTER' and op.payload.get('repair_operation_id'):
        required |= {'reject_pair', 'edit_golden'}
    if op.operation_type == 'CLUSTER' and op.payload.get('structure_intent'):
        required |= {'manage_entity_structure', 'reject_pair', 'edit_golden'}
        if not get_settings().entity_structure_enabled:
            raise ValueError('STRUCTURE_DISABLED')
    if (not get_settings().session_workflow_enabled or run.archived_at is not None or
            actor is None or not actor.is_active or run.owner_user_id != actor.id or
            not required <= ROLE_CAPABILITIES.get(actor.role, frozenset())):
        raise ValueError('WORKFLOW_AUTHORIZATION_CHANGED')
    from masterone.mdm.matching_foundation_service import verify_operation_binding
    verify_operation_binding(db, run, op)


def job_identity(db, op_id):
    op = db.get(WorkflowOperation, op_id)
    if op is None:
        raise ValueError('WORKFLOW_OPERATION_NOT_FOUND')
    guard(db, op.run_id)
    from masterone.mdm.generation_staging import lock_run
    run = lock_run(db, op.run_id)
    db.refresh(run)
    task = db.query(ResolutionTask).filter_by(run_id=run.id).one()
    if run.workflow_version != 2 or task.operation_id != op.id or op.status not in ('RUNNING', 'QUEUED'):
        raise ValueError('WORKFLOW_OPERATION_IDENTITY')
    authorize_job(db, run, op)
    return run, op, task


def finish(db, run, op, task, state):
    authorize_job(db, run, op)
    db.flush()  # Flush publication rows before the last DB-clock ownership check.
    from masterone.mdm.matching_agents.execution_budget import check_publication
    check_publication(db)
    record_timing(db, op)
    completed(db, run.id)
    from masterone.mdm.matching_agents.workflow_agents import latest_result
    evidence = op.result.get('matching_agents') or latest_result(db, run.id)
    degraded = bool(evidence and evidence['status'] == 'DEGRADED')
    matching_operation_degraded = bool(op.result.get('matching_agents') and degraded)
    status, phase = ('DEGRADED', 'degraded') if matching_operation_degraded else ('COMPLETED', 'completed')
    op.status, op.stage = status, phase
    task.status, task.phase = status, phase
    run.status, run.phase = ('DEGRADED', 'degraded') if degraded else (status, phase)
    if matching_operation_degraded:
        op.error_code = task.error_code = 'MATCHING_AGENTS_DEGRADED'
    run.workflow_state = state
    run.workflow_revision += 1
    from masterone.mdm.workflow_processing import continue_processing
    continue_processing(db, run, op)


@measured_operation
@integration_job
def execute_workflow(operation_id, factory, progress):
    with factory.begin() as db:
        run, op, task = job_identity(db, operation_id)
        run_id, kind, revision, policy = run.id, op.operation_type, run.workflow_revision, run.policy_version
        agents_config = None
        if op.payload.get('matching_binding_id'):
            from masterone.mdm.matching_agents.workflow_agents import config_for_operation
            agents_config = config_for_operation(db, run, op)
    if kind == 'BULK_DECISIONS':
        from masterone.mdm.workflow_bulk import run_bulk_decisions
        return run_bulk_decisions(operation_id, factory, progress)
    if kind == 'CLUSTER':
        from masterone.mdm.workflow_finalize import run_cluster
        return run_cluster(operation_id, factory, progress)
    if kind == 'CLUSTER_PREVIEW':
        from masterone.mdm.workflow_cluster_preview import run_preview
        return run_preview(operation_id, factory, progress)
    if kind == 'GOLDEN_RESOLVE':
        from masterone.mdm.workflow_resolution import run_resolution
        return run_resolution(operation_id, factory, progress)
    if kind == 'FINALIZE_GOLDEN':
        from masterone.mdm.workflow_finalization import run_finalization
        return run_finalization(operation_id, factory, progress)
    if kind == 'ASSESS_PAIRS':
        from masterone.mdm.workflow_assessment import run_assessment
        return run_assessment(operation_id, factory, progress)
    if kind == 'REBUILD_CANDIDATES':
        from masterone.mdm.workflow_candidate_rebuild import run_rebuild
        return run_rebuild(operation_id, factory, progress)
    if kind == 'PAIR_ADVICE':
        from masterone.mdm.workflow_pair_advice import run_pair_advice
        return run_pair_advice(operation_id, factory, progress)
    if kind == 'GROUP_ANNOTATIONS':
        from masterone.mdm.workflow_group_annotations import run_group_annotations
        return run_group_annotations(operation_id, factory, progress)
    if kind in {'AUTOMATION_SHADOW','AUTOMATION_APPLY'}:
        from masterone.mdm.workflow_automation import run_apply, run_shadow
        return (run_shadow if kind=='AUTOMATION_SHADOW' else run_apply)(operation_id,factory,progress)
    profile = select_engine(factory, run_id)
    with engine_context(profile), TemporaryDirectory(prefix='workflow-compute-') as directory:
        execution_identity = digest(config_identity(policy))
        from masterone.mdm.pipeline.decision_semantics import load_cannot_links
        from masterone.mdm.pipeline.stage_runner import StageRunner
        with factory() as db:
            if kind == 'PREPARE':
                query = db.query(ResolutionRecord).filter_by(run_id=run_id).order_by(ResolutionRecord.id)
                record_count = query.count()
                # Count actual read/prepare units plus the final publication. The
                # last unit is completed only when DATA_READY commits.
                prepare_total = 2 * record_count + 1
                progress('prepare-read', 0, prepare_total)
                records = []
                for row in query.yield_per(512):
                    records.append(record_payload(row))
                    if len(records) % 512 == 0:
                        progress('prepare-read', len(records), prepare_total)
                progress('prepare-read', len(records), prepare_total)
            else:
                artifact = db.query(WorkflowArtifact).filter_by(run_id=run_id, phase='PREPARE').order_by(WorkflowArtifact.created_at.desc()).first()
                prepared = load(db, artifact.id if artifact else None, run_id, 'PREPARE')
                if prepared['profile_identity'] != digest(profile):
                    raise ValueError('WORKFLOW_PROFILE_CHANGED')
                if prepared.get('execution_identity') != execution_identity:
                    raise ValueError('WORKFLOW_CONFIG_CHANGED')
                records = prepared['records']
            negative_pairs = load_cannot_links(db, run_id)
        if kind != 'PREPARE':
            progress(kind.lower(), 0, len(records))
        runner = StageRunner(run_id, current_claim().attempt_id if current_claim() else operation_id, output_dir=Path(directory))
        try:
            if kind == 'PREPARE':
                from masterone.mdm.data_profile import build_profile
                prepared_records = []
                for start in range(0, len(records), 512):
                    prepared_records.extend(runner.run_prepare(records[start:start + 512]))
                    progress('prepare-normalize', record_count + len(prepared_records), prepare_total)
                records = prepared_records
                payload = {'records': records, 'profile': build_profile(records), 'execution_identity': execution_identity,
                           'input_hash': digest(records), 'profile_identity': digest(profile), 'policy_version': policy}
            elif kind == 'DECIDE':
                from masterone.mdm.pipeline.checkpoints import DatabasePartitionStore
                store = DatabasePartitionStore(factory, run_id, current_claim())
                from masterone.mdm.decision_progress import track_decision, candidate_progress
                with track_decision(progress):
                    candidate_progress(0, force=True)
                    integration_candidates = None
                    if agents_config and agents_config['kind'] == 'RULE_HYBRID_DETERMINISTIC':
                        from masterone.mdm.matching_agents.candidate_union import build_union
                        from masterone.mdm.matching_agents.workflow_agents import vectors_for_operation, seal_agent
                        with timed_stage('embedding-retrieval'):
                            vectors = vectors_for_operation(factory, run_id, operation_id, records, progress) if agents_config['experiment'] in ('E2', 'E3') else None
                        with timed_stage('candidate-retrieval'):
                            integration_candidates = build_union(records, agents_config, vectors, [], progress)
                        provenance = integration_candidates.metrics.pop('provenance')
                        provenance_id = seal_agent(factory, run_id, operation_id, 'CANDIDATE_PROVENANCE',
                                                   {'observations': provenance, 'config_digest': digest(agents_config)})
                        integration_candidates.metrics['provenance_artifact_id'] = provenance_id
                    with timed_stage('candidate-rule-scoring'):
                        output = runner.run_decide(records, policy, negative_pairs, store=store, factory=factory,
                                                   integration_candidates=integration_candidates)
                try:
                    payload = {k: output[k] for k in ('candidate_count', 'insufficient_count', 'candidate_metrics', 'scoring_metrics', 'streaming')}
                    payload['pairs'] = list(output['pairs']) + output['insufficient_pairs']
                    payload['input_hash'], payload['profile_identity'], payload['policy_version'] = digest(records), digest(profile), policy
                    payload['execution_identity'] = execution_identity
                finally:
                    if hasattr(output['pairs'], 'close'):
                        output['pairs'].close()
            else:
                raise ValueError('WORKFLOW_OPERATION_TYPE')
        finally:
            runner.close_snapshot()
            runner.con.close()
        if kind == 'DECIDE':
            progress('seal-decisions', payload['candidate_count'], payload['candidate_count'])
        artifact_id = seal(factory, run_id, kind, payload)
        if kind == 'PREPARE':
            progress('prepare-publish', 2 * record_count, prepare_total)
        assessed_payload, assessed_artifact = None, None
        if kind == 'DECIDE':
            from masterone.config import get_settings
            if (op.payload.get('processing') or
                    (agents_config['assessment_auto_enabled'] if agents_config else get_settings().pair_assessment_auto_enabled)):
                from masterone.mdm.workflow_assessment import compute_assessment
                assessed_payload = compute_assessment(records, payload['pairs'], profile, negative_pairs, progress,
                                                      op.payload.get('model_scope'))
        agents_result = None
        if (kind == 'DECIDE' and op.payload.get('matching_binding_id')
                and not (agents_config and agents_config.get('experiment') in ('E0', 'E1'))):
            from masterone.mdm.matching_agents.workflow_agents import run_agents
            with timed_stage('embedding-evidence'):
                agents_result = run_agents(factory, run_id, operation_id, records, payload['pairs'],
                    payload['scoring_metrics'], progress, source_artifact_id=artifact_id,
                    effective_pairs=assessed_payload['pairs'] if assessed_payload else None,
                    skip_advisory=bool(op.payload.get('demo_fast_mode')))
            if agents_result and agents_config['kind'] == 'RULE_HYBRID_DETERMINISTIC':
                from masterone.mdm.matching_agents.workflow_agents import coordinated_payload
                if assessed_payload is None:
                    from masterone.mdm.workflow_assessment import compute_assessment
                    assessed_payload = compute_assessment(records, payload['pairs'], profile, negative_pairs, progress)
                with timed_stage('decision-coordinator'):
                    assessed_payload = coordinated_payload(factory, run_id, assessed_payload, agents_result, agents_config)
        # Seal once after the coordinator has produced the final assessment.
        # The intermediate assessment is never published or read by run_agents.
        if assessed_payload is not None:
            progress('seal-assessment', payload['candidate_count'], payload['candidate_count'])
            assessed_artifact = seal(factory, run_id, 'PAIR_ASSESSMENT', assessed_payload)
    if kind == 'DECIDE':
        progress('publish-decisions', 0, payload['candidate_count'])
    with factory.begin() as db:
        run, op, task = job_identity(db, operation_id)
        if run.workflow_revision != revision:
            raise ValueError('WORKFLOW_REVISION_CHANGED')
        with engine_context(profile):
            if execution_identity != digest(config_identity(policy)):
                raise ValueError('WORKFLOW_CONFIG_CHANGED')
        op.artifact_id = artifact_id
        if kind == 'DECIDE':
            run.candidate_count, run.insufficient_count = payload['candidate_count'], payload['insufficient_count']
            if assessed_payload:
                from masterone.mdm.workflow_assessment import publish_initial_assessment
                publish_initial_assessment(db, run, op, payload['pairs'], assessed_payload, assessed_artifact)
            else:
                for start in range(0, len(payload['pairs']), 1000):
                    db.execute(WorkflowPair.__table__.insert(), [
                        {'id': pair['id'], 'run_id': run_id, 'baseline': pair}
                        for pair in payload['pairs'][start:start + 1000]])
            if agents_result:
                op.result = {**op.result, 'matching_agents': agents_result}
        if op.payload.get('matching_binding_id'):
            from masterone.mdm.matching_foundation_service import append_binding_stage
            from masterone.mdm.workflow_assessment_reads import active_assessment
            current_assessment = active_assessment(db, run.id) if kind == 'DECIDE' else None
            append_binding_stage(db, run, op, 'PREPARED' if kind == 'PREPARE' else 'ASSESSED', artifact_id,
                assessment_id=current_assessment.id if current_assessment else None, input_hash=payload.get('input_hash'))
        if kind == 'PREPARE':
            op.progress = {'processed': prepare_total, 'total': prepare_total}
            task.processed_count = task.total_count = prepare_total
        elif kind == 'DECIDE':
            count = payload['candidate_count']
            op.progress = {**op.progress, 'processed': count, 'total': count,
                           'candidate_count': count, 'classified_count': count, 'classification_started': True}
            task.processed_count = task.total_count = count
        finish(db, run, op, task, 'DATA_READY' if kind == 'PREPARE' else 'DECIDE_REVIEW')
