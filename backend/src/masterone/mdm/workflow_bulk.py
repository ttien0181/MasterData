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
                with db.no_autoflush:
                    for pair in rows:
                        if pair.decision not in ('UNDECIDED', 'DEFERRED'):
                            counts['skipped'] += 1
                        elif op.payload['decision'] == 'merge' and (
                            not eligibility(pair)['auto_allowed' if op.payload.get('auto_only', False) else 'manual_allowed'] or
                            tuple(sorted((pair.baseline['left_id'], pair.baseline['right_id']))) in links):
                            counts['blocked'] += 1
                        else:
                            apply_decision(db, run, pair, op.payload['decision'], op.actor_user_id,
                                           links=links, resolve_conflicts=False)
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
