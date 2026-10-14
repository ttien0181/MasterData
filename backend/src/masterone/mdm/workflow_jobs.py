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
                'FINALIZE_GOLDEN': {'edit_golden'}}[op.operation_type]
    if op.operation_type == 'CLUSTER' and op.payload.get('repair_operation_id'):
        required |= {'reject_pair', 'edit_golden'}
    if (not get_settings().session_workflow_enabled or run.archived_at is not None or
            actor is None or not actor.is_active or run.owner_user_id != actor.id or
            not required <= ROLE_CAPABILITIES.get(actor.role, frozenset())):
        raise ValueError('WORKFLOW_AUTHORIZATION_CHANGED')


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
    completed(db, run.id)
    op.status, op.stage = 'COMPLETED', 'completed'
    task.status, task.phase, run.status, run.phase = 'COMPLETED', 'completed', 'COMPLETED', 'completed'
    run.workflow_state = state
    run.workflow_revision += 1


def execute_workflow(operation_id, factory, progress):
    with factory.begin() as db:
        run, op, task = job_identity(db, operation_id)
        run_id, kind, revision, policy = run.id, op.operation_type, run.workflow_revision, run.policy_version
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
                records = [record_payload(r) for r in db.query(ResolutionRecord).filter_by(run_id=run_id).order_by(ResolutionRecord.id)]
            else:
                artifact = db.query(WorkflowArtifact).filter_by(run_id=run_id, phase='PREPARE').order_by(WorkflowArtifact.created_at.desc()).first()
                prepared = load(db, artifact.id if artifact else None, run_id, 'PREPARE')
                if prepared['profile_identity'] != digest(profile):
                    raise ValueError('WORKFLOW_PROFILE_CHANGED')
                if prepared.get('execution_identity') != execution_identity:
                    raise ValueError('WORKFLOW_CONFIG_CHANGED')
                records = prepared['records']
            negative_pairs = load_cannot_links(db, run_id)
        progress(kind.lower(), 0, len(records))
        runner = StageRunner(run_id, current_claim().attempt_id if current_claim() else operation_id, output_dir=Path(directory))
        try:
            if kind == 'PREPARE':
                from masterone.mdm.data_profile import build_profile
                records = runner.run_prepare(records)
                payload = {'records': records, 'profile': build_profile(records), 'execution_identity': execution_identity,
                           'input_hash': digest(records), 'profile_identity': digest(profile), 'policy_version': policy}
            elif kind == 'DECIDE':
                from masterone.mdm.pipeline.checkpoints import DatabasePartitionStore
                store = DatabasePartitionStore(factory, run_id, current_claim())
                output = runner.run_decide(records, policy, negative_pairs, store=store, factory=factory)
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
        artifact_id = seal(factory, run_id, kind, payload)
        assessed_payload, assessed_artifact = None, None
        if kind == 'DECIDE':
            from masterone.config import get_settings
            if get_settings().pair_assessment_auto_enabled:
                from masterone.mdm.workflow_assessment import compute_assessment
                assessed_payload = compute_assessment(records, payload['pairs'], profile, negative_pairs, progress)
                assessed_artifact = seal(factory, run_id, 'PAIR_ASSESSMENT', assessed_payload)
    with factory.begin() as db:
        run, op, task = job_identity(db, operation_id)
        if run.workflow_revision != revision:
            raise ValueError('WORKFLOW_REVISION_CHANGED')
        with engine_context(profile):
            if execution_identity != digest(config_identity(policy)):
                raise ValueError('WORKFLOW_CONFIG_CHANGED')
        op.artifact_id = artifact_id
        if kind == 'DECIDE':
            for pair in payload['pairs']:
                db.add(WorkflowPair(id=pair['id'], run_id=run_id, baseline=pair))
            run.candidate_count, run.insufficient_count = payload['candidate_count'], payload['insufficient_count']
            if assessed_payload:
                db.flush()
                from masterone.mdm.workflow_assessment import publish_assessment
                publish_assessment(db, run, op, assessed_payload, assessed_artifact)
        finish(db, run, op, task, 'DATA_READY' if kind == 'PREPARE' else 'DECIDE_REVIEW')
