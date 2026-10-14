"""Explicit completion seal, followed by the existing guarded engine."""
from time import perf_counter
from types import SimpleNamespace

from masterone.mdm.engine_adapter import engine_context
from masterone.mdm.generation_staging import config_identity, digest, digest_sequence
from masterone.mdm.models import ResolutionEngineSelection, ResolutionRecord
from masterone.mdm.pipeline.golden_v2 import record_payload
from masterone.mdm.workflow_artifacts import load, seal
from masterone.mdm.workflow_models import (
    WorkflowArtifact,
    WorkflowCluster,
    WorkflowConflict,
    WorkflowGeneration,
    WorkflowMember,
    WorkflowOperation,
    WorkflowPair,
)
from masterone.mdm.workflow_service import (
    audit,
    check_revision,
    completion_blockers,
    conflict,
    enabled_run,
    enqueue,
    new_operation,
    operation_read,
    replay,
)


def decision_snapshot(db, run, *, allow_partial=False):
    run_id = run.id
    revision = run.workflow_revision
    source = db.query(WorkflowArtifact).filter_by(run_id=run_id, phase='DECIDE').order_by(WorkflowArtifact.created_at.desc()).first()
    records = [record_payload(r) for r in db.query(ResolutionRecord).filter_by(run_id=run_id).order_by(ResolutionRecord.id)]
    from masterone.mdm.workflow_assessment import assessment_snapshot
    assessed = assessment_snapshot(db, run, records)
    baseline = assessed if assessed else load(db, source.id if source else None, run_id, 'DECIDE')
    profile = db.get(ResolutionEngineSelection, run_id)
    if profile is None or (not assessed and (baseline['input_hash'] != digest(records) or baseline['profile_identity'] != digest(profile.configuration))):
        conflict('WORKFLOW_INPUT_CHANGED')
    effective_profile = assessed['profile'] if assessed else profile.configuration
    with engine_context(effective_profile):
        if baseline.get('execution_identity') != digest(config_identity(run.policy_version)):
            if not assessed or baseline.get('execution_identity') != digest(config_identity(baseline['policy_version'])):
                conflict('WORKFLOW_CONFIG_CHANGED')
    from masterone.mdm.pipeline.decision_semantics import load_cannot_links
    from masterone.mdm.workflow_assessment_reads import effective_result
    pair_rows = db.query(WorkflowPair).filter_by(run_id=run_id).order_by(WorkflowPair.id).all()
    snapshot = {'records': assessed['records'] if assessed else records, 'pairs': [effective_result(p) for p in pair_rows], 'profile': effective_profile,
        'overlays': {p.id: p.decision for p in pair_rows},
        'review_reasons': {p.id: p.review_reason for p in pair_rows if p.review_reason},
        'negative_pairs': sorted(load_cannot_links(db, run_id)), 'policy_version': baseline['policy_version'] if assessed else run.policy_version,
        'workflow_revision': revision, 'decision_revision': run.decision_revision,
        'input_hash': baseline['input_hash'], 'profile_identity': baseline['profile_identity']}
    snapshot['execution_identity'] = baseline['execution_identity']
    if allow_partial:
        snapshot['allow_partial'] = True
        snapshot['known_conflicts'] = [{**c.payload, 'id': c.id, 'pair_id': c.pair_id} for c in
            db.query(WorkflowConflict).filter_by(run_id=run_id, resolved=False).order_by(WorkflowConflict.id)]
    if assessed:
        snapshot.update(assessment_id=assessed['assessment_id'], source_profile_identity=assessed['source_profile_identity'],
                        source_input_hash=assessed['source_input_hash'])
    snapshot['group_approvals'] = [o.result for o in db.query(WorkflowOperation).filter_by(run_id=run_id, operation_type='GROUP_APPROVAL', status='COMPLETED').order_by(WorkflowOperation.created_at)]
    return snapshot


def validate_snapshot_current(db, run, snapshot):
    from masterone.mdm.pipeline.decision_semantics import load_cannot_links
    from masterone.mdm.workflow_assessment import validate_assessed_snapshot
    if not validate_assessed_snapshot(db, run.id, snapshot):
        profile = db.get(ResolutionEngineSelection, run.id)
        if profile is None or digest(profile.configuration) != snapshot['profile_identity']:
            raise ValueError('WORKFLOW_PROFILE_CHANGED')
        from masterone.mdm.workflow_snapshot_producers import record_stream
        if digest_sequence(record_stream(db, run.id)) != snapshot['input_hash']:
            raise ValueError('WORKFLOW_INPUT_CHANGED')
    if sorted(load_cannot_links(db, run.id)) != [tuple(p) for p in snapshot['negative_pairs']]:
        raise ValueError('WORKFLOW_NEGATIVE_LINKS_CHANGED')
    with engine_context(snapshot['profile']):
        if snapshot['execution_identity'] != digest(config_identity(snapshot['policy_version'])):
            raise ValueError('WORKFLOW_CONFIG_CHANGED')


def finalize_decisions(db, run_id, request, actor):
    excluded = {'allow_partial'} if not request.allow_partial else set()
    excluded |= {key for key in ('preview_operation_id', 'preview_token') if getattr(request, key) is None}
    payload = request.model_dump(exclude=excluded)
    run = enabled_run(db, run_id, actor)
    prior = replay(db, run_id, 'CLUSTER', request.request_id, payload)
    if prior:
        return operation_read(prior)
    check_revision(run, payload)
    blockers = completion_blockers(db, run_id)
    if request.allow_partial:
        blockers = [b for b in blockers if b['code'] not in ('PENDING_DECISIONS', 'CLUSTER_CONFLICT')]
    if blockers:
        conflict('DECISIONS_INCOMPLETE', blockers=blockers)
    from masterone.mdm.workflow_cluster_preview import checked_preview
    preview = checked_preview(db, run, request) if request.preview_operation_id or request.preview_token else None
    op = new_operation(db, run, 'CLUSTER', request.request_id, payload, actor)
    if preview:
        op.artifact_id = preview.result['decision_snapshot_id']
    enqueue(db, run, op)
    run.workflow_state = 'CLUSTER_QUEUED'
    run.workflow_revision += 1
    audit(db, run, actor, 'WORKFLOW_FINALIZE_DECISIONS', {'operation_id': op.id,
          'snapshot_id': op.artifact_id, 'decision_revision': run.decision_revision})
    db.commit()
    return operation_read(op)



def cluster_snapshot(snapshot, run_id, *, frequencies=None, index=None):
    from masterone.mdm.workflow_cluster_engine import compute_components
    return compute_components(snapshot, run_id, frequencies=frequencies, index=index)


def partial_cluster_snapshot(snapshot, run_id, *, frequencies=None, index=None):
    from masterone.mdm.workflow_cluster_index import SnapshotIndex
    from masterone.mdm.workflow_partial import partial_snapshot
    index = index or SnapshotIndex(snapshot, frequencies=frequencies)
    def compute(part, rid):
        return cluster_snapshot(part, rid, frequencies=index.frequencies)
    return partial_snapshot(snapshot, run_id, compute, index=index)


def compute_publication(snapshot, run_id, revision, *, previous_plan=None, virtual_singletons=False):
    from masterone.mdm.cluster_publication import with_singletons
    from masterone.mdm.workflow_cluster_index import SnapshotIndex
    from masterone.mdm.workflow_partial import publication_summary
    from masterone.mdm.workflow_plan_cache import reuse_regions
    index = SnapshotIndex(snapshot)
    frequencies = index.frequencies
    def compute(part, rid):
        part_index = index if part is snapshot else SnapshotIndex(part, frequencies=frequencies)
        return (partial_cluster_snapshot(part, rid, frequencies=frequencies, index=part_index) if part.get('allow_partial')
                else cluster_snapshot(part, rid, frequencies=frequencies, index=part_index))
    result = reuse_regions(snapshot, previous_plan, run_id, compute)
    if result is None:
        result = compute(snapshot, run_id)
        result['region_cache'] = {'reused_records': 0, 'recomputed_records': len(snapshot['records'])}
    from masterone.mdm.workflow_partial import candidate_region
    from masterone.mdm.workflow_review_rules import review_required
    pending = [p for p in snapshot['pairs'] if review_required(snapshot['overlays'].get(p['id'], 'UNDECIDED'), p)]
    excluded = set(result['conflict_record_ids']) | candidate_region(
        snapshot, {rid for p in pending for rid in (p['left_id'], p['right_id'])}, index=index)
    result['conflict_record_ids'] = sorted(excluded)
    if pending:
        result['unresolved_pair_ids'] = sorted(set(result.get('unresolved_pair_ids', [])) | {p['id'] for p in pending})
    result['safe_clusters'] = [c for c in result['safe_clusters'] if not excluded.intersection(c['member_record_ids'])]
    safe_records = [r for r in snapshot['records'] if r['id'] not in excluded]
    result['clusters'] = with_singletons(safe_records, result['safe_clusters'], run_id, revision,
                                         include_singletons=not virtual_singletons)
    for cluster in result['clusters']:
        cluster['source_snapshot_hash'] = digest([index.by_id[rid] for rid in sorted(cluster['member_record_ids'])])
    result['publication'] = publication_summary(snapshot, result)
    if virtual_singletons:
        multi_members = sum(len(c['member_record_ids']) for c in result['clusters'])
        singles = len(safe_records) - multi_members
        result['publication'].update(clusters=len(result['clusters']) + singles, singletons=singles,
            covered_records=len(safe_records), unresolved_records=len(excluded),
            unresolved_record_ids=sorted(excluded), publication_status='PARTIAL' if excluded or result['conflicts'] else 'COMPLETE')
    result['cluster_metrics'].update({key: result['publication'][key] for key in
                                     ('covered_records', 'unresolved_records', 'total_records')})
    return result


def run_cluster(operation_id, factory, progress):
    from masterone.config import get_settings
    if get_settings().workflow_parquet_enabled:
        from masterone.mdm.workflow_snapshot_producers import ensure_streamed_snapshot
        ensure_streamed_snapshot(operation_id, factory, progress)
    with factory() as db:
        op = db.get(WorkflowOperation, operation_id)
        artifact_format = db.query(WorkflowArtifact.payload['format'].as_string()).filter_by(id=op.artifact_id).scalar() if op.artifact_id else None
    if artifact_format == 'p119_workflow_snapshot_v2':
        from masterone.mdm.workflow_streamed_jobs import run_streamed_cluster
        return run_streamed_cluster(operation_id, factory, progress)
    from masterone.mdm.workflow_cluster_preview import enrich_groups, operation_snapshot, publication_plan
    from masterone.mdm.workflow_jobs import finish, job_identity
    started = perf_counter()
    snapshot = operation_snapshot(operation_id, factory, progress)
    performance = {'snapshot_seconds': perf_counter() - started}
    with factory.begin() as db:
        run, op, task = job_identity(db, operation_id)
        run_id, revision, next_cluster = run.id, run.workflow_revision, run.cluster_revision + 1
        repair_metadata = dict(op.result)
        started = perf_counter()
        validate_snapshot_current(db, run, snapshot)
        result = publication_plan(db, run, op, snapshot, next_cluster)
        performance.update(validation_seconds=perf_counter() - started, plan_reused=result is not None)
    with engine_context(snapshot['profile']):
        if snapshot.get('execution_identity') != digest(config_identity(snapshot['policy_version'])):
            raise ValueError('WORKFLOW_CONFIG_CHANGED')
        progress('cluster', 0, len(snapshot['records']))
        started = perf_counter()
        if result is None:
            result = compute_publication(snapshot, run_id, next_cluster, virtual_singletons=True)
            enrich_groups(result, snapshot)
        performance['compute_seconds'] = perf_counter() - started
        # Optional annotations are requested in their own durable worker job
        # after publication. Base memberships never wait on an AI provider.
        result['llm_stats'] = {'checked': 0, 'outbound_attempts': 0, 'stopped': 'SEPARATE_JOB'}
        result['semantic_annotations'] = {}
        result['workflow_context'] = {key: snapshot[key] for key in
            ('input_hash', 'profile_identity', 'execution_identity', 'policy_version', 'workflow_revision', 'decision_revision')}
        result['workflow_context']['snapshot_hash'] = digest(snapshot)
        result['workflow_context']['assessment_id'] = snapshot.get('assessment_id')
        result['local_evidence_version'] = 1
        from uuid import uuid4

        from masterone.mdm.workflow_entities import plan_entity_ids
        from masterone.mdm.workflow_generation_reads import prepare_virtual_publication
        generation_id = str(uuid4())
        with factory() as db:
            run, _, _ = job_identity(db, operation_id)
            source_payload = prepare_virtual_publication(db, run, result, snapshot, generation_id, repair_metadata)
            planned_generation = SimpleNamespace(revision=next_cluster)
            result['clusters'] = plan_entity_ids(db, run, planned_generation, result['clusters'])
        source_id = seal(factory, run_id, 'SINGLETON_SOURCE', source_payload)
        result['generation_coverage'] = {**result['publication'], 'source_artifact_id': source_id,
                                         'source_hash': digest(source_payload)}
        for key in ('unresolved_record_ids', 'unresolved_pair_ids', 'publication_status'):
            result['generation_coverage'].pop(key, None)
        artifact_id = seal(factory, run_id, 'CLUSTER', result)
    # Build once, instead of rescanning the entire artifact whenever a group opens.
    from collections import defaultdict
    evidence_by_cluster = defaultdict(list)
    membership = {rid: cluster['id'] for cluster in result['clusters'] for rid in cluster['member_record_ids']}
    from masterone.mdm.pipeline.semantic_address import with_annotations
    for pair in with_annotations(result['pairs'], result.get('semantic_annotations', {})):
        cid = membership.get(pair['left_id'])
        if cid is not None and membership.get(pair['right_id']) == cid:
            evidence_by_cluster[cid].append(pair)
    progress('publish-groups', 0, len(result['clusters']))
    publication_started = perf_counter()
    with factory.begin() as db:
        run, op, task = job_identity(db, operation_id)
        if run.workflow_revision != revision or run.decision_revision != snapshot['decision_revision']:
            raise ValueError('WORKFLOW_REVISION_CHANGED')
        validate_snapshot_current(db, run, snapshot)
        generation = WorkflowGeneration(id=generation_id, run_id=run_id, revision=next_cluster, artifact_id=artifact_id,
            storage_version='workflow_virtual_v2', coverage=result['generation_coverage'], singleton_confirmation={})
        if result.get('virtual_confirmation_default'):
            generation.singleton_confirmation = {'default_confirmed': True, 'generation_id': generation_id,
                'source_hash': result['generation_coverage']['source_hash'], 'actor_user_id': op.actor_user_id,
                'reason': 'UNCHANGED_FROZEN_SINGLETONS_AFTER_REPAIR'}
        db.add(generation)
        db.flush()
        from masterone.mdm.workflow_entities import assign_entity_ids, previous_clusters_by_member
        previous_by_member = previous_clusters_by_member(db, run, membership)
        groups = assign_entity_ids(db, run, generation, result['clusters'], actor=op.actor_user_id, record_versions=False)
        cluster_batch, member_batch = [], []

        def flush_batch():
            if cluster_batch:
                db.add_all(cluster_batch)
                db.flush()  # FK targets must exist before their member batch.
                db.add_all(member_batch)
                db.flush()
                cluster_batch.clear()
                member_batch.clear()

        for cluster in groups:
            local_payload = {**cluster, 'local_evidence_version': 1,
                             'pair_evidence': evidence_by_cluster[cluster['id']],
                             'workflow_context': result['workflow_context'], 'llm_stats': result['llm_stats']}
            row = WorkflowCluster(id=cluster['id'], generation_id=generation.id, payload=local_payload, golden=cluster['golden_fields'])
            from masterone.mdm.golden_service import carry_choices, repair_confirmation
            ancestors = {previous_by_member[rid].id: previous_by_member[rid] for rid in cluster['member_record_ids'] if rid in previous_by_member}
            carry_choices(db, run, row, previous_clusters=list(ancestors.values()))
            if repair_confirmation(row, list(ancestors.values()), op.result):
                from masterone.mdm.golden_service import unresolved_extra_fields, validate_selections
                validate_selections(db, run, row, row.selections)
                row.confirmed = not unresolved_extra_fields(db, row)
            from masterone.mdm.models import ResolutionEntity
            from masterone.mdm.workflow_entities import selected_entity_version
            selected_entity_version(db, db.get(ResolutionEntity, row.payload['entity_id']), generation, row, actor=op.actor_user_id)
            cluster_batch.append(row)
            for rid in cluster['member_record_ids']:
                member_batch.append(WorkflowMember(generation_id=generation.id, cluster_id=row.id, record_id=rid))
            if len(cluster_batch) >= 500 or len(member_batch) >= 2000:
                flush_batch()
        flush_batch()
        run.cluster_revision = next_cluster
        run.golden_revision += 1
        for conflict_id in result.get('reevaluated_conflict_ids', []):
            prior_conflict = db.get(WorkflowConflict, conflict_id)
            if prior_conflict is not None and prior_conflict.run_id == run_id:
                prior_conflict.resolved = True
        retained = set(result.get('retained_conflict_ids', []))
        retained_payloads = [{k: v for k, v in c.items() if k != 'id'} for c in snapshot.get('known_conflicts', []) if c['id'] in retained]
        publication = {key: value for key, value in result['publication'].items()
                       if key not in ('unresolved_record_ids', 'unresolved_pair_ids')}
        blockers = [{**{key: blocker[key] for key in ('pair_id', 'reason') if key in blocker},
                     'record_ids': blocker.get('record_ids', [])[:100],
                     'record_count': len(blocker.get('record_ids', []))}
                    for blocker in result['conflicts'][:25]]
        # Pollable operations keep bounded summaries. Complete coverage IDs and
        # conflict evidence remain in the immutable artifact and conflict rows.
        receipt = {key: value for key, value in op.result.items()
                   if key not in ('unresolved_record_ids', 'unresolved_pair_ids')}
        performance['publication_seconds'] = perf_counter() - publication_started
        op.result = {**receipt, **publication, 'publication': publication, 'performance': performance,
                     'blockers': blockers, 'blocker_count': len(result['conflicts'])}
        if result['conflicts'] or result['publication']['unresolved_records']:
            for blocker in result['conflicts']:
                if blocker not in retained_payloads:
                    db.add(WorkflowConflict(run_id=run_id, operation_id=op.id, pair_id=blocker['pair_id'], payload=blocker))
            finish(db, run, op, task, 'DECIDE_REVIEW')
        else:
            finish(db, run, op, task, 'GOLDEN_REVIEW')
        if op.payload.get('repair_operation_id'):
            receipt = db.get(WorkflowOperation, op.payload['repair_operation_id'])
            if receipt.operation_type == 'APPLY_REPAIR':
                from masterone.mdm.workflow_resolution import versions
                receipt.result = {**receipt.result, 'undo_versions': versions(run)}
        # Fence again at the end of row publication, before marking the attempt successful.
        from masterone.mdm.job_queue import current_claim, database_now, utc
        if current_claim():
            from masterone.mdm.models import ResolutionJobAttempt
            attempt = db.get(ResolutionJobAttempt, current_claim().attempt_id)
            if utc(attempt.lease_expires_at) <= database_now(db) or utc(attempt.deadline_at) <= database_now(db):
                from masterone.mdm.job_queue import OwnershipLostError
                raise OwnershipLostError('JOB_OWNER_FENCED')
