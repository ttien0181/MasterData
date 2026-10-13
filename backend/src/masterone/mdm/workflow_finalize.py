"""Explicit completion seal, followed by the existing guarded engine."""
from contextlib import closing
from copy import deepcopy
from hashlib import sha256
from types import SimpleNamespace

from masterone.mdm.engine_adapter import engine_context, feature
from masterone.mdm.generation_staging import config_identity, digest
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


def finalize_decisions(db, run_id, request, actor):
    # Keep the canonical payload for pre-upgrade full-publication receipts.
    payload = request.model_dump(exclude={'allow_partial'} if not request.allow_partial else set())
    run = enabled_run(db, run_id, actor)
    prior = replay(db, run_id, 'CLUSTER', request.request_id, payload)
    if prior:
        return operation_read(prior)
    check_revision(run, payload)
    blockers = completion_blockers(db, run_id)
    if payload.get('allow_partial'):
        blockers = [b for b in blockers if b['code'] not in ('PENDING_DECISIONS', 'CLUSTER_CONFLICT')]
    if blockers:
        conflict('DECISIONS_INCOMPLETE', blockers=blockers)
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
    if payload.get('allow_partial'):
        snapshot['allow_partial'] = True
        snapshot['known_conflicts'] = [{**c.payload, 'id': c.id, 'pair_id': c.pair_id} for c in
            db.query(WorkflowConflict).filter_by(run_id=run_id, resolved=False).order_by(WorkflowConflict.id)]
    if assessed:
        snapshot.update(assessment_id=assessed['assessment_id'], source_profile_identity=assessed['source_profile_identity'],
                        source_input_hash=assessed['source_input_hash'])
    # End the read transaction before sealing potentially large content.
    factory_bind = db.get_bind()
    db.rollback()
    from sqlalchemy.orm import sessionmaker
    artifact_id = seal(sessionmaker(factory_bind, expire_on_commit=False), run_id, 'DECISION_SNAPSHOT', snapshot)
    run = enabled_run(db, run_id, actor)
    prior = replay(db, run_id, 'CLUSTER', request.request_id, payload)
    if prior:
        return operation_read(prior)
    check_revision(run, payload)
    blockers = completion_blockers(db, run_id)
    if payload.get('allow_partial'):
        blockers = [b for b in blockers if b['code'] not in ('PENDING_DECISIONS', 'CLUSTER_CONFLICT')]
    if blockers:
        conflict('DECISIONS_INCOMPLETE', blockers=blockers)
    op = new_operation(db, run, 'CLUSTER', request.request_id, payload, actor)
    op.artifact_id = artifact_id
    enqueue(db, run, op)
    run.workflow_state = 'CLUSTER_QUEUED'
    run.workflow_revision += 1
    audit(db, run, actor, 'WORKFLOW_FINALIZE_DECISIONS', {'operation_id': op.id, 'snapshot_id': artifact_id, 'decision_revision': run.decision_revision})
    db.commit()
    return operation_read(op)


def cluster_snapshot(snapshot, run_id):
    from itertools import combinations

    from masterone.mdm.pipeline.cluster_guards import check_component, has_blocking_violation, run_cluster_guards
    from masterone.mdm.pipeline.cluster_policy import GuardBudget, load_policy
    from masterone.mdm.pipeline.clustering import build_proposals
    from masterone.mdm.pipeline.decision_semantics import ReviewDecision, evaluate_prepared
    from masterone.mdm.pipeline.decision_store import DecisionStore
    from masterone.mdm.pipeline.golden import build_golden
    from masterone.mdm.pipeline.golden_v2 import golden_payload
    from masterone.mdm.pipeline.matching import compare_and_score_rule, value_frequencies
    from masterone.mdm.resolution_pairs import reviewed_members
    records = snapshot['records']
    by_id = {r['id']: r for r in records}
    overlays = snapshot['overlays']
    pairs = deepcopy([p for p in snapshot['pairs'] if p['decision'] != 'INSUFFICIENT_EVIDENCE' or overlays.get(p['id']) == 'ACCEPTED'])
    vetoes = frozenset(tuple(sorted((p['left_id'], p['right_id']))) for p in snapshot['pairs'] if overlays.get(p['id']) in ('REJECTED', 'NON_MATCH'))
    links = frozenset(tuple(p) for p in snapshot['negative_pairs'])
    # A mergeable conclusion can still require explicit acceptance. Keep the
    # original evidence for conflict reporting; exclude pending manual edges
    # from automatic union before any component is formed.
    automatic_pairs = [{**p, 'decision': 'REVIEW'} if p['decision'] == 'MERGEABLE'
                       and p.get('triage_status') == 'REVIEW' and overlays.get(p['id']) != 'ACCEPTED'
                       else p for p in pairs]
    result = build_proposals(records, automatic_pairs, run_id, snapshot['policy_version'], negative_pairs=links, execution_blocked_pairs=vetoes)
    proposals = result['proposals']
    conflicts = []
    from masterone.mdm.workflow_group_approvals import approval_fingerprint, validate_group
    for approval in snapshot.get('group_approvals', []):
        ids = set(approval['record_ids'])
        if not approval.get('reason', '').strip() or approval.get('fingerprint') != approval_fingerprint(snapshot, ids):
            continue
        if validate_group(snapshot, sorted(ids)):
            continue
        # An exact exception may not swallow members of an outside automatic group.
        if any(ids.intersection(p['member_record_ids']) and not set(p['member_record_ids']) <= ids for p in proposals):
            continue
        proposals = [p for p in proposals if not ids.intersection(p['member_record_ids'])]
        proposals.append({'id': sha256(f"{run_id}:approved:{','.join(sorted(ids))}".encode()).hexdigest(),
            'member_record_ids': sorted(ids), 'status': 'REVIEW_APPROVED', 'origin': 'GROUP_REVIEW',
            'group_approval_id': approval['operation_id'], 'group_approval_reason': approval['reason'],
            'golden_fields': build_golden(sorted(ids), {rid: golden_payload(SimpleNamespace(**by_id[rid])) for rid in ids})})
    policy, budget = load_policy(legacy=not feature('cluster_safety_v2_enabled')), GuardBudget()
    with closing(DecisionStore()) as store:
        for pair in pairs:
            store.add({**pair, 'user_decision': overlays.get(pair['id'], 'UNDECIDED'),
                       'review_reason': snapshot.get('review_reasons', {}).get(pair['id'])})
        store.seal()
        frequencies = value_frequencies(records)
        for pair in sorted(pairs, key=lambda p: (-p.get('match_weight', p.get('score', 0)), p['id'])):
            if overlays.get(pair['id']) != 'ACCEPTED':
                # New guard triage must be acknowledged explicitly; automatic inputs remain unchanged.
                if pair['decision'] == 'MERGEABLE' and pair.get('triage_status') == 'REVIEW' and overlays.get(pair['id']) not in ('REJECTED', 'NON_MATCH'):
                    conflicts.append({'pair_id': pair['id'], 'reason': pair['triage_reason'],
                                      'record_ids': [pair['left_id'], pair['right_id']]})
                continue
            endpoints = {pair['left_id'], pair['right_id']}
            if any(endpoints <= set(p['member_record_ids']) for p in proposals):
                continue
            members = reviewed_members([SimpleNamespace(**p) for p in proposals], endpoints)
            reason = ''
            if any(tuple(sorted((a, b))) in vetoes | links for a in members for b in members if a != b):
                reason = 'CLUSTER_REVIEW_VETO'
            elif any(p['decision'] in ('BLOCKED', 'NOT_MERGEABLE', 'NON_MATCH') for p in pairs if {p['left_id'], p['right_id']} <= members):
                reason = 'CLUSTER_PAIR_PERMISSION_CONFLICT'
            if not reason and feature('decision_semantics_v2_enabled'):
                # Reuse the evaluator used by require_manual_merge, including absent candidate edges.
                saved = {tuple(sorted((p['left_id'], p['right_id']))): p for p in pairs}
                for a, b in combinations(sorted(members), 2):
                    prior = saved.get((a, b))
                    review = ReviewDecision(overlays.get(prior['id'], 'UNDECIDED')) if prior else ReviewDecision()
                    evaluated = compare_and_score_rule(by_id[a], by_id[b], set(), frequencies, links)
                    if not evaluate_prepared(evaluated, by_id[a], by_id[b], review=review, negative_pairs=links)[2].manual_allowed:
                        reason = 'CLUSTER_PAIR_PERMISSION_CONFLICT'
                        break
            if not reason and feature('cluster_safety_v2_enabled'):
                reason = check_component(members, by_id, store, policy, budget, frequencies, links, vetoes)
            elif not reason:
                violations = run_cluster_guards(frozenset({pair['left_id']}), frozenset(members - {pair['left_id']}), by_id,
                                               negative_pairs=links, pairs=pairs, policy=policy)
                if has_blocking_violation(violations):
                    reason = next(v.reason for v in violations if v.severity == 'BLOCK')
            if 'BUDGET' in reason:
                raise ValueError(reason)
            if reason:
                conflicts.append({'pair_id': pair['id'], 'reason': reason, 'record_ids': sorted(members)})
                continue
            proposals = [p for p in proposals if not set(p['member_record_ids']) & members]
            ids = sorted(members)
            proposals.append({'id': sha256(f'{run_id}:review:{",".join(ids)}'.encode()).hexdigest(),
                'member_record_ids': ids, 'status': 'REVIEW_APPROVED', 'origin': 'REVIEW',
                'golden_fields': build_golden(ids, {rid: golden_payload(SimpleNamespace(**by_id[rid])) for rid in ids})})
    # A conflict blocks its complete connected region, not disconnected valid groups.
    affected = {rid for c in conflicts for rid in c['record_ids']}
    changed = True
    while changed:
        before = len(affected)
        for proposal in proposals:
            if affected.intersection(proposal['member_record_ids']):
                affected.update(proposal['member_record_ids'])
        changed = before != len(affected)
    safe = [p for p in proposals if not affected.intersection(p['member_record_ids'])]
    return {'clusters': [] if conflicts else proposals, 'conflicts': conflicts, 'pairs': pairs,
            'safe_clusters': safe, 'conflict_record_ids': sorted(affected),
            'cluster_metrics': result.get('cluster_metrics', {})}


def partial_cluster_snapshot(snapshot, run_id):
    from masterone.mdm.workflow_partial import partial_snapshot
    return partial_snapshot(snapshot, run_id, cluster_snapshot)


def run_cluster(operation_id, factory, progress):
    from masterone.mdm.cluster_publication import with_singletons
    from masterone.mdm.workflow_jobs import finish, job_identity
    with factory.begin() as db:
        run, op, task = job_identity(db, operation_id)
        snapshot = load(db, op.artifact_id, run.id, 'DECISION_SNAPSHOT')
        run_id, revision, next_cluster = run.id, run.workflow_revision, run.cluster_revision + 1
        from masterone.mdm.workflow_assessment import validate_assessed_snapshot
        if not validate_assessed_snapshot(db, run_id, snapshot) and digest(db.get(ResolutionEngineSelection, run_id).configuration) != snapshot['profile_identity']:
            raise ValueError('WORKFLOW_PROFILE_CHANGED')
    with engine_context(snapshot['profile']):
        if snapshot.get('execution_identity') != digest(config_identity(snapshot['policy_version'])):
            raise ValueError('WORKFLOW_CONFIG_CHANGED')
        progress('cluster', 0, len(snapshot['records']))
        result = partial_cluster_snapshot(snapshot, run_id) if snapshot.get('allow_partial') else cluster_snapshot(snapshot, run_id)
        safe_records = [r for r in snapshot['records'] if r['id'] not in set(result['conflict_record_ids'])]
        result['clusters'] = with_singletons(safe_records, result['safe_clusters'], run_id, next_cluster)
        from masterone.mdm.workflow_partial import publication_summary
        result['publication'] = publication_summary(snapshot, result)
        result['cluster_metrics'].update({key: result['publication'][key] for key in
                                         ('covered_records', 'unresolved_records', 'total_records')})
        # Annotation binds to post-cluster evidence and uses the existing gateway/policy.
        from masterone.mdm.pipeline.semantic_address import run_annotations
        semantic = run_annotations(result['pairs'], lambda ids: {r['id']: r for r in snapshot['records'] if r['id'] in ids},
                                   run_id=run_id, factory=factory,
                                   parallel=factory.kw['bind'].dialect.name == 'postgresql')
        result['llm_stats'] = semantic.stats
        result['semantic_annotations'] = semantic.annotations
        result['workflow_context'] = {key: snapshot[key] for key in
            ('input_hash', 'profile_identity', 'execution_identity', 'policy_version', 'workflow_revision', 'decision_revision')}
        result['workflow_context']['snapshot_hash'] = digest(snapshot)
        result['workflow_context']['assessment_id'] = snapshot.get('assessment_id')
        artifact_id = seal(factory, run_id, 'CLUSTER', result)
    with factory.begin() as db:
        run, op, task = job_identity(db, operation_id)
        if run.workflow_revision != revision or run.decision_revision != snapshot['decision_revision']:
            raise ValueError('WORKFLOW_REVISION_CHANGED')
        if not validate_assessed_snapshot(db, run_id, snapshot) and digest(db.get(ResolutionEngineSelection, run_id).configuration) != snapshot['profile_identity']:
            raise ValueError('WORKFLOW_PROFILE_CHANGED')
        with engine_context(snapshot['profile']):
            if snapshot['execution_identity'] != digest(config_identity(snapshot['policy_version'])):
                raise ValueError('WORKFLOW_CONFIG_CHANGED')
        generation = WorkflowGeneration(run_id=run_id, revision=next_cluster, artifact_id=artifact_id)
        db.add(generation)
        db.flush()
        previous = db.query(WorkflowCluster).join(WorkflowGeneration).filter(WorkflowGeneration.run_id == run_id,
                   WorkflowGeneration.revision == run.cluster_revision).all() if run.cluster_revision else []
        previous_by_member = {rid: c for c in previous for rid in c.payload['member_record_ids']}
        for cluster in result['clusters']:
            row = WorkflowCluster(id=cluster['id'], generation_id=generation.id, payload=cluster, golden=cluster['golden_fields'])
            from masterone.mdm.golden_service import carry_choices, repair_confirmation
            ancestors = {previous_by_member[rid].id: previous_by_member[rid] for rid in cluster['member_record_ids'] if rid in previous_by_member}
            carry_choices(db, run, row, previous_clusters=list(ancestors.values()))
            if repair_confirmation(row, list(ancestors.values()), op.result):
                from masterone.mdm.golden_service import unresolved_extra_fields, validate_selections
                validate_selections(db, run, row, row.selections)
                row.confirmed = not unresolved_extra_fields(db, row)
            db.add(row)
            db.flush()
            for rid in cluster['member_record_ids']:
                db.add(WorkflowMember(generation_id=generation.id, cluster_id=row.id, record_id=rid))
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
        op.result = {**receipt, **publication, 'publication': publication,
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
