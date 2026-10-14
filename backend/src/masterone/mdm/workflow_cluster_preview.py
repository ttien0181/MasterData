"""Queued previews share the immutable snapshot and guarded publication engine."""
from collections import defaultdict
from heapq import heappush, heapreplace
from pathlib import Path
from time import perf_counter

from masterone.mdm.engine_adapter import engine_context
from masterone.mdm.generation_staging import config_identity, digest
from masterone.mdm.workflow_artifacts import load, seal
from masterone.mdm.workflow_service import conflict


class PreviewInvalidatedError(ValueError):
    pass


def enrich_groups(computed, snapshot):
    records = {r['id']: r for r in snapshot['records']}
    for group in computed['clusters']:
        members = []
        for rid in group['member_record_ids']:
            record = records[rid]
            fields = {field: record.get('raw_fields', {}).get(field) for field in
                      ('name', 'phone', 'email', 'dob', 'government_id', 'address')}
            members.append({'record_id': rid, 'external_id': record.get('external_id'),
                            'source_system': record.get('source_system'), 'name': fields['name'], 'fields': fields})
        group['preview_members'] = members[:25]
        group['member_count'] = len(group['member_record_ids'])
        group['display_name'] = next((str(m['name']) for m in members if m['name']),
                                     members[0].get('external_id') or group['member_record_ids'][0])
    from itertools import combinations

    from masterone.mdm.workflow_cluster_index import SnapshotIndex
    index = SnapshotIndex(snapshot, frequencies={})
    pages = {}
    for group in [*computed.get('review_groups', []),
                  *(g for g in computed['clusters'] if len(g['member_record_ids']) > 25)]:
        ids = group['member_record_ids']
        if group.get('approvable') is False:
            continue
        fingerprint = group['fingerprint']
        members = [{'record_id': rid, 'external_id': records[rid].get('external_id'),
                    'source_system': records[rid].get('source_system'),
                    'name': records[rid].get('raw_fields', {}).get('name'),
                    'fields': {field: records[rid].get('raw_fields', {}).get(field) for field in
                               ('name', 'phone', 'email', 'dob', 'government_id', 'address')}} for rid in ids]
        pages[fingerprint] = {'members': members, 'records': [records[rid] for rid in ids],
                              'frequencies': group.get('field_frequencies', {}), 'profile': snapshot.get('profile', {}),
                              'evidence': [{'left_id': a, 'right_id': b, **({'baseline': index.pairs[a, b]}
                                           if (a, b) in index.pairs else {})} for a, b in combinations(ids, 2)]}
        group['preview_members'] = members[:25]
    computed['review_group_pages'] = pages
def conflict_summaries(computed, snapshot):
    records = {r['id']: r for r in snapshot['records']}
    results = []
    for issue in computed['conflicts'][:25]:
        members = [
            {'record_id': rid, 'external_id': records[rid].get('external_id'),
             'source_system': records[rid].get('source_system'),
             'name': records[rid].get('raw_fields', {}).get('name'),
             'fields': {field: records[rid].get('raw_fields', {}).get(field) for field in
                        ('name', 'phone', 'email', 'dob', 'government_id', 'address')}}
            for rid in issue['record_ids'][:25] if rid in records]
        draft = next((g for g in computed.get('review_groups', [])
                      if g['member_record_ids'] == issue['record_ids'][:100]), {})
        results.append({**issue, 'record_ids': issue['record_ids'][:100], 'preview_members': members,
                        'member_count': len(issue['record_ids']),
                        **{k: draft[k] for k in ('fingerprint', 'approvable') if k in draft}})
    return results


def group_summary(group):
    return {key: group[key] for key in ('id', 'entity_id', 'member_record_ids', 'member_count', 'fingerprint', 'singleton', 'status',
                                      'display_name', 'preview_members', 'golden_fields') if key in group}


def preview_groups(db, run, operation_id, limit=25, offset=0):
    from fastapi import HTTPException

    from masterone.mdm.workflow_artifacts import list_page
    from masterone.mdm.workflow_assessment_reads import active_assessment
    from masterone.mdm.workflow_models import WorkflowOperation
    op = db.query(WorkflowOperation).filter_by(id=operation_id, run_id=run.id,
        operation_type='CLUSTER_PREVIEW', status='COMPLETED').one_or_none()
    if op is None or not op.result.get('plan_artifact_id'):
        raise HTTPException(404, 'Preview result not found')
    if op.result.get('streamed_preview'):
        from masterone.mdm.workflow_streamed_preview import streamed_groups_page
        page = streamed_groups_page(db, run, op.result['plan_artifact_id'], offset, limit)
    else:
        page = list_page(db, op.result['plan_artifact_id'], run.id, 'CLUSTER_PLAN',
                         ('computed', 'clusters'), offset, limit, expected_hash=op.result.get('plan_storage_hash'))
    assessment = active_assessment(db, run.id)
    return {**page, 'items': [group_summary(c) for c in page['items']],
            'source_decision_revision': op.result['source_decision_revision'],
            'source_assessment_id': op.result['source_assessment_id'],
            'stale': op.result['source_decision_revision'] != run.decision_revision
                     or op.result['source_assessment_id'] != (assessment.id if assessment else None)}


def review_group_page(db, run, operation_id, fingerprint, kind, limit=25, offset=0):
    """Read a bounded page of frozen members or direct/additional evidence."""
    from fastapi import HTTPException

    from masterone.mdm.workflow_artifacts import list_page, read_value
    from masterone.mdm.workflow_models import WorkflowOperation
    op = db.query(WorkflowOperation).filter_by(id=operation_id, run_id=run.id,
        operation_type='CLUSTER_PREVIEW', status='COMPLETED').one_or_none()
    if op is None or not op.result.get('plan_artifact_id') or kind not in ('members', 'evidence'):
        raise HTTPException(404, 'Review group not found')
    if op.result.get('streamed_preview'):
        from masterone.mdm.workflow_streamed_preview import streamed_review_page
        return streamed_review_page(db, run, op, fingerprint, kind, offset, limit)
    path = ('computed', 'review_group_pages', fingerprint)
    try:
        page = list_page(db, op.result['plan_artifact_id'], run.id, 'CLUSTER_PLAN', (*path, kind), offset, limit,
                         expected_hash=op.result.get('plan_storage_hash'))
        if kind == 'evidence':
            inputs = list_page(db, op.result['plan_artifact_id'], run.id, 'CLUSTER_PLAN', (*path, 'records'), 0, 100)
            records = {r['id']: r for r in inputs['items']}
            metadata = read_value(db, op.result['plan_artifact_id'], run.id, 'CLUSTER_PLAN', path,
                                  expected_hash=op.result.get('plan_storage_hash'))
            from masterone.mdm.pipeline.pair_assessment import assess_pair
            items = []
            with engine_context(metadata['profile']):
                for entry in page['items']:
                    observed = entry.get('baseline')
                    baseline = observed or assess_pair(records[entry['left_id']], records[entry['right_id']], metadata['frequencies'])
                    items.append({**baseline, 'computed_only': observed is None, 'baseline': baseline})
            page['items'] = items
    except KeyError as error:
        raise HTTPException(404, 'Review group not found') from error
    from masterone.mdm.workflow_assessment_reads import active_assessment
    assessment = active_assessment(db, run.id)
    return {**page, 'fingerprint': fingerprint,
            'stale': op.result['source_decision_revision'] != run.decision_revision
                     or op.result['source_assessment_id'] != (assessment.id if assessment else None)}


def publication_identity():
    """A cached result is valid only under the engine that computed it."""
    from hashlib import sha256
    base = Path(__file__).parent
    files = ('workflow_finalize.py', 'workflow_partial.py', 'workflow_group_approvals.py',
             'workflow_plan_cache.py', 'workflow_cluster_preview.py',
             'workflow_cluster_index.py', 'workflow_cluster_engine.py', 'pipeline/decision_store.py',
             'workflow_entities.py', 'golden_service.py', 'workflow_generation_reads.py',
             'workflow_snapshot_storage.py', 'workflow_snapshot_views.py', 'workflow_snapshot_index.py',
             'workflow_snapshot_producers.py', 'workflow_streamed_plan.py', 'workflow_streamed_entities.py',
             'workflow_streamed_jobs.py', 'workflow_streamed_preview.py', 'workflow_finalization.py',
             'cluster_publication.py', 'pipeline/clustering.py', 'pipeline/cluster_guards.py',
             'pipeline/cluster_policy.py', 'pipeline/decision_semantics.py',
             'pipeline/matching.py', 'pipeline/golden.py', 'pipeline/golden_v2.py',
             'pipeline/survivorship_policy.py')
    return digest({name: sha256((base / name).read_bytes()).hexdigest() for name in files})


def previous_plan(db, run_id, operation_id):
    """Cache misses/corrupt historical results always fall back to full validation."""
    from masterone.mdm.workflow_models import WorkflowOperation
    op = db.query(WorkflowOperation).filter(WorkflowOperation.run_id == run_id,
        WorkflowOperation.operation_type == 'CLUSTER_PREVIEW', WorkflowOperation.status == 'COMPLETED',
        WorkflowOperation.id != operation_id).order_by(WorkflowOperation.created_at.desc()).first()
    if op is None or not op.result.get('plan_artifact_id'):
        return None
    try:
        plan = load(db, op.result['plan_artifact_id'], run_id, 'CLUSTER_PLAN')
        if plan['run_id'] != run_id or plan['engine_identity'] != publication_identity() or digest(plan) != op.result['plan_hash']:
            return None
        snapshot = load(db, op.result['decision_snapshot_id'], run_id, 'DECISION_SNAPSHOT')
        if plan['snapshot_hash'] != digest(snapshot):
            return None
        return {'snapshot': snapshot, 'computed': plan['computed']}
    except (ValueError, KeyError):
        return None


def publication_plan(db, run, op, snapshot, revision):
    """Verify the exact reviewed result; historical previews fall back to compute."""
    from types import SimpleNamespace
    if not op.payload.get('preview_operation_id'):
        return None
    preview = checked_preview(db, run, SimpleNamespace(
        preview_operation_id=op.payload['preview_operation_id'], preview_token=op.payload['preview_token'],
        allow_partial=op.payload.get('allow_partial', False)))
    receipt = preview.result
    if not receipt.get('plan_artifact_id'):
        return None
    if receipt['decision_snapshot_id'] != op.artifact_id:
        raise ValueError('CLUSTER_PLAN_SNAPSHOT_CHANGED')
    plan = load(db, receipt['plan_artifact_id'], run.id, 'CLUSTER_PLAN')
    if (digest(plan) != receipt['plan_hash'] or plan['run_id'] != run.id
            or plan['target_cluster_revision'] != revision or plan['snapshot_hash'] != digest(snapshot)
            or plan['engine_identity'] != publication_identity()):
        raise ValueError('CLUSTER_PLAN_CHANGED')
    return plan['computed']


def operation_snapshot(operation_id, factory, progress):
    from masterone.mdm.models import ResolutionRun
    from masterone.mdm.workflow_finalize import decision_snapshot
    from masterone.mdm.workflow_jobs import job_identity

    with factory.begin() as db:
        run, op, _ = job_identity(db, operation_id)
        run_id, revision, decision_revision = run.id, run.workflow_revision, run.decision_revision
        if op.artifact_id:
            return load(db, op.artifact_id, run_id, 'DECISION_SNAPSHOT')
        partial = op.payload.get('allow_partial', False)
    progress('snapshot', 0, 1)
    # Heavy reads and hashes are outside the run-lock transaction and HTTP handler.
    with factory() as db:
        snapshot = decision_snapshot(db, db.get(ResolutionRun, run_id), allow_partial=partial)
    artifact_id = seal(factory, run_id, 'DECISION_SNAPSHOT', snapshot)
    with factory.begin() as db:
        run, op, _ = job_identity(db, operation_id)
        if ((run.workflow_revision, run.decision_revision) != (revision, decision_revision)
                or snapshot['decision_revision'] != decision_revision):
            error = PreviewInvalidatedError if op.operation_type == 'CLUSTER_PREVIEW' else ValueError
            raise error('WORKFLOW_REVISION_CHANGED')
        op.artifact_id = artifact_id
    progress('snapshot', 1, 1)
    return snapshot


def pending_regions(snapshot):
    from masterone.mdm.workflow_partial import candidate_adjacency
    from masterone.mdm.workflow_review_rules import review_required

    adjacency, pending_by_record = candidate_adjacency(snapshot), defaultdict(set)
    for pair in snapshot['pairs']:
        a, b = pair['left_id'], pair['right_id']
        decision = snapshot['overlays'].get(pair['id'], 'UNDECIDED')
        if review_required(decision, pair):
            pending_by_record[a].add(pair['id'])
            pending_by_record[b].add(pair['id'])
    visited, regions = set(), []
    for seed in sorted(pending_by_record):
        if seed in visited:
            continue
        ids, queue = set(), [seed]
        while queue:
            rid = queue.pop()
            if rid in ids:
                continue
            ids.add(rid)
            queue.extend(adjacency[rid] - ids)
        visited.update(ids)
        pairs = {pid for rid in ids for pid in pending_by_record[rid]}
        region = {'record_ids': sorted(ids)[:100], 'record_count': len(ids),
                  'pending_pair_ids': sorted(pairs)[:25], 'pending_pair_count': len(pairs)}
        item = (len(ids), seed, region)
        if len(regions) < 25:
            heappush(regions, item)
        elif item[:2] > regions[0][:2]:
            heapreplace(regions, item)
    return sorted((r for _, _, r in regions), key=lambda r: (-r['record_count'], r['record_ids']))


def run_preview(operation_id, factory, progress):
    from masterone.config import get_settings
    if get_settings().workflow_parquet_enabled:
        from masterone.mdm.workflow_snapshot_producers import ensure_streamed_snapshot
        ensure_streamed_snapshot(operation_id, factory, progress)
    from masterone.mdm.workflow_models import WorkflowArtifact, WorkflowOperation
    with factory() as db:
        op = db.get(WorkflowOperation, operation_id)
        artifact_format = db.query(WorkflowArtifact.payload['format'].as_string()).filter_by(id=op.artifact_id).scalar() if op.artifact_id else None
    if artifact_format == 'p119_workflow_snapshot_v2':
        from masterone.mdm.workflow_streamed_preview import run_streamed_preview
        return run_streamed_preview(operation_id, factory, progress)
    from masterone.mdm.workflow_finalize import compute_publication, validate_snapshot_current
    from masterone.mdm.workflow_jobs import finish, job_identity

    started = perf_counter()
    try:
        snapshot = operation_snapshot(operation_id, factory, progress)
    except PreviewInvalidatedError:
        with factory.begin() as db:
            run, op, task = job_identity(db, operation_id)
            op.error_code = 'CLUSTER_PREVIEW_STALE'
            op.result = {'stale': True}
            finish(db, run, op, task, 'DECIDE_REVIEW')
        return
    snapshot_seconds = perf_counter() - started
    with factory.begin() as db:
        run, op, _ = job_identity(db, operation_id)
        run_id = run.id
        next_cluster, snapshot_id = run.cluster_revision + 1, op.artifact_id
    with factory() as db:
        cached = previous_plan(db, run_id, operation_id)
    with engine_context(snapshot['profile']):
        if snapshot['execution_identity'] != digest(config_identity(snapshot['policy_version'])):
            raise ValueError('WORKFLOW_CONFIG_CHANGED')
        progress('preview-groups', 0, len(snapshot['records']))
        started = perf_counter()
        computed = (compute_publication(snapshot, run_id, next_cluster, previous_plan=cached) if cached
                    else compute_publication(snapshot, run_id, next_cluster))
        enrich_groups(computed, snapshot)
        compute_seconds = perf_counter() - started
    from types import SimpleNamespace

    from masterone.mdm.workflow_entities import plan_entity_ids
    with factory() as db:
        run, _, _ = job_identity(db, operation_id)
        computed['clusters'] = plan_entity_ids(db, run, SimpleNamespace(revision=next_cluster), computed['clusters'])
    plan = {'run_id': run_id, 'target_cluster_revision': next_cluster, 'snapshot_hash': digest(snapshot),
            'engine_identity': publication_identity(), 'computed': computed}
    plan_id = seal(factory, run_id, 'CLUSTER_PLAN', plan)
    from masterone.mdm.workflow_models import WorkflowArtifact
    with factory() as db:
        storage_hash = db.query(WorkflowArtifact.content_hash).filter_by(id=plan_id).scalar()
    result = {'publication': {k: v for k, v in computed['publication'].items()
                              if k not in ('unresolved_record_ids', 'unresolved_pair_ids')},
        'groups': [group_summary(c) for c in computed['clusters'][:25]],
        'groups_next_cursor': '25' if len(computed['clusters']) > 25 else None,
        'conflicts': conflict_summaries(computed, snapshot), 'conflict_count': len(computed['conflicts']),
        'pending_regions': pending_regions(snapshot), 'source_decision_revision': snapshot['decision_revision'],
        'source_assessment_id': snapshot.get('assessment_id'), 'allow_partial': snapshot.get('allow_partial', False),
        'decision_snapshot_id': snapshot_id, 'plan_artifact_id': plan_id, 'plan_hash': digest(plan),
        'plan_storage_hash': storage_hash,
        'performance': {'snapshot_seconds': snapshot_seconds, 'compute_seconds': compute_seconds,
                        **computed['region_cache']}}
    from masterone.mdm.pipeline.cluster_policy import load_policy
    with engine_context(snapshot['profile']):
        policy = load_policy()
    result['review_limits'] = {'normal': policy.max_cluster_size, 'reviewed': policy.max_reviewed_cluster_size,
                               'pair_checks': policy.max_reviewed_pair_checks_per_union}
    result['preview_token'] = digest({'snapshot': digest(snapshot), 'preview': result})
    preview_artifact = seal(factory, run_id, 'CLUSTER_PREVIEW', result)
    with factory.begin() as db:
        run, op, task = job_identity(db, operation_id)
        if run.decision_revision != snapshot['decision_revision']:
            op.error_code = 'CLUSTER_PREVIEW_STALE'
            # Deny adds a cannot-link. Validate all other source/config fences,
            # while retaining the obsolete receipt's original immutable links.
            from masterone.mdm.pipeline.decision_semantics import load_cannot_links
            validate_snapshot_current(db, run, {**snapshot, 'negative_pairs': sorted(load_cannot_links(db, run.id))})
        else:
            validate_snapshot_current(db, run, snapshot)
        op.result = {**result, 'preview_artifact_id': preview_artifact}
        op.progress = {'processed': len(snapshot['records']), 'total': len(snapshot['records']), 'unit': 'records'}
        finish(db, run, op, task, 'DECIDE_REVIEW')


def checked_preview(db, run, request):
    from masterone.mdm.workflow_assessment_reads import active_assessment
    from masterone.mdm.workflow_models import WorkflowOperation

    preview = db.query(WorkflowOperation).filter_by(id=request.preview_operation_id, run_id=run.id,
        operation_type='CLUSTER_PREVIEW', status='COMPLETED').one_or_none()
    if preview is None or request.preview_token != preview.result.get('preview_token'):
        conflict('CLUSTER_PREVIEW_INVALID')
    assessment = active_assessment(db, run.id)
    if (preview.result['source_decision_revision'] != run.decision_revision
            or preview.result['source_assessment_id'] != (assessment.id if assessment else None)
            or preview.result['allow_partial'] != request.allow_partial):
        conflict('CLUSTER_PREVIEW_STALE')
    sealed = load(db, preview.result['preview_artifact_id'], run.id, 'CLUSTER_PREVIEW')
    if sealed != {k: v for k, v in preview.result.items() if k != 'preview_artifact_id'}:
        conflict('CLUSTER_PREVIEW_INVALID')
    return preview
