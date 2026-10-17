from masterone.mdm.workflow_review import apply_decision, eligibility, negative_links, scoped_pairs
from masterone.mdm.workflow_service import conflict


def _measure_bulk_sql(function):
    """Aggregate numeric DB work per operation, without logging source values."""
    from functools import wraps

    @wraps(function)
    def measured(operation_id, factory, progress):
        import json
        import logging
        from collections import Counter
        from time import perf_counter

        from sqlalchemy import event

        engine = factory.kw['bind']
        counts, seconds = Counter(), Counter()
        def before(_conn, _cursor, statement, _parameters, context, many):
            context._bulk_sql_started = perf_counter()
            kind = statement.split()[0].upper()
            counts[kind] += 1
            counts['executemany_calls'] += int(many)
        def after(_conn, _cursor, statement, _parameters, context, _many):
            seconds[statement.split()[0].upper()] += perf_counter() - context._bulk_sql_started
        def commit(_conn):
            counts['commit_attempts'] += 1
        event.listen(engine, 'before_cursor_execute', before)
        event.listen(engine, 'after_cursor_execute', after)
        event.listen(engine, 'commit', commit)
        try:
            return function(operation_id, factory, progress)
        finally:
            event.remove(engine, 'before_cursor_execute', before)
            event.remove(engine, 'after_cursor_execute', after)
            event.remove(engine, 'commit', commit)
            logging.getLogger(__name__).info('BULK_SQL_METRICS %s', json.dumps(
                {'operation_id': operation_id, 'counts': dict(counts), 'sql_seconds': dict(seconds)}))
    return measured


def flush_review_batch(db, run_id, pairs):
    """Persist homogeneous decisions in sets, within the existing checkpoint transaction.

    Decision/feedback builders still run for every pair. Only their persistence
    changes; revision predicates detect an unexpected write before checkpointing.
    """
    from collections import defaultdict

    from sqlalchemy import insert, update
    from sqlalchemy.orm.attributes import set_committed_value

    from masterone.mdm.matching_foundation_models import StewardFeedback
    from masterone.mdm.models import ResolutionAuditEvent
    from masterone.mdm.workflow_models import WorkflowPair

    groups = defaultdict(list)
    for pair in pairs:
        groups[pair.revision - 1, pair.decision, pair.review_reason].append(pair)
    with db.no_autoflush:
        for (revision, decision, reason), rows in groups.items():
            result = db.execute(update(WorkflowPair.__table__).where(
                WorkflowPair.run_id == run_id, WorkflowPair.id.in_([p.id for p in rows]),
                WorkflowPair.revision == revision).values(
                    decision=decision, review_reason=reason, automation=None, revision=revision + 1))
            if result.rowcount != len(rows):
                raise ValueError('WORKFLOW_BULK_PAIR_REVISION_CHANGED')
            for pair in rows:
                for key in ('decision', 'review_reason', 'automation', 'revision'):
                    set_committed_value(pair, key, getattr(pair, key))
        # Defaults (audit UUID/timestamp) remain the model's existing defaults.
        # Database lineage/hash/FK guards run for these inserts as usual.
        for model in (StewardFeedback, ResolutionAuditEvent):
            pending = [row for row in db.new if isinstance(row, model) and row.run_id == run_id]
            if pending:
                values = [{c.name: getattr(row, c.name) for c in model.__table__.columns
                           if c.name in row.__dict__ or c.default is None} for row in pending]
                db.execute(insert(model.__table__), values)
                for row in pending:
                    db.expunge(row)


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


@_measure_bulk_sql
def run_bulk_decisions(operation_id, factory, progress):
    """Persist each bounded batch and its cursor together; retry never repeats writes."""
    from masterone.config import get_settings
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
            rows = query.limit(get_settings().workflow_bulk_batch_rows).all()
            if not rows:
                finish(db, run, op, task, 'DECIDE_REVIEW')
                done = True
            else:
                links = negative_links(db, run.id, rows)
                applied = []
                changed_pairs = []
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
                            changed_pairs.append(pair)
                if op.payload['decision'] == 'no_merge':
                    flush_review_batch(db, run.id, changed_pairs)
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
