"""Leased publication from a frozen Parquet decision universe and disk plan."""
from dataclasses import asdict
from types import SimpleNamespace
from uuid import uuid4

from masterone.mdm.engine_adapter import engine_context
from masterone.mdm.generation_staging import digest_sequence
from masterone.mdm.models import ResolutionRun
from masterone.mdm.workflow_jobs import finish, job_identity
from masterone.mdm.workflow_models import (
    WorkflowArtifact,
    WorkflowCluster,
    WorkflowConflict,
    WorkflowGeneration,
    WorkflowMember,
    WorkflowOperation,
)
from masterone.mdm.workflow_snapshot_index import unpack
from masterone.mdm.workflow_snapshot_producers import validate_streamed_snapshot
from masterone.mdm.workflow_snapshot_storage import open_snapshot, seal_snapshot
from masterone.mdm.workflow_streamed_entities import StreamedEntities
from masterone.mdm.workflow_streamed_plan import StreamedPlan


def checked_plan(db, run, op, snapshot, target_revision):
    from masterone.mdm.workflow_cluster_preview import checked_preview, publication_identity
    if not op.payload.get('preview_operation_id'):
        return None
    preview = checked_preview(db, run, SimpleNamespace(preview_operation_id=op.payload['preview_operation_id'],
        preview_token=op.payload['preview_token'], allow_partial=op.payload.get('allow_partial', False)))
    receipt = preview.result
    with open_snapshot(db, receipt['plan_artifact_id'], run.id, 'CLUSTER_PLAN') as saved:
        metadata = dict(saved.row.payload['metadata'])
        if (saved.row.content_hash != receipt.get('plan_storage_hash') or saved.row.content_hash != receipt.get('plan_hash')
                or metadata['run_id'] != run.id or metadata['snapshot_id'] != snapshot.row.id
                or metadata['snapshot_hash'] != snapshot.row.content_hash or metadata['target_cluster_revision'] != target_revision
                or metadata['source_golden_revision'] != run.golden_revision or metadata['engine_identity'] != publication_identity()):
            raise ValueError('CLUSTER_PLAN_CHANGED')
        frozen_memberships = digest_sequence({'entity_id': g['entity_id'], 'member_record_ids': g['member_record_ids']}
                                             for g in saved.iter_rows('groups'))
        if frozen_memberships != metadata['memberships_hash']:
            raise ValueError('CLUSTER_PLAN_CHANGED')
        return metadata


def run_streamed_cluster(operation_id, factory, progress):
    with factory.begin() as db:
        run, op, _ = job_identity(db, operation_id)
        run_id, snapshot_id, revision = run.id, op.artifact_id, run.workflow_revision
        target_revision, decision_revision = run.cluster_revision + 1, run.decision_revision
        golden_revision = run.golden_revision
        repair = dict(op.result)
    with factory() as source_db, open_snapshot(source_db, snapshot_id, run_id, 'DECISION_SNAPSHOT') as view:
        run = source_db.get(ResolutionRun, run_id)
        metadata = dict(view.row.payload['metadata'])
        validate_streamed_snapshot(source_db, run, metadata)
        source_op = source_db.get(WorkflowOperation, operation_id)
        frozen_plan = checked_plan(source_db, run, source_op, view, target_revision)
        source_db.rollback()
        progress('cluster', 0, len(view.records))
        with engine_context(view['profile']):
            from masterone.mdm.pipeline.cluster_policy import load_policy
            policy = asdict(load_policy())
            plan = StreamedPlan(view, run_id, target_revision)
            generation_id = frozen_plan['generation_id'] if frozen_plan else str(uuid4())
            planned_generation = SimpleNamespace(id=generation_id, revision=target_revision)
            identities = StreamedEntities(source_db, run, plan)
            identities.prepare_singletons(planned_generation, repair)
            identities.prepare(planned_generation)
        if frozen_plan:
            from masterone.mdm.workflow_streamed_preview import memberships_hash
            if (memberships_hash(plan) != frozen_plan['memberships_hash'] or plan.counts() != frozen_plan['coverage']
                    or digest_sequence(r['id'] for r in plan.iter_eligible_records()) != frozen_plan['eligible_ids_hash']):
                raise ValueError('CLUSTER_PLAN_CHANGED')
        source_db.rollback()
        source_id = seal_snapshot(factory, run_id, 'SINGLETON_SOURCE',
            {'profile': view['profile'], 'survivorship_policy': policy, 'workflow_context': metadata},
            {'records': plan.iter_eligible_records()}, operation_id=operation_id)
        counts = plan.counts()
        context = {key: metadata[key] for key in ('input_hash', 'profile_identity', 'execution_identity',
                   'policy_version', 'workflow_revision', 'decision_revision', 'assessment_id')}
        context.update(snapshot_hash=view.row.content_hash, snapshot_artifact_id=snapshot_id)
        artifact_id = seal_snapshot(factory, run_id, 'CLUSTER',
            {'workflow_context': context, 'publication': counts, 'local_evidence_version': 1},
            {'groups': plan.iter_groups(), 'pairs': (unpack(payload) for (payload,) in plan.connection.execute(
                'SELECT payload FROM pairs ORDER BY id')), 'excluded': plan.iter_excluded(),
             'conflicts': (unpack(payload) for (payload,) in plan.connection.execute('SELECT payload FROM plan_issues ORDER BY id')),
             'review_groups': plan.iter_reviews()},
            operation_id=operation_id)
        with factory() as db:
            source_hash = db.query(WorkflowArtifact.content_hash).filter_by(id=source_id).scalar()
            validate_streamed_snapshot(db, db.get(type(run), run_id), metadata)
        with factory.begin() as db:
            run, op, task = job_identity(db, operation_id)
            if (run.workflow_revision != revision or run.cluster_revision + 1 != target_revision
                    or run.decision_revision != decision_revision or run.golden_revision != golden_revision):
                raise ValueError('WORKFLOW_REVISION_CHANGED')
            generation = WorkflowGeneration(id=generation_id, run_id=run_id, revision=target_revision,
                artifact_id=artifact_id, storage_version='workflow_parquet_v2',
                coverage={**counts, 'source_artifact_id': source_id, 'source_hash': source_hash}, singleton_confirmation={})
            db.add(generation)
            db.flush()
            identities.db, identities.run = db, run
            cluster_batch, member_batch = [], []
            def flush():
                db.add_all(cluster_batch)
                db.flush()
                db.add_all(member_batch)
                db.flush()
                cluster_batch.clear()
                member_batch.clear()
            from masterone.mdm.golden_service import (
                carry_choices,
                repair_confirmation,
                unresolved_extra_fields,
                validate_selections,
            )
            for group in identities.persist(generation, actor=op.actor_user_id, record_versions=False):
                members = group['member_record_ids']
                placeholders = ','.join('?' for _ in members)
                evidence = [unpack(payload) for (payload,) in plan.connection.execute(
                    f'SELECT payload FROM pairs WHERE a IN ({placeholders}) AND b IN ({placeholders}) ORDER BY id', (*members, *members))]
                payload = {**group, 'local_evidence_version': 1, 'pair_evidence': evidence,
                           'workflow_context': context, 'llm_stats': {'checked': 0, 'stopped': 'SEPARATE_JOB'}}
                row = WorkflowCluster(id=group['id'], generation_id=generation_id, payload=payload,
                                      golden=group['golden_fields'], selections={}, confirmed=False)
                previous = identities.previous_groups(members)
                carry_choices(db, run, row, previous_clusters=previous)
                if repair_confirmation(row, previous, repair):
                    validate_selections(db, run, row, row.selections)
                    row.confirmed = not unresolved_extra_fields(db, row)
                from masterone.mdm.models import ResolutionEntity
                from masterone.mdm.workflow_entities import selected_entity_version
                selected_entity_version(db, db.get(ResolutionEntity, row.payload['entity_id']), generation, row, actor=op.actor_user_id)
                cluster_batch.append(row)
                member_batch.extend(WorkflowMember(generation_id=generation_id, cluster_id=row.id, record_id=rid) for rid in members)
                if len(cluster_batch) >= 500 or len(member_batch) >= 2000:
                    flush()
            flush()
            for cid, retained in plan.connection.execute('SELECT id,retained FROM plan_conflict_state'):
                if not retained:
                    prior = db.get(WorkflowConflict, cid)
                    if prior is not None and prior.run_id == run_id:
                        prior.resolved = True
            for key, payload in plan.connection.execute('SELECT id,payload FROM plan_issues ORDER BY id'):
                if not key.startswith('retained:'):
                    issue = unpack(payload)
                    db.add(WorkflowConflict(run_id=run_id, operation_id=op.id, pair_id=issue['pair_id'], payload=issue))
            issues = plan.issues()
            issue_count = plan.connection.execute('SELECT COUNT(*) FROM plan_issues').fetchone()[0]
            op.result = {**repair, **counts, 'publication': counts, 'blockers': issues, 'blocker_count': issue_count,
                         'snapshot_id': snapshot_id, 'streamed_publication': True}
            run.cluster_revision = target_revision
            run.golden_revision += 1
            finish(db, run, op, task, 'DECIDE_REVIEW' if issue_count or counts['unresolved_records'] else 'GOLDEN_REVIEW')
