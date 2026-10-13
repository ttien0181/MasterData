"""Conservative repair proposals: engine-verified, reviewed, audited and reversible."""
from copy import deepcopy

from fastapi import HTTPException

from masterone.mdm.engine_adapter import engine_context
from masterone.mdm.generation_staging import config_identity, digest
from masterone.mdm.workflow_artifacts import load, seal
from masterone.mdm.workflow_cluster_reads import current_generation
from masterone.mdm.workflow_models import (
    WorkflowArtifact,
    WorkflowCluster,
    WorkflowConflict,
    WorkflowOperation,
    WorkflowPair,
)
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


def versions(run):
    return {key: getattr(run, key) for key in ('workflow_revision', 'decision_revision', 'cluster_revision', 'golden_revision')}


def submit_resolution(db, run_id, request, actor):
    payload = request.model_dump()
    run = enabled_run(db, run_id, actor)
    prior = replay(db, run_id, 'GOLDEN_RESOLVE', request.request_id, payload)
    if prior:
        return operation_read(prior)
    check_revision(run, payload)
    if active_operation(db, run_id) or run.workflow_state not in ('DECIDE_REVIEW', 'GOLDEN_REVIEW'):
        conflict('GOLDEN_LOCKED')
    generation = current_generation(db, run)
    from masterone.mdm.golden_service import selected_clusters
    if payload.get('cluster_ids') is not None or payload.get('excluded_cluster_ids'):
        selected_clusters(db, run, payload)
    op = new_operation(db, run, 'GOLDEN_RESOLVE', request.request_id, payload, actor)
    op.artifact_id = generation.artifact_id
    enqueue(db, run, op)
    run.workflow_revision += 1
    audit(db, run, actor, 'WORKFLOW_GOLDEN_RESOLVE', {'operation_id': op.id})
    db.commit()
    return operation_read(op)


def run_resolution(operation_id, factory, progress):
    from masterone.mdm.golden_service import selected_clusters, validate_selections
    from masterone.mdm.workflow_jobs import finish, job_identity
    with factory.begin() as db:
        run, op, task = job_identity(db, operation_id)
        original_state, run_id, revision = run.workflow_state, run.id, run.workflow_revision
        generation = current_generation(db, run)
        output = load(db, generation.artifact_id, run_id, 'CLUSTER')
        snapshot = deepcopy(resolution_snapshot(db, run_id, output))
        selected = selected_clusters(db, run, op.payload) if output['clusters'] else []
        selected_ids = [c.id for c in selected]
        # Selection of official groups never silently repairs an unrelated conflict region.
        conflicts = output['conflicts'] if op.payload.get('cluster_ids') is None else []
        pair_ids = sorted({c['pair_id'] for c in conflicts})
        pairs = db.query(WorkflowPair).filter(WorkflowPair.run_id == run_id, WorkflowPair.id.in_(pair_ids)).all()
        changes = [{'pair_id': p.id, 'before': p.decision, 'revision': p.revision, 'after': 'REJECTED',
                    'left_id': p.baseline['left_id'], 'right_id': p.baseline['right_id']} for p in pairs if p.decision != 'REJECTED']
    proposal = None
    if changes:
        repaired = deepcopy(snapshot)
        for change in changes:
            repaired['overlays'][change['pair_id']] = 'REJECTED'
        progress('verify-repair', 0, len(changes))
        with engine_context(snapshot['profile']):
            if snapshot['execution_identity'] != digest(config_identity(snapshot['policy_version'])):
                raise ValueError('WORKFLOW_CONFIG_CHANGED')
            result, groups = repair_preview_groups(repaired, run_id, generation.revision + 1)
        from masterone.mdm.workflow_partial import candidate_region
        affected = candidate_region(snapshot, {rid for conflict_row in conflicts for rid in conflict_row['record_ids']})
        proposal = {'status': 'READY' if not result['conflicts'] else 'NEEDS_REVIEW',
                    'reason_codes': sorted({c['reason'] for c in conflicts}), 'changes': changes,
                    'before_record_ids': sorted(affected),
                    'after_groups': [g['member_record_ids'] for g in groups if affected.intersection(g['member_record_ids'])],
                    'explanation_vi': 'Đề xuất giữ riêng các liên kết bị guard từ chối. Nhóm sau thay đổi đã được kiểm tra bằng engine hiện tại. Cần duyệt trước khi áp dụng.',
                    'remaining_conflicts': result['conflicts']}
        proposal['unresolved_record_count'] = len(result.get('unresolved_record_ids', []))
    plan = {'snapshot': snapshot, 'proposal': proposal, 'decision_revision': snapshot['decision_revision']}
    artifact_id = seal(factory, run_id, 'GOLDEN_REPAIR', plan)
    with factory.begin() as db:
        run, op, task = job_identity(db, operation_id)
        if run.workflow_revision != revision or run.decision_revision != snapshot['decision_revision']:
            raise ValueError('WORKFLOW_REVISION_CHANGED')
        with engine_context(snapshot['profile']):
            if snapshot['execution_identity'] != digest(config_identity(snapshot['policy_version'])):
                raise ValueError('WORKFLOW_CONFIG_CHANGED')
        clusters = db.query(WorkflowCluster).filter(WorkflowCluster.id.in_(selected_ids), WorkflowCluster.generation_id == generation.id).all()
        confirmed, blocked = 0, 0
        for c in clusters:
            try:
                validate_selections(db, run, c, c.selections)
                # Conflicting extra fields need an explicit source choice.
                from masterone.mdm.golden_service import unresolved_extra_fields
                unresolved = unresolved_extra_fields(db, c)
                if unresolved or c.payload.get('requires_manual_review'):
                    blocked += 1
                    continue
            except HTTPException:
                blocked += 1
                continue
            c.confirmed = True
            c.revision += 1
            confirmed += 1
        if confirmed:
            run.golden_revision += 1
        op.artifact_id = artifact_id
        audit(db, run, op.actor_user_id, 'WORKFLOW_GOLDEN_RESOLVED', {'operation_id': op.id, 'confirmed': confirmed, 'blocked': blocked})
        finish(db, run, op, task, original_state)
        summary = None if proposal is None else {**proposal, 'change_count': len(proposal['changes']),
            'changes': proposal['changes'][:25], 'before_record_ids': proposal['before_record_ids'][:100],
            'after_groups': proposal['after_groups'][:100], 'after_group_count': len(proposal['after_groups']),
            'remaining_conflicts': proposal['remaining_conflicts'][:25]}
        op.result = {'confirmed': confirmed, 'blocked': blocked, 'proposal': summary, 'versions': versions(run)}


def repair_command(db, run_id, request, actor, undo=False):
    kind = 'UNDO_REPAIR' if undo else 'APPLY_REPAIR'
    payload = request.model_dump()
    run = enabled_run(db, run_id, actor)
    prior = replay(db, run_id, kind, request.request_id, payload)
    if prior:
        return prior.result['response']
    check_revision(run, payload)
    if active_operation(db, run_id) or run.workflow_state not in ('DECIDE_REVIEW', 'GOLDEN_REVIEW'):
        conflict('REPAIR_LOCKED')
    source = db.query(WorkflowOperation).filter_by(id=request.operation_id, run_id=run_id, status='COMPLETED').one_or_none()
    if source is None or source.operation_type != ('APPLY_REPAIR' if undo else 'GOLDEN_RESOLVE'):
        raise HTTPException(404, 'Repair operation not found')
    plan = load(db, source.artifact_id, run_id, 'GOLDEN_REPAIR')
    proposal = plan['proposal']
    if not proposal or proposal['status'] != 'READY':
        conflict('REPAIR_NOT_READY')
    if not undo and versions(run) != source.result['versions']:
        conflict('REPAIR_STALE')
    if undo:
        if versions(run) != source.result.get('undo_versions'):
            conflict('UNDO_STALE')
    changes = proposal['changes']
    snapshot = deepcopy(plan['snapshot'])
    snapshot['overlays'] = {p.id: p.decision for p in db.query(WorkflowPair).filter_by(run_id=run_id)}
    for change in changes:
        pair = db.query(WorkflowPair).filter_by(id=change['pair_id'], run_id=run_id).one()
        expected_decision = 'REJECTED' if undo else change['before']
        expected_revision = change['revision'] + (1 if undo else 0)
        if pair.decision != expected_decision or pair.revision != expected_revision:
            conflict('REPAIR_STALE')
        pair.decision = change['before'] if undo else 'REJECTED'
        pair.revision += 1
        snapshot['overlays'][pair.id] = pair.decision
        audit(db, run, actor, 'WORKFLOW_' + kind + '_PAIR', {'pair_id': pair.id, 'decision': pair.decision, 'revision': pair.revision})
    for blocker in db.query(WorkflowConflict).filter_by(run_id=run_id, resolved=False):
        blocker.resolved = True  # The newly queued engine recreates all current conflicts.
    run.decision_revision += 1
    run.workflow_revision += 1
    snapshot['decision_revision'] = run.decision_revision
    snapshot['workflow_revision'] = run.workflow_revision
    artifact = WorkflowArtifact(run_id=run_id, phase='DECISION_SNAPSHOT', payload=snapshot, content_hash=digest(snapshot))
    db.add(artifact)
    db.flush()
    receipt = new_operation(db, run, kind, request.request_id, payload, actor)
    receipt.artifact_id = source.artifact_id
    receipt.status, receipt.stage = 'COMPLETED', 'completed'
    job = new_operation(db, run, 'CLUSTER', 'repair-cluster:' + receipt.id, {'repair_operation_id': receipt.id}, actor)
    job.artifact_id = artifact.id
    job.result = {'repair_record_ids': proposal['before_record_ids'],
                  'auto_confirm_record_ids': [] if undo else proposal['before_record_ids']}
    enqueue(db, run, job)
    run.workflow_state = 'CLUSTER_QUEUED'
    receipt.result = {'cluster_operation_id': job.id, 'decision_revision': run.decision_revision, 'operation_id': receipt.id}
    audit(db, run, actor, 'WORKFLOW_' + kind, {'source_operation_id': source.id, **receipt.result})
    response = {**operation_read(job), 'result': dict(receipt.result)}
    receipt.result = {**receipt.result, 'response': response}
    db.commit()
    return response


def resolution_snapshot(db, run_id, cluster_output):
    snapshot_hash = cluster_output['workflow_context']['snapshot_hash']
    artifact = db.query(WorkflowArtifact.id).filter_by(run_id=run_id, phase='DECISION_SNAPSHOT',
                                                      content_hash=snapshot_hash).first()
    return load(db, artifact[0] if artifact else None, run_id, 'DECISION_SNAPSHOT')


def repair_preview_groups(snapshot, run_id, revision):
    """Forecast exactly the guarded eligible records used by publication."""
    from masterone.mdm.cluster_publication import with_singletons
    from masterone.mdm.workflow_finalize import cluster_snapshot, partial_cluster_snapshot
    result = partial_cluster_snapshot(snapshot, run_id) if snapshot.get('allow_partial') else cluster_snapshot(snapshot, run_id)
    records = [record for record in snapshot['records'] if record['id'] not in set(result['conflict_record_ids'])]
    groups = with_singletons(records, result['safe_clusters'], run_id, revision) if not result['conflicts'] else []
    return result, groups
