from masterone.mdm.workflow_review import apply_decision, eligibility, negative_links, scoped_pairs
from masterone.mdm.workflow_service import conflict


def selected_pairs(db, run_id, scope):
    if (scope.get('pair_ids') is None) == (scope.get('filter') is None):
        conflict('BULK_SCOPE_REQUIRED')
    query = scoped_pairs(db, run_id, scope.get('filter'))
    from masterone.mdm.workflow_models import WorkflowPair
    if scope.get('pair_ids') is not None:
        query = query.filter(WorkflowPair.id.in_(scope['pair_ids']))
        if query.count() != len(set(scope['pair_ids'])):
            conflict('BULK_PAIR_NOT_FOUND')
    if scope.get('excluded_pair_ids'):
        query = query.filter(~WorkflowPair.id.in_(scope['excluded_pair_ids']))
    return query.order_by(WorkflowPair.id)


def validate_soft_group(db, run, payload):
    """One source-bound reviewed component, at most 20 members; no filtered D batches."""
    if payload['decision'] != 'merge':
        return
    from masterone.mdm.pipeline.cluster_policy import load_policy
    policy = load_policy()
    from masterone.mdm.workflow_assessment_reads import effective_column
    from masterone.mdm.workflow_models import WorkflowPair
    from masterone.mdm.workflow_review_clusters import review_cluster
    query = selected_pairs(db, run.id, payload['scope']).filter(
        WorkflowPair.decision.in_(('UNDECIDED', 'DEFERRED')),
        effective_column()['primary_group'].as_string() == 'D')
    seed = query.first()
    if seed is None:
        return
    if (payload['scope'].get('pair_ids') is None or not payload.get('review_group_token') or
            not payload.get('review_group_record_ids')):
        conflict('SOFT_BULK_GROUP_REVIEW_REQUIRED')
    group = review_cluster(db, run, seed.id)
    members = payload['review_group_record_ids']
    selected = {p.id for p in selected_pairs(db, run.id, payload['scope']).limit(policy.max_pair_checks_per_union + 1)}
    if (not group['batch_allowed'] or len(members) > policy.max_cluster_size or len(members) != len(set(members)) or
            set(members) != {r['record_id'] for r in group['records']} or
            selected != set(group['pending_pair_ids']) or group['review_scope_token'] != payload['review_group_token']):
        conflict('SOFT_BULK_GROUP_REVIEW_CHANGED')


def run_bulk_decisions(operation_id, factory, progress):
    """Persist each bounded batch and its cursor together; retry never repeats writes."""
    from masterone.mdm.job_queue import guard
    from masterone.mdm.workflow_jobs import finish, job_identity
    from masterone.mdm.workflow_models import WorkflowConflict, WorkflowPair
    while True:
        done = False
        with factory.begin() as db:
            run, op, task = job_identity(db, operation_id)
            checkpoint = op.result.get('checkpoint', {})
            expected = checkpoint.get('decision_revision', op.payload['expected_decision_revision'])
            if run.decision_revision != expected:
                raise ValueError('WORKFLOW_BULK_REVISION_CHANGED')
            query = selected_pairs(db, run.id, op.payload['scope'])
            counts = dict(checkpoint['counts']) if checkpoint else dict(total=query.count(), processed=0,
                applied=0, accepted=0, skipped=0, blocked=0, unit='pairs')
            if checkpoint.get('last_pair_id'):
                query = query.filter(WorkflowPair.id > checkpoint['last_pair_id'])
            rows = query.limit(100).all()
            if not rows:
                finish(db, run, op, task, 'DECIDE_REVIEW')
                done = True
            else:
                links = negative_links(db, run.id, rows)
                applied = []
                from masterone.mdm.steward_feedback import (
                    capture_review_snapshot,
                    persist_feedback,
                    prepare_feedback_batch,
                )
                feedback_batch = prepare_feedback_batch(db, run, rows, op, op.actor_user_id)
                with db.no_autoflush:
                    for pair in rows:
                        if pair.decision not in ('UNDECIDED', 'DEFERRED'):
                            counts['skipped'] += 1
                        elif op.payload['decision'] == 'merge' and (
                            not eligibility(pair)['auto_allowed' if op.payload.get('auto_only', False) else 'manual_allowed'] or
                            tuple(sorted((pair.baseline['left_id'], pair.baseline['right_id']))) in links):
                            counts['blocked'] += 1
                        elif op.payload['decision'] == 'merge' and eligibility(pair).get('reason_required') and not (op.payload.get('reason') or '').strip():
                            counts['blocked'] += 1
                        else:
                            snapshot = capture_review_snapshot(db, run, pair, batch=feedback_batch)
                            apply_decision(db, run, pair, op.payload['decision'], op.actor_user_id,
                                           links=links, resolve_conflicts=False, reason=op.payload.get('reason'))
                            persist_feedback(db, run, pair, op, op.actor_user_id, snapshot, 'HUMAN_BULK_DIRECT', batch=feedback_batch)
                            counts['applied'] += 1
                            counts['accepted'] += 1  # Keep the old progress contract as an alias.
                            applied.append(pair.id)
                if applied:
                    if op.payload['decision'] in ('no_merge', 'deny'):
                        db.query(WorkflowConflict).filter(WorkflowConflict.run_id == run.id,
                            WorkflowConflict.pair_id.in_(applied), WorkflowConflict.resolved.is_(False)).update(
                                {'resolved': True}, synchronize_session=False)
                    run.decision_revision += 1
                    run.workflow_revision += 1
                counts['processed'] += len(rows)
                op.progress, op.stage = counts, 'review'
                op.result = {'checkpoint': {'last_pair_id': rows[-1].id, 'decision_revision': run.decision_revision,
                                            'counts': counts}}
                task.processed_count, task.total_count = counts['processed'], counts['total']
                db.flush()
                guard(db, run.id)
        if done:
            return
        progress('review', counts['processed'], counts['total'])
