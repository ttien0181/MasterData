"""Exact-member, evidence-bound exceptions for missing direct bridge evidence."""
from contextlib import closing
from time import perf_counter

from masterone.mdm.generation_staging import digest


def approval_fingerprint(snapshot, record_ids):
    ids = set(record_ids)
    return digest({'records': sorted((r for r in snapshot['records'] if r['id'] in ids), key=lambda r: r['id']),
        'pairs': sorted(({'pair': p, 'decision': snapshot['overlays'].get(p['id'], 'UNDECIDED'),
                         'reason': snapshot.get('review_reasons', {}).get(p['id'])}
                        for p in snapshot['pairs'] if {p['left_id'], p['right_id']} <= ids),
                       key=lambda p: p['pair']['id']),
        'negative_pairs': sorted(p for p in snapshot['negative_pairs'] if set(p) <= ids),
        'assessment_id': snapshot.get('assessment_id'), 'profile': snapshot.get('profile'),
        'execution_identity': snapshot.get('execution_identity')})


def validate_group(snapshot, record_ids, *, review_reason=None, frequencies=None, guard_budget=None):
    from masterone.mdm.engine_adapter import feature
    from masterone.mdm.pipeline.cluster_guards import check_component, has_blocking_violation, run_cluster_guards
    from masterone.mdm.pipeline.cluster_policy import GuardBudget, load_policy, reviewed_policy
    from masterone.mdm.pipeline.decision_store import DecisionStore
    from masterone.mdm.pipeline.matching import value_frequencies

    ids = set(record_ids)
    by_id = {r['id']: r for r in snapshot['records']}
    policy = load_policy(legacy=not feature('cluster_safety_v2_enabled'))
    if len(ids) != len(record_ids) or not 2 <= len(ids) <= policy.max_reviewed_cluster_size or not ids <= by_id.keys():
        return 'GROUP_MEMBERS_INVALID'
    if len(ids) > policy.max_cluster_size and not (review_reason or '').strip():
        return 'GROUP_APPROVAL_REASON_REQUIRED'
    if (review_reason or '').strip():
        policy = reviewed_policy(policy)
    adjacency = {rid: set() for rid in ids}
    internal = [p for p in snapshot['pairs'] if {p['left_id'], p['right_id']} <= ids]
    for pair in internal:
        if snapshot['overlays'].get(pair['id']) == 'ACCEPTED':
            adjacency[pair['left_id']].add(pair['right_id'])
            adjacency[pair['right_id']].add(pair['left_id'])
    visited, pending = set(), [min(ids)]
    while pending:
        rid = pending.pop()
        if rid not in visited:
            visited.add(rid)
            pending.extend(adjacency[rid] - visited)
    if visited != ids:
        return 'GROUP_ACCEPTED_LINKS_REQUIRED'
    with closing(DecisionStore(memory=len(ids) <= 20)) as store:
        for pair in internal:
            decision = snapshot['overlays'].get(pair['id'], 'UNDECIDED')
            proposed = bool((review_reason or '').strip()) and decision in ('UNDECIDED', 'DEFERRED')
            store.add({**pair, 'user_decision': 'ACCEPTED' if proposed else decision,
                       'review_reason': review_reason if proposed else snapshot.get('review_reasons', {}).get(pair['id'])})
        store.seal()
        links = frozenset(tuple(sorted(p)) for p in snapshot['negative_pairs'])
        vetoes = frozenset(tuple(sorted((p['left_id'], p['right_id']))) for p in internal
                          if snapshot['overlays'].get(p['id']) in ('REJECTED', 'NON_MATCH'))
        if not feature('cluster_safety_v2_enabled'):
            first = min(ids)
            violations = run_cluster_guards(frozenset({first}), frozenset(ids - {first}), by_id,
                                           negative_pairs=links, pairs=internal, policy=policy)
            if has_blocking_violation(violations):
                return next(v.reason for v in violations if v.severity == 'BLOCK')
        return check_component(ids, by_id, store, policy, guard_budget if guard_budget is not None else GuardBudget(),
                               frequencies if frequencies is not None else value_frequencies(snapshot['records']), links, vetoes,
                               allow_missing_evidence=True)


def group_snapshot(db, run, record_ids, *, _context=None):
    """Read bounded reviewed members and internal evidence, using current profile."""
    from masterone.mdm.generation_staging import config_identity
    from masterone.mdm.models import ResolutionEngineSelection, ResolutionNegativeEdge, ResolutionPair, ResolutionRecord
    from masterone.mdm.pipeline.golden_v2 import record_payload
    from masterone.mdm.pipeline.pair_assessment import assessment_record
    from masterone.mdm.workflow_assessment import assessment_code_identity
    from masterone.mdm.workflow_assessment_reads import active_assessment, effective_result
    from masterone.mdm.workflow_models import WorkflowPair
    from masterone.mdm.workflow_service import conflict

    if _context:
        if (_context['session'] is not db or _context['transaction'] is not db.get_transaction()
                or _context['run_id'] != run.id):
            raise ValueError('GROUP_BATCH_CONTEXT_CHANGED')
        assessment, profile, policy_version, execution_identity = (_context[k] for k in
            ('assessment', 'profile', 'policy_version', 'execution_identity'))
    else:
        assessment = active_assessment(db, run.id)
        selected = db.get(ResolutionEngineSelection, run.id)
        if selected is None:
            conflict('WORKFLOW_PROFILE_CHANGED')
        configuration = assessment.configuration if assessment else {}
        if assessment and (configuration['source_profile_identity'] != digest(selected.configuration)
                           or configuration['assessment_code_identity'] != assessment_code_identity()):
            conflict('WORKFLOW_CONFIG_CHANGED')
        profile = configuration['profile'] if assessment else selected.configuration
        policy_version = configuration['policy_version'] if assessment else run.policy_version
        from masterone.mdm.engine_adapter import engine_context
        with engine_context(profile):
            execution_identity = digest(config_identity(policy_version))
        if _context is not None:
            _context.update(session=db, transaction=db.get_transaction(), run_id=run.id,
                assessment=assessment, profile=profile, policy_version=policy_version, execution_identity=execution_identity)
    rows = db.query(ResolutionRecord).filter(ResolutionRecord.run_id == run.id, ResolutionRecord.id.in_(record_ids)).order_by(ResolutionRecord.id).all()
    records = [assessment_record(record_payload(r)) if assessment else record_payload(r) for r in rows]
    pairs = db.query(WorkflowPair).filter(WorkflowPair.run_id == run.id,
        WorkflowPair.baseline['left_id'].as_string().in_(record_ids),
        WorkflowPair.baseline['right_id'].as_string().in_(record_ids)).order_by(WorkflowPair.id).all()
    links = db.query(ResolutionNegativeEdge).filter(ResolutionNegativeEdge.run_id == run.id,
        ResolutionNegativeEdge.active.is_(True), ResolutionNegativeEdge.left_record_id.in_(record_ids),
        ResolutionNegativeEdge.right_record_id.in_(record_ids)).all()
    legacy_links = db.query(ResolutionPair).filter(ResolutionPair.run_id == run.id,
        ResolutionPair.user_decision == 'NON_MATCH', ResolutionPair.left_record_id.in_(record_ids),
        ResolutionPair.right_record_id.in_(record_ids)).all()
    from masterone.mdm.workflow_structure import structure_links
    scoped_structure_links = {p for p in structure_links(db, run.id) if set(p) <= set(record_ids)}
    return {'records': records, 'pairs': [effective_result(p) for p in pairs],
            'overlays': {p.id: p.decision for p in pairs},
            'review_reasons': {p.id: p.review_reason for p in pairs if p.review_reason},
            'negative_pairs': sorted({tuple(sorted((p.left_record_id, p.right_record_id))) for p in [*links, *legacy_links]}
                                     | scoped_structure_links),
            'assessment_id': assessment.id if assessment else None, 'profile': profile,
            'execution_identity': execution_identity, 'policy_version': policy_version}


def approve_group(db, run_id, request, actor, *, commit=True, _run=None, _context=None):
    started = perf_counter()
    performance = {}
    from masterone.mdm.engine_adapter import engine_context
    from masterone.mdm.workflow_service import (
        audit,
        check_revision,
        conflict,
        enabled_run,
        new_operation,
        operation_read,
        replay,
    )
    from masterone.mdm.workflow_service import (
        review_blocking_operation as active_operation,
    )

    excluded = {key for key in ('fingerprint', 'preview_operation_id') if getattr(request, key) is None}
    payload = request.model_dump(exclude=excluded)
    if not request.reason.strip():
        conflict('GROUP_APPROVAL_REASON_REQUIRED')
    run = _run if _run is not None else enabled_run(db, run_id, actor)
    prior = replay(db, run_id, 'GROUP_APPROVAL', request.request_id, payload)
    if prior:
        return operation_read(prior)
    check_revision(run, payload)
    if run.workflow_state != 'DECIDE_REVIEW' or active_operation(db, run_id):
        conflict('DECISIONS_LOCKED')
    ids = sorted(request.record_ids)
    snapshot = group_snapshot(db, run, ids, _context=_context)
    performance['snapshot_seconds'] = perf_counter() - started
    if bool(request.fingerprint) != bool(request.preview_operation_id):
        conflict('GROUP_REVIEW_RECEIPT_REQUIRED')
    if request.fingerprint:
        from masterone.mdm.workflow_models import WorkflowOperation
        preview = db.query(WorkflowOperation).filter_by(id=request.preview_operation_id, run_id=run_id,
            operation_type='CLUSTER_PREVIEW', status='COMPLETED').one_or_none()
        if (preview is None or preview.result['source_decision_revision'] != run.decision_revision
                or preview.result['source_assessment_id'] != snapshot.get('assessment_id')
                or request.fingerprint != approval_fingerprint(snapshot, ids)):
            conflict('GROUP_REVIEW_CHANGED')
        from fastapi import HTTPException

        from masterone.mdm.workflow_cluster_preview import review_group_page
        try:
            members = review_group_page(db, run, preview.id, request.fingerprint, 'members', 100, 0)
        except HTTPException as error:
            if error.status_code != 404:
                raise
            conflict('GROUP_REVIEW_CHANGED')
        if members['stale'] or members['total'] != len(ids) or {m['record_id'] for m in members['items']} != set(ids):
            conflict('GROUP_REVIEW_CHANGED')
    with engine_context(snapshot['profile']):
        reason = validate_group(snapshot, ids, review_reason=request.reason.strip())
    performance['validated_at_seconds'] = perf_counter() - started
    if reason:
        conflict('GROUP_APPROVAL_BLOCKED', reason=reason)
    from masterone.mdm.workflow_assessment_reads import effective_result
    from masterone.mdm.workflow_models import WorkflowConflict, WorkflowPair
    from masterone.mdm.workflow_review import apply_decision
    members = set(ids)
    for pair in db.query(WorkflowPair).filter(WorkflowPair.run_id == run_id,
        WorkflowPair.baseline['left_id'].as_string().in_(ids), WorkflowPair.baseline['right_id'].as_string().in_(ids)):
        effective = effective_result(pair)
        if {effective['left_id'], effective['right_id']} <= members and pair.decision in ('UNDECIDED', 'DEFERRED'):
            apply_decision(db, run, pair, 'merge', actor, reason=request.reason)
            snapshot['overlays'][pair.id] = 'ACCEPTED'
            snapshot.setdefault('review_reasons', {})[pair.id] = request.reason.strip()
    conflicts = db.query(WorkflowConflict).filter_by(run_id=run_id, resolved=False)
    if db.bind.dialect.name == 'postgresql':
        from sqlalchemy import cast
        from sqlalchemy.dialects.postgresql import JSONB
        # Same member-subset test as below, executed before ORM hydration. Large
        # runs can contain thousands of unrelated bridge conflicts.
        conflicts = conflicts.filter(
            WorkflowConflict.payload['reason'].as_string() == 'CLUSTER_BRIDGE_MISSING_EVIDENCE',
            cast(WorkflowConflict.payload['record_ids'], JSONB).contained_by(ids))
    for row in conflicts:
        if row.payload.get('reason') == 'CLUSTER_BRIDGE_MISSING_EVIDENCE' and set(row.payload.get('record_ids', [])) <= members:
            row.resolved = True
    op = new_operation(db, run, 'GROUP_APPROVAL', request.request_id, payload, actor)
    op.status, op.stage = 'COMPLETED', 'completed'
    op.result = {'record_ids': ids, 'reason': request.reason.strip(),
                 'fingerprint': approval_fingerprint(snapshot, ids), 'operation_id': op.id,
                 'performance': {**performance, 'before_commit_seconds': perf_counter() - started}}
    # Approval affects the effective grouping decisions and invalidates a preview.
    run.decision_revision += 1
    run.workflow_revision += 1
    audit(db, run, actor, 'WORKFLOW_GROUP_APPROVAL', op.result)
    db.commit() if commit else db.flush()
    return operation_read(op)


def approve_groups(db, run_id, request, actor):
    """Atomic bounded batch; each group retains its normal receipt and audit."""
    from uuid import NAMESPACE_URL, uuid5

    from masterone.mdm.matching_foundation_service import authorize
    from masterone.mdm.workflow_contracts import GroupApprovalRequest
    from masterone.mdm.workflow_service import audit, check_revision, enabled_run, new_operation, operation_read, replay
    payload = request.model_dump()
    run = enabled_run(db, run_id, actor)
    prior = replay(db, run_id, 'BULK_GROUP_APPROVAL', request.request_id, payload)
    if prior:
        return operation_read(prior)
    authorize(db, actor, 'approve_pair', owner=run.owner_user_id)
    check_revision(run, payload)
    try:
        op = new_operation(db, run, 'BULK_GROUP_APPROVAL', request.request_id, payload, actor)
        # Synchronous receipt is invisible until the whole transaction commits;
        # it must not look like a queued worker that blocks its own children.
        op.status, op.stage = 'COMPLETED', 'completed'
        context, items = {}, []
        for index, item in enumerate(request.groups):
            child = GroupApprovalRequest(**item.model_dump(),
                request_id=str(uuid5(NAMESPACE_URL, op.id + ':' + str(index))),
                expected_workflow_revision=run.workflow_revision, expected_decision_revision=run.decision_revision)
            items.append(approve_group(db, run_id, child, actor, commit=False, _run=run, _context=context))
        op.status, op.stage = 'COMPLETED', 'completed'
        op.result = {'approved_count': len(items), 'items': items,
                     'workflow_revision': run.workflow_revision, 'decision_revision': run.decision_revision}
        audit(db, run, actor, 'WORKFLOW_BULK_GROUP_APPROVAL', {'operation_id': op.id, 'approved_count': len(items)})
        db.commit()
        return operation_read(op)
    except Exception:
        db.rollback()
        raise
