"""S1 immutable partition constraints and existing v2 publication orchestration."""
from copy import deepcopy
from itertools import product

from fastapi import HTTPException

from masterone.mdm.generation_staging import digest


def partition_pairs(members, partitions):
    if (len(partitions) != 2 or any(not p for p in partitions)
            or len(set(members)) != len(members)):
        raise ValueError('INVALID_PARTITION')
    flat = [rid for p in partitions for rid in p]
    if len(flat) != len(set(flat)) or set(flat) != set(members):
        raise ValueError('INVALID_PARTITION')
    return {tuple(sorted(pair)) for pair in product(*partitions)}


def active_constraints(db, run_id, exclude_id=None):
    from sqlalchemy import exists, select
    from sqlalchemy.orm import aliased

    from masterone.mdm.matching_foundation_models import StructureConstraint
    release = aliased(StructureConstraint)
    released = exists(select(release.id).where(release.run_id == run_id,
        release.kind == 'REMERGE_RELEASE', release.status == 'RELEASED',
        release.supersedes_id == StructureConstraint.id))
    query = db.query(StructureConstraint).filter(StructureConstraint.run_id == run_id,
        StructureConstraint.kind == 'SEPARATION', StructureConstraint.status == 'PREPARED', ~released)
    if exclude_id is not None:
        query = query.filter(StructureConstraint.id != exclude_id)
    return query.yield_per(512)


def structure_links(db, run_id, exclude_id=None):
    links = set()
    for row in active_constraints(db, run_id, exclude_id):
        value = row.evidence_ref['partition_manifest']
        if digest(value) != row.partition_manifest_hash:
            raise ValueError('STRUCTURE_CONSTRAINT_INTEGRITY')
        links.update(partition_pairs(value['members'], value['partitions']))
    return links


def project_snapshot(snapshot, intent):
    value = deepcopy(snapshot)
    value['structure_intent'] = deepcopy(intent)
    value['structure_original_negative_pairs'] = deepcopy(snapshot['negative_pairs'])
    cross = partition_pairs(intent['members'], intent['partitions'])
    negative = set(map(tuple, snapshot['negative_pairs']))
    if intent['kind'] == 'SPLIT':
        negative |= cross
        for pair in value['pairs']:
            if (tuple(sorted((pair['left_id'], pair['right_id']))) in cross
                    and (value['overlays'].get(pair['id']) == 'ACCEPTED'
                         or value['overlays'].get(pair['id']) in ('UNDECIDED', 'DEFERRED')
                         and pair.get('decision') == 'MERGEABLE')):
                value['overlays'][pair['id']] = 'REJECTED'
                value['review_reasons'][pair['id']] = intent['reason']
    else:
        negative -= set(map(tuple, intent['release_pairs']))
        existing = {p['id'] for p in value['pairs']}
        if not set(intent['bridge_pair_ids']) <= existing:
            raise ValueError('BRIDGE_NOT_IN_CANDIDATE_UNIVERSE')
        for pid in intent['bridge_pair_ids']:
            value['overlays'][pid] = 'ACCEPTED'
            value['review_reasons'][pid] = intent['reason']
    value['negative_pairs'] = sorted(negative)
    value['group_approvals'] = [approval for approval in value.get('group_approvals', [])
                               if not set(approval['record_ids']).intersection(intent['members'])]
    return value


def validate_publication(result, intent):
    members = set(intent['members'])
    expected = ({frozenset(p) for p in intent['partitions']} if intent['kind'] == 'SPLIT'
                else {frozenset(members)})
    actual = [set(c['member_record_ids']) for c in result['clusters'] if members.intersection(c['member_record_ids'])]
    if (any(not ids <= members for ids in actual) or {frozenset(ids) for ids in actual} != expected
            or sum(map(len, actual)) != len(members)
            or result['conflicts'] or result['publication']['unresolved_records']):
        raise ValueError('STRUCTURE_PUBLICATION_INVALID')


def partition_approvals(snapshot, intent, operation_id):
    from masterone.mdm.workflow_group_approvals import approval_fingerprint, validate_group
    targets = intent['partitions'] if intent['kind'] == 'SPLIT' else [intent['members']]
    receipts = []
    for members in targets:
        if len(members) < 2:
            continue
        reason = validate_group(snapshot, members, review_reason=intent['reason'])
        if reason:
            raise ValueError('STRUCTURE_PARTITION_APPROVAL_INVALID:' + reason)
        receipts.append({'record_ids': members, 'reason': intent['reason'], 'operation_id': operation_id,
                         'fingerprint': approval_fingerprint(snapshot, members), 'origin': 'STRUCTURE'})
    return receipts


def _run(db, run_id, actor):
    from masterone.mdm.matching_foundation_service import authorize, require_flag
    from masterone.mdm.workflow_service import active_operation, conflict, enabled_run
    authorize(db, actor, 'manage_entity_structure')
    require_flag('entity_structure_enabled')
    run = enabled_run(db, run_id, actor)
    if active_operation(db, run_id) or run.workflow_state not in ('GOLDEN_REVIEW', 'FINALIZED'):
        conflict('STRUCTURE_CURRENT_COMPLETE_GENERATION_REQUIRED')
    return run


def build_preview(db, run, body, kind):
    from masterone.mdm.engine_adapter import engine_context
    from masterone.mdm.pipeline.cluster_policy import load_policy
    from masterone.mdm.workflow_cluster_reads import current_generation, generation_metadata
    from masterone.mdm.workflow_generation_reads import GenerationReader
    from masterone.mdm.workflow_group_approvals import group_snapshot, validate_group
    from masterone.mdm.workflow_models import WorkflowPair
    from masterone.mdm.workflow_service import check_revision, conflict
    if kind == 'SPLIT' and (body.get('bridge_pair_ids') or body.get('constraint_id')):
        raise HTTPException(422, {'code': 'SPLIT_REMERGE_FIELDS_FORBIDDEN'})
    if kind == 'REMERGE' and body.get('partitions') is not None:
        raise HTTPException(422, {'code': 'REMERGE_PARTITIONS_INFERRED_FROM_CURRENT_CLUSTERS'})
    check_revision(run, body)
    generation = current_generation(db, run)
    if (generation.storage_version == 'workflow_parquet_v2'
            or generation_metadata(db, run, generation)['publication_status'] != 'COMPLETE'):
        conflict('STRUCTURE_UNSUPPORTED_OR_PARTIAL_GENERATION')
    reader = GenerationReader(db, run, generation)
    cluster_ids = body['cluster_ids']
    if len(cluster_ids) != (1 if kind == 'SPLIT' else 2) or len(set(cluster_ids)) != len(cluster_ids):
        raise HTTPException(422, {'code': 'STRUCTURE_CLUSTER_SCOPE_INVALID'})
    groups = [reader.row(cid) for cid in cluster_ids]
    partitions = body['partitions'] if kind == 'SPLIT' else [g.payload['member_record_ids'] for g in groups]
    members = sorted(rid for group in groups for rid in group.payload['member_record_ids'])
    try:
        cross = partition_pairs(members, partitions)
    except ValueError as exc:
        raise HTTPException(422, {'code': str(exc)}) from exc
    snapshot = group_snapshot(db, run, members)
    # Group snapshot's legacy SQL links do not contain structure provenance.
    snapshot['negative_pairs'] = sorted(set(map(tuple, snapshot['negative_pairs'])) | structure_links(db, run.id))
    intent = {'kind': kind, 'members': members, 'partitions': [sorted(p) for p in partitions],
              'reason': body['reason'].strip(), 'release_pairs': [], 'bridge_pair_ids': body.get('bridge_pair_ids', []),
              'constraint_id': body.get('constraint_id'), 'generation_id': generation.id,
              'cluster_ids': cluster_ids, 'pair_revisions': {}, 'superseded': []}
    blocked = []
    internal = {p.id: p for p in db.query(WorkflowPair).filter_by(run_id=run.id)
                if {p.baseline['left_id'], p.baseline['right_id']} <= set(members)}
    intent['pair_revisions'] = {pid: p.revision for pid, p in internal.items()}
    if kind == 'SPLIT':
        for pid, pair in internal.items():
            from masterone.mdm.workflow_assessment_reads import effective_result
            if (tuple(sorted((pair.baseline['left_id'], pair.baseline['right_id']))) in cross
                    and (pair.decision == 'ACCEPTED' or pair.decision in ('UNDECIDED', 'DEFERRED')
                         and effective_result(pair).get('decision') == 'MERGEABLE')):
                intent['superseded'].append({'pair_id': pid, 'before': pair.decision,
                                             'revision': pair.revision, 'after_revision': pair.revision + 1})
    else:
        target = next((r for r in active_constraints(db, run.id) if r.id == intent['constraint_id']), None)
        if target is None:
            blocked.append('ACTIVE_SPLIT_CONSTRAINT_REQUIRED')
        elif {frozenset(p) for p in target.evidence_ref['partition_manifest']['partitions']} != {frozenset(p) for p in partitions}:
            blocked.append('CONSTRAINT_PARTITION_SCOPE_MISMATCH')
        else:
            # Difference preserves overlapping independent constraints and Deny.
            remaining = structure_links(db, run.id, target.id)
            from masterone.mdm.models import ResolutionNegativeEdge, ResolutionPair
            independent = {tuple(sorted((p.left_record_id, p.right_record_id))) for p in
                           [*db.query(ResolutionNegativeEdge).filter_by(run_id=run.id, active=True),
                            *db.query(ResolutionPair).filter_by(run_id=run.id, user_decision='NON_MATCH')]}
            intent['release_pairs'] = sorted(cross - remaining - independent)
            origins = {r['pair_id']: r for r in target.evidence_ref.get('superseded', [])}
            if not intent['bridge_pair_ids']:
                blocked.append('EXISTING_BRIDGE_REQUIRED')
            for pid in intent['bridge_pair_ids']:
                pair = internal.get(pid)
                if pair is None or tuple(sorted((pair.baseline['left_id'], pair.baseline['right_id']))) not in cross:
                    blocked.append('BRIDGE_NOT_IN_CANDIDATE_UNIVERSE')
                elif pair.decision == 'NON_MATCH' or (pair.decision == 'REJECTED'
                     and (pid not in origins or pair.revision != origins[pid]['after_revision'])):
                    blocked.append('INDEPENDENT_REVIEW_VETO')
                else:
                    from types import SimpleNamespace

                    from masterone.mdm.workflow_review import eligibility
                    if not eligibility(SimpleNamespace(**{**pair.__dict__, 'decision': 'UNDECIDED'}))['manual_allowed']:
                        blocked.append('BRIDGE_EVIDENCE_INSUFFICIENT')
    projected = project_snapshot(snapshot, intent) if not blocked else snapshot
    with engine_context(snapshot['profile']):
        policy = load_policy()
        if len(members) > policy.max_reviewed_cluster_size:
            blocked.append('CLUSTER_SIZE_LIMIT')
        targets = partitions if kind == 'SPLIT' else [members]
        for ids in targets:
            if len(ids) > 1 and not blocked:
                reason = validate_group(projected, ids, review_reason=intent['reason'])
                if reason:
                    blocked.append(reason)
    versions = {key: getattr(run, key) for key in ('workflow_revision', 'decision_revision', 'cluster_revision', 'golden_revision')}
    evidence_hash = digest(snapshot)
    receipt = {'intent': intent, 'versions': versions, 'evidence_hash': evidence_hash,
               'assessment_id': snapshot.get('assessment_id'), 'blocked_reasons': sorted(set(blocked)),
               'affected_golden': [{'cluster_id': g.id, 'confirmed': g.confirmed} for g in groups],
               'cross_candidate_pair_ids': sorted(pid for pid, p in internal.items()
                   if tuple(sorted((p.baseline['left_id'], p.baseline['right_id']))) in cross)}
    receipt['preview_token'] = digest(receipt)
    return receipt


def preview(db, run_id, body, actor, kind):
    from masterone.mdm.workflow_service import audit, new_operation, operation_read, replay
    run = _run(db, run_id, actor)
    prior = replay(db, run_id, 'STRUCTURE_PREVIEW', body['request_id'], body)
    if prior:
        return operation_read(prior)
    receipt = build_preview(db, run, body, kind)
    op = new_operation(db, run, 'STRUCTURE_PREVIEW', body['request_id'], body, actor)
    op.status, op.stage, op.result = 'COMPLETED', 'preview_completed', receipt
    audit(db, run, actor, 'WORKFLOW_STRUCTURE_PREVIEW', {'operation_id': op.id, 'kind': kind,
                                                     'blocked_reasons': receipt['blocked_reasons']})
    db.commit()
    return operation_read(op)


def execute(db, run_id, body, actor):
    from masterone.mdm.engine_adapter import engine_context
    from masterone.mdm.matching_foundation_models import StructureConstraint

    # Replays remain available after the operation changes workflow state.
    from masterone.mdm.matching_foundation_service import authorize, require_flag
    from masterone.mdm.workflow_finalize import decision_snapshot
    from masterone.mdm.workflow_models import WorkflowArtifact, WorkflowOperation, WorkflowPair
    from masterone.mdm.workflow_service import (
        audit,
        check_revision,
        conflict,
        enabled_run,
        enqueue,
        new_operation,
        operation_read,
        replay,
    )
    authorize(db, actor, 'manage_entity_structure')
    require_flag('entity_structure_enabled')
    run = enabled_run(db, run_id, actor)
    payload = {**body, 'structure_request': True}
    prior = replay(db, run_id, 'CLUSTER', body['request_id'], payload)
    if prior:
        return operation_read(prior)
    run = _run(db, run_id, actor)
    check_revision(run, body)
    source = db.query(WorkflowOperation).filter_by(id=body['preview_operation_id'], run_id=run_id,
                                                 operation_type='STRUCTURE_PREVIEW', status='COMPLETED').one_or_none()
    if source is None or source.result.get('preview_token') != body['preview_token']:
        conflict('STRUCTURE_PREVIEW_INVALID')
    actual = build_preview(db, run, source.payload, source.result['intent']['kind'])
    if actual['preview_token'] != body['preview_token'] or actual['blocked_reasons']:
        conflict('STRUCTURE_PREVIEW_STALE_OR_BLOCKED', blocked_reasons=actual['blocked_reasons'])
    intent = actual['intent']
    payload['structure_intent'] = intent
    # Hash idempotency uses the public request, not regenerated intent.
    op = new_operation(db, run, 'CLUSTER', body['request_id'], {**body, 'structure_request': True}, actor)
    stored_payload = {**op.payload, 'structure_intent': intent}
    stored_payload['structure_preview_operation_id'] = stored_payload.pop('preview_operation_id')
    stored_payload['structure_preview_token'] = stored_payload.pop('preview_token')
    op.payload = stored_payload
    manifest = {'members': intent['members'], 'partitions': intent['partitions'], 'kind': intent['kind']}
    constraint = StructureConstraint(run_id=run.id, originating_operation_id=op.id, owner_user_id=actor,
        creator_id=actor, data_scope='owner-private', kind='SEPARATION' if intent['kind'] == 'SPLIT' else 'REMERGE_RELEASE',
        partition_manifest_uri='workflow-operation:' + op.id, partition_manifest_hash=digest(manifest),
        evidence_ref={'partition_manifest': manifest, 'reason': intent['reason'], 'preview_operation_id': source.id,
                      'preview_token': body['preview_token'], 'superseded': intent['superseded']},
        effective_revision=run.decision_revision + 1, status='PREPARED' if intent['kind'] == 'SPLIT' else 'RELEASED',
        supersedes_id=intent['constraint_id'] if intent['kind'] == 'REMERGE' else None)
    db.add(constraint)
    db.flush()
    changes = {item['pair_id']: 'REJECTED' for item in intent['superseded']}
    if intent['kind'] == 'REMERGE':
        changes.update({pid: 'ACCEPTED' for pid in intent['bridge_pair_ids']})
    for pid, target in changes.items():
        pair = db.query(WorkflowPair).filter_by(id=pid, run_id=run.id).one()
        if pair.revision != intent['pair_revisions'][pid]:
            conflict('STALE_PAIR_REVISION')
        before = {'decision': pair.decision, 'revision': pair.revision, 'reason': pair.review_reason}
        pair.decision, pair.review_reason, pair.automation = target, intent['reason'], None
        pair.revision += 1
        audit(db, run, actor, 'WORKFLOW_STRUCTURE_PAIR_SUPERSEDE', {'pair_id': pid, 'before': before,
             'after': {'decision': target, 'revision': pair.revision}, 'constraint_id': constraint.id,
             'operation_id': op.id, 'origin': 'STRUCTURE', 'human_direct_label': None})
    run.decision_revision += 1
    run.workflow_revision += 1
    snapshot = decision_snapshot(db, run)
    snapshot['group_approvals'] = [a for a in snapshot.get('group_approvals', [])
                                 if not set(a['record_ids']).intersection(intent['members'])]
    with engine_context(snapshot['profile']):
        approvals = partition_approvals(snapshot, intent, op.id)
    snapshot['group_approvals'].extend(approvals)
    artifact = WorkflowArtifact(run_id=run.id, phase='DECISION_SNAPSHOT', content_hash=digest(snapshot), payload=snapshot)
    db.add(artifact)
    db.flush()
    op.artifact_id, op.result = artifact.id, {'structure_kind': intent['kind'], 'constraint_id': constraint.id,
        'previous_generation_id': intent['generation_id'], 'affected_record_ids': intent['members'],
        'decision_revision': run.decision_revision, 'structure_group_approvals': approvals}
    enqueue(db, run, op)
    run.workflow_state = 'CLUSTER_QUEUED'
    audit(db, run, actor, 'WORKFLOW_STRUCTURE_INTENT', {'operation_id': op.id, 'constraint_id': constraint.id,
                                                     'kind': intent['kind'], 'reason': intent['reason']})
    db.commit()
    return operation_read(op)
