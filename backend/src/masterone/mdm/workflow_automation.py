"""Certificate-bound, revision-fenced automation; never finalizes or exports."""
from sqlalchemy import func
from masterone.config import get_settings
from masterone.mdm.generation_staging import digest
from masterone.mdm.pipeline.pair_automation import VERSION,code_identity,load_certificate,plan_components
from masterone.mdm.workflow_artifacts import load,seal
from masterone.mdm.workflow_models import WorkflowOperation,WorkflowPair
from masterone.mdm.models import ResolutionRun
from masterone.mdm.workflow_service import (
    active_operation,audit,check_revision,conflict,enabled_run,enqueue,new_operation,operation_read,replay,
)


def stopped(db,run_id):
    return db.query(WorkflowOperation.id).filter_by(run_id=run_id,operation_type='AUTOMATION_STOP',status='COMPLETED').first() is not None


def spent_components(db,run_id):
    return db.query(func.coalesce(func.sum(WorkflowOperation.result['applied_components'].as_integer()),0)).filter_by(
        run_id=run_id,operation_type='AUTOMATION_APPLY',status='COMPLETED').scalar()


def runtime_gate(db,run_id,snapshot):
    settings=get_settings()
    _,gate=load_certificate(settings,snapshot['profile'],{r['source_system'] for r in snapshot['records']},
        db.get(ResolutionRun,run_id).owner_user_id)
    gate['reasons']=list(gate['reasons'])
    from masterone.mdm.matching_agents.workflow_agents import latest_result
    if latest_result(db, run_id):gate['reasons'].append('MATCHING_AGENTS_AUTOMATION_DISABLED_M2A')
    if not settings.pair_automation_enabled:gate['reasons'].append('AUTOMATION_DISABLED')
    if stopped(db,run_id):gate['reasons'].append('AUTOMATION_STOPPED')
    gate['eligible']=not gate['reasons']
    gate['remaining_component_budget']=max(0,settings.pair_automation_max_components_per_run-spent_components(db,run_id))
    if not gate['remaining_component_budget']:
        gate['reasons'].append('CANARY_BUDGET_EXHAUSTED');gate['eligible']=False
    return gate


def preview_plan(db,run_id,snapshot):
    gate=runtime_gate(db,run_id,snapshot)
    plan=plan_components(snapshot,limit=get_settings().pair_automation_max_components_per_run,
        certified_cohorts=set(gate['cohorts']) if gate['eligible'] else None)
    qualified=[c for c in plan['components'] if gate['eligible'] and c['cohort'] in gate['cohorts']]
    qualified=qualified[:gate['remaining_component_budget']]
    reasons=list(gate['reasons'])
    if gate['eligible'] and plan['eligible_components'] and not qualified:reasons.append('COHORT_NOT_CERTIFIED')
    if not plan['eligible_components']:reasons.append('NO_SAFE_COMPONENTS')
    return dict(components=plan['components'],candidate_components=plan['eligible_components'],
        code_identity=code_identity(),profile_identity=digest(snapshot['profile']),
        owner_user_id=db.get(ResolutionRun,run_id).owner_user_id,
        source_systems=sorted({r['source_system'] for r in snapshot['records']}),
        qualified_components=len(qualified),qualified_fingerprints=[c['fingerprint'] for c in qualified],
        applicable_pairs=sum(len(c['pair_ids']) for c in qualified),can_apply=bool(qualified),
        certificate_hash=gate.get('certificate_hash'),precision_target=gate['precision_target'],
        blocked_reasons=reasons,remaining_component_budget=gate['remaining_component_budget'],
        guard_rejections=plan['guard_rejections'],skipped_components=plan['skipped_components'],truncated=plan['truncated'],
        source_decision_revision=snapshot['decision_revision'],source_assessment_id=snapshot.get('assessment_id'))


def run_shadow(operation_id,factory,progress):
    from masterone.mdm.workflow_cluster_preview import operation_snapshot
    from masterone.mdm.workflow_finalize import validate_snapshot_current
    from masterone.mdm.workflow_jobs import finish,job_identity
    snapshot=operation_snapshot(operation_id,factory,progress)
    if not snapshot.get('assessment_id'):raise ValueError('AUTOMATION_ASSESSMENT_REQUIRED')
    with factory.begin() as db:
        run,op,_=job_identity(db,operation_id)
        revision,run_id,snapshot_id=run.workflow_revision,run.id,op.artifact_id
    progress('automation-shadow',0,len(snapshot['pairs']))
    with factory() as db:result=preview_plan(db,run_id,snapshot)
    result['decision_snapshot_id']=snapshot_id
    result['preview_token']=digest({'snapshot':digest(snapshot),'plan':result})
    artifact=seal(factory,run_id,'AUTOMATION_PLAN',result)
    with factory.begin() as db:
        run,op,task=job_identity(db,operation_id)
        if run.workflow_revision!=revision or run.decision_revision!=snapshot['decision_revision']:raise ValueError('AUTOMATION_PREVIEW_STALE')
        validate_snapshot_current(db,run,snapshot)
        op.result={**result,'plan_artifact_id':artifact}
        op.progress=dict(processed=len(snapshot['pairs']),total=len(snapshot['pairs']),unit='pairs')
        finish(db,run,op,task,'DECIDE_REVIEW')


def begin_apply(db,run_id,request,actor):
    payload=request.model_dump();run=enabled_run(db,run_id,actor)
    prior=replay(db,run_id,'AUTOMATION_APPLY',request.request_id,payload)
    if prior:return operation_read(prior)
    check_revision(run,payload)
    if run.workflow_state!='DECIDE_REVIEW' or active_operation(db,run_id):conflict('DECISIONS_LOCKED')
    preview=db.query(WorkflowOperation).filter_by(id=request.preview_operation_id,run_id=run_id,
        operation_type='AUTOMATION_SHADOW',status='COMPLETED').one_or_none()
    if preview is None or request.preview_token!=preview.result.get('preview_token'):conflict('AUTOMATION_PREVIEW_INVALID')
    plan=load(db,preview.result['plan_artifact_id'],run_id,'AUTOMATION_PLAN')
    if plan!={k:v for k,v in preview.result.items() if k!='plan_artifact_id'}:conflict('AUTOMATION_PREVIEW_INVALID')
    from masterone.mdm.workflow_assessment_reads import active_assessment
    assessment=active_assessment(db,run_id)
    if plan['source_decision_revision']!=run.decision_revision or not assessment or plan['source_assessment_id']!=assessment.id:conflict('AUTOMATION_PREVIEW_STALE')
    snapshot=load(db,plan['decision_snapshot_id'],run_id,'DECISION_SNAPSHOT')
    gate=runtime_gate(db,run_id,snapshot)
    if not plan['can_apply'] or not gate['eligible'] or gate.get('certificate_hash')!=plan['certificate_hash']:
        conflict('AUTOMATION_NOT_CERTIFIED',reasons=gate['reasons'] or plan['blocked_reasons'])
    op=new_operation(db,run,'AUTOMATION_APPLY',request.request_id,payload,actor)
    op.artifact_id=plan['decision_snapshot_id'];op.result={'plan_artifact_id':preview.result['plan_artifact_id']}
    enqueue(db,run,op);run.workflow_revision+=1
    audit(db,run,actor,'WORKFLOW_AUTOMATION_QUEUED',{'operation_id':op.id,'preview_operation_id':preview.id,'certificate_hash':plan['certificate_hash']})
    db.commit();return operation_read(op)


def run_apply(operation_id,factory,progress):
    from masterone.mdm.workflow_finalize import validate_snapshot_current
    from masterone.mdm.workflow_jobs import finish,job_identity
    with factory.begin() as db:
        run,op,_=job_identity(db,operation_id)
        run_id,revision,decision_revision=run.id,run.workflow_revision,run.decision_revision
        snapshot=load(db,op.artifact_id,run.id,'DECISION_SNAPSHOT')
        plan=load(db,op.result['plan_artifact_id'],run.id,'AUTOMATION_PLAN')
        initial_gate=runtime_gate(db,run.id,snapshot)
        if not initial_gate['eligible'] or initial_gate.get('certificate_hash')!=plan['certificate_hash']:raise ValueError('AUTOMATION_CERTIFICATE_OR_STOP_CHANGED')
    # Compute and compare whole components before any acceptance is persisted.
    fresh=plan_components(snapshot,limit=get_settings().pair_automation_max_components_per_run,
        certified_cohorts=set(initial_gate['cohorts']))
    progress('automation-apply',0,len(plan['qualified_fingerprints']))
    with factory.begin() as db:
        run,op,task=job_identity(db,operation_id)
        gate=runtime_gate(db,run.id,snapshot)
        if not gate['eligible'] or gate.get('certificate_hash')!=plan['certificate_hash']:raise ValueError('AUTOMATION_CERTIFICATE_OR_STOP_CHANGED')
        if run.workflow_revision!=revision or run.decision_revision!=decision_revision or decision_revision!=snapshot['decision_revision']:raise ValueError('AUTOMATION_REVISION_CHANGED')
        validate_snapshot_current(db,run,snapshot)
        if fresh['components']!=plan['components']:raise ValueError('AUTOMATION_COMPONENT_CHANGED')
        selected=[c for c in fresh['components'] if c['fingerprint'] in plan['qualified_fingerprints']]
        if (not selected or len(selected)!=len(plan['qualified_fingerprints']) or len(selected)>gate['remaining_component_budget']
                or any(c['cohort'] not in gate['cohorts'] for c in selected)):raise ValueError('AUTOMATION_SCOPE_CHANGED')
        ids={pid for c in selected for pid in c['pair_ids']}
        pairs={p.id:p for p in db.query(WorkflowPair).filter(WorkflowPair.run_id==run.id,WorkflowPair.id.in_(ids))}
        if len(pairs)!=len(ids) or any(p.decision!='UNDECIDED' for p in pairs.values()):raise ValueError('AUTOMATION_REVIEWER_INTENT_CHANGED')
        for component in selected:
            for pid in component['pair_ids']:
                pair=pairs[pid];pair.decision='ACCEPTED';pair.revision+=1
                pair.automation=dict(version=VERSION,operation_id=op.id,certificate_hash=plan['certificate_hash'],
                    component_fingerprint=component['fingerprint'],cohort=component['cohort'],source='AUTOMATIC')
                audit(db,run,op.actor_user_id,'WORKFLOW_PAIR_AUTOMATIC_ACCEPTED',{'pair_id':pid,'pair_revision':pair.revision,**pair.automation})
        # File revocation is checked again immediately before publication.
        final_gate=runtime_gate(db,run.id,snapshot)
        if not final_gate['eligible'] or final_gate.get('certificate_hash')!=plan['certificate_hash']:raise ValueError('AUTOMATION_CERTIFICATE_CHANGED')
        run.decision_revision+=1;run.workflow_revision+=1
        op.result={**op.result,'applied_components':len(selected),'applied_pairs':len(ids),
            'certificate_hash':plan['certificate_hash'],'component_fingerprints':[c['fingerprint'] for c in selected]}
        op.progress=dict(processed=len(ids),total=len(ids),unit='pairs')
        finish(db,run,op,task,'DECIDE_REVIEW')


def stop_automation(db,run_id,request,actor):
    payload=request.model_dump();run=enabled_run(db,run_id,actor)
    prior=replay(db,run_id,'AUTOMATION_STOP',request.request_id,payload)
    if prior:return operation_read(prior)
    check_revision(run,payload)
    active=active_operation(db,run_id)
    if run.workflow_state!='DECIDE_REVIEW' or (active and active.operation_type not in {'AUTOMATION_SHADOW','AUTOMATION_APPLY'}):conflict('DECISIONS_LOCKED')
    cancelled_id=None
    if active:
        from masterone.mdm.models import ResolutionTask,ResolutionJobAttempt
        from masterone.mdm.job_queue import database_now
        task=db.query(ResolutionTask).filter_by(run_id=run_id,operation_id=active.id).one()
        task.cancel_requested=True
        task.status,task.phase,task.error_code='CANCELLED','cancelled','AUTOMATION_STOPPED'
        if task.attempt_id:
            attempt=db.get(ResolutionJobAttempt,task.attempt_id)
            if attempt and attempt.status=='RUNNING':
                attempt.status,attempt.error_code,attempt.finished_at='CANCELLED','AUTOMATION_STOPPED',database_now(db)
        active.status,active.stage,active.error_code='CANCELLED','cancelled','AUTOMATION_STOPPED'
        cancelled_id=active.id
        run.status,run.phase='COMPLETED','completed'
    op=new_operation(db,run,'AUTOMATION_STOP',request.request_id,payload,actor)
    op.status,op.stage,op.result='COMPLETED','completed',{'stopped':True,'saved_decisions_preserved':True}
    run.workflow_revision+=1
    audit(db,run,actor,'WORKFLOW_AUTOMATION_STOPPED',{'operation_id':op.id,'cancelled_operation_id':cancelled_id})
    db.commit();return operation_read(op)
