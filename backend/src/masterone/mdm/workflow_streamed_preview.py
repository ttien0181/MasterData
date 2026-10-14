"""Bounded preview receipts and pages over a frozen disk publication plan."""
from dataclasses import asdict
from types import SimpleNamespace
from uuid import uuid4

from masterone.mdm.engine_adapter import engine_context
from masterone.mdm.generation_staging import digest, digest_sequence
from masterone.mdm.models import ResolutionRun
from masterone.mdm.workflow_artifacts import seal
from masterone.mdm.workflow_jobs import finish, job_identity
from masterone.mdm.workflow_models import WorkflowArtifact
from masterone.mdm.workflow_snapshot_index import unpack
from masterone.mdm.workflow_snapshot_producers import validate_streamed_snapshot
from masterone.mdm.workflow_snapshot_storage import open_snapshot, seal_snapshot
from masterone.mdm.workflow_streamed_entities import StreamedEntities
from masterone.mdm.workflow_streamed_plan import StreamedPlan


def memberships_hash(plan):
    return digest_sequence({'entity_id': g['entity_id'], 'member_record_ids': g['member_record_ids']} for g in plan.iter_groups())


def streamed_groups_page(db, run, plan_id, offset, limit):
    from masterone.mdm.workflow_cluster_preview import group_summary
    from masterone.mdm.workflow_generation_reads import GenerationReader
    with open_snapshot(db, plan_id, run.id, 'CLUSTER_PLAN') as plan:
        metadata = plan.row.payload['metadata']
        persisted = plan.descriptors.get('groups', {}).get('count', 0)
        items = plan.list_page('groups', offset, limit)['items']
        if len(items) < limit:
            with open_snapshot(db, metadata['source_artifact_id'], run.id, 'SINGLETON_SOURCE') as source:
                if source.row.content_hash != metadata['source_hash'] or source.locators is None or plan.locators is None:
                    raise ValueError('WORKFLOW_INDEX_UNAVAILABLE')
                source.locators.execute('ATTACH DATABASE ? AS preview_plan', (str(plan.directory / 'locators.sqlite'),))
                rows = source.locators.execute("SELECT s.id FROM locations s WHERE s.stream='records' AND NOT EXISTS "
                    "(SELECT 1 FROM preview_plan.locations p WHERE p.stream='members' AND p.id=s.id) ORDER BY s.id LIMIT ? OFFSET ?",
                    (limit-len(items), max(0, offset-persisted))).fetchall()
                generation = SimpleNamespace(id=metadata['generation_id'], run_id=run.id, storage_version='workflow_parquet_v2',
                    coverage={'source_artifact_id': source.row.id, 'source_hash': source.row.content_hash}, singleton_confirmation={})
                reader = GenerationReader(db, run, generation)
                items.extend(reader._virtual_row(record).payload for record in source.record_page(rid for (rid,) in rows))
        total = metadata['coverage']['clusters']
        return {'items': [group_summary(g) for g in items], 'total': total,
                'next_cursor': str(offset+len(items)) if offset+len(items) < total else None}


def run_streamed_preview(operation_id, factory, progress):
    from masterone.mdm.workflow_cluster_preview import publication_identity
    with factory.begin() as db:
        run, op, _ = job_identity(db, operation_id)
        run_id, source_id, revision = run.id, op.artifact_id, run.workflow_revision
        target_revision, source_golden_revision = run.cluster_revision + 1, run.golden_revision
    with factory() as db, open_snapshot(db, source_id, run_id, 'DECISION_SNAPSHOT') as snapshot:
        run = db.get(ResolutionRun, run_id)
        metadata = dict(snapshot.row.payload['metadata'])
        validate_streamed_snapshot(db, run, metadata)
        db.rollback()
        progress('preview-groups', 0, len(snapshot.records))
        generation_id = str(uuid4())
        with engine_context(snapshot['profile']):
            from masterone.mdm.pipeline.cluster_policy import load_policy
            policy = load_policy()
            plan = StreamedPlan(snapshot, run_id, target_revision)
            identity = StreamedEntities(db, run, plan)
            generation = SimpleNamespace(id=generation_id, revision=target_revision)
            identity.prepare_singletons(generation)
            identity.prepare(generation)
        db.rollback()
        eligible_id = seal_snapshot(factory, run_id, 'SINGLETON_SOURCE',
            {'profile': snapshot['profile'], 'survivorship_policy': asdict(policy), 'workflow_context': metadata},
            {'records': plan.iter_eligible_records()}, operation_id=operation_id)
        with factory() as read_db:
            eligible_hash = read_db.query(WorkflowArtifact.content_hash).filter_by(id=eligible_id).scalar()
        coverage = plan.counts()
        plan_meta = {'run_id': run_id, 'target_cluster_revision': target_revision, 'generation_id': generation_id,
            'snapshot_id': source_id, 'snapshot_hash': snapshot.row.content_hash, 'engine_identity': publication_identity(),
            'source_golden_revision': source_golden_revision, 'source_artifact_id': eligible_id, 'source_hash': eligible_hash,
            'coverage': coverage, 'memberships_hash': memberships_hash(plan),
            'eligible_ids_hash': digest_sequence(r['id'] for r in plan.iter_eligible_records())}
        plan_id = seal_snapshot(factory, run_id, 'CLUSTER_PLAN', plan_meta,
            {'groups': plan.iter_groups(), 'members': ({'id': rid, 'group_id': gid} for rid, gid in plan.connection.execute(
                'SELECT id,group_id FROM plan_members ORDER BY id')),
             'excluded': plan.iter_excluded(),
             'pairs': (unpack(payload) for (payload,) in plan.connection.execute('SELECT payload FROM pairs ORDER BY id')),
             'review_groups': plan.iter_reviews(),
             'conflicts': (unpack(payload) for (payload,) in plan.connection.execute('SELECT payload FROM plan_issues ORDER BY id'))},
            operation_id=operation_id)
        with factory() as read_db:
            run_for_page = read_db.get(ResolutionRun, run_id)
            page = streamed_groups_page(read_db, run_for_page, plan_id, 0, 25)
            plan_hash = read_db.query(WorkflowArtifact.content_hash).filter_by(id=plan_id).scalar()
        result = {'publication': coverage, 'groups': page['items'], 'groups_next_cursor': page['next_cursor'],
            'conflicts': plan.issues(), 'conflict_count': plan.connection.execute('SELECT COUNT(*) FROM plan_issues').fetchone()[0],
            'pending_regions': plan.pending_summaries(), 'source_decision_revision': metadata['decision_revision'],
            'source_assessment_id': metadata.get('assessment_id'), 'source_golden_revision': source_golden_revision,
            'allow_partial': metadata.get('allow_partial', False), 'decision_snapshot_id': source_id,
            'plan_artifact_id': plan_id, 'plan_hash': plan_hash, 'plan_storage_hash': plan_hash,
            'review_limits': {'normal': policy.max_cluster_size, 'reviewed': policy.max_reviewed_cluster_size,
                              'pair_checks': policy.max_reviewed_pair_checks_per_union}, 'streamed_preview': True}
        result['preview_token'] = digest({'snapshot': snapshot.row.content_hash, 'preview': result})
        preview_id = seal(factory, run_id, 'CLUSTER_PREVIEW', result)
        with factory() as read_db:
            validate_streamed_snapshot(read_db, read_db.get(ResolutionRun, run_id), metadata)
        with factory.begin() as publish_db:
            run, op, task = job_identity(publish_db, operation_id)
            if run.golden_revision != source_golden_revision or run.cluster_revision + 1 != target_revision:
                raise ValueError('WORKFLOW_REVISION_CHANGED')
            if run.workflow_revision != revision:
                op.error_code = 'CLUSTER_PREVIEW_STALE'
            op.result = {**result, 'preview_artifact_id': preview_id}
            op.progress = {'processed': len(snapshot.records), 'total': len(snapshot.records), 'unit': 'records'}
            finish(publish_db, run, op, task, 'DECIDE_REVIEW')


def streamed_review_page(db, run, op, fingerprint, kind, offset, limit):
    from itertools import combinations, islice

    from fastapi import HTTPException

    from masterone.mdm.pipeline.pair_assessment import assess_pair
    from masterone.mdm.workflow_assessment_reads import active_assessment
    with open_snapshot(db, op.result['plan_artifact_id'], run.id, 'CLUSTER_PLAN') as plan:
        if plan.row.content_hash != op.result.get('plan_storage_hash'):
            raise ValueError('CLUSTER_PLAN_CHANGED')
        try:
            draft = plan.lookup('review_groups', fingerprint)
        except KeyError:
            raise HTTPException(404, 'Review group not found') from None
        if draft.get('approvable') is False:
            raise HTTPException(404, 'Review group not found')
        ids = sorted(draft['member_record_ids'])
        records = {r['id']: r for r in draft['records']}
        if len(ids) > 100 or set(ids) != set(records):
            raise ValueError('WORKFLOW_ARTIFACT_INVALID')
        if kind == 'members':
            total = len(ids)
            items = [{'record_id': rid, 'external_id': records[rid].get('external_id'),
                'source_system': records[rid].get('source_system'), 'name': records[rid]['raw_fields'].get('name'),
                'fields': records[rid]['raw_fields']} for rid in ids[offset:offset+limit]]
        else:
            total = len(ids)*(len(ids)-1)//2
            observed = {tuple(sorted((p['left_id'], p['right_id']))): p for p in draft['pairs']}
            items = []
            with engine_context(draft['profile']):
                for a, b in islice(combinations(ids, 2), offset, offset+limit):
                    baseline = observed.get((a,b))
                    computed = baseline is None
                    baseline = baseline or assess_pair(records[a], records[b], draft.get('field_frequencies', {}))
                    items.append({**baseline, 'baseline': baseline, 'computed_only': computed})
        assessment = active_assessment(db, run.id)
        return {'items': items, 'total': total, 'next_cursor': str(offset+len(items)) if offset+len(items)<total else None,
            'fingerprint': fingerprint, 'stale': op.result['source_decision_revision'] != run.decision_revision
                or op.result['source_assessment_id'] != (assessment.id if assessment else None)}
