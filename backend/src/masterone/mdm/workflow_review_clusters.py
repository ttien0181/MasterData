"""Bounded, read-only cluster tasks; mutations use the existing pair batch flow."""
from itertools import combinations

from fastapi import HTTPException
from sqlalchemy import or_

from masterone.mdm.data_profile import record_read
from masterone.mdm.models import ResolutionRecord
from masterone.mdm.workflow_assessment_reads import effective_column, effective_result
from masterone.mdm.workflow_models import WorkflowPair
from masterone.mdm.workflow_review import negative_links, pair_read
from masterone.mdm.workflow_review_rules import review_required

MAX_PAIRS = 200
ACTIONABLE = ('MERGEABLE', 'REVIEW')
BLOCKED = ('NOT_MERGEABLE', 'BLOCKED', 'NON_MATCH')


def missing_pair_evidence(db, run, ids, missing):
    """Evaluate absent edges read-only, using the assessment's global frequencies."""
    from contextlib import closing
    from hashlib import sha256

    from masterone.mdm.engine_adapter import engine_context
    from masterone.mdm.pipeline.cluster_guards import check_component
    from masterone.mdm.pipeline.cluster_policy import GuardBudget, load_policy
    from masterone.mdm.pipeline.decision_store import DecisionStore
    from masterone.mdm.pipeline.matching import _contact_items
    from masterone.mdm.pipeline.pair_assessment import assess_prepared_pair
    from masterone.mdm.workflow_assessment_reads import active_assessment
    from masterone.mdm.workflow_group_approvals import group_snapshot
    from masterone.mdm.workflow_models import WorkflowArtifact

    assessment = active_assessment(db, run.id)
    if assessment is None:
        return [], False
    version = db.query(WorkflowArtifact.payload['contact_frequency_version'].as_integer()).filter_by(
        id=assessment.artifact_id, run_id=run.id).scalar()
    if version != 1:
        return [], False  # Historical assessments need a reassessment; never use local-only counts.
    snapshot = group_snapshot(db, run, sorted(ids))
    by_id = {r['id']: r for r in snapshot['records']}
    if by_id.keys() != ids:
        return [], False
    keys = [(field, value) for field in ('phone', 'email') for value in sorted({
        item['value'] for r in by_id.values() for item in _contact_items(r, field) if item.get('value')})]
    if len(keys) > 256:
        return [], False
    frequencies = {'phone': {}, 'email': {}}
    if keys:
        counts = db.query(*(WorkflowArtifact.payload['contact_frequencies'][field][value].as_integer()
                            for field, value in keys)).filter_by(id=assessment.artifact_id, run_id=run.id).one()
        for (field, value), count in zip(keys, counts):
            frequencies[field][value] = count or 0
    links = frozenset(snapshot['negative_pairs'])
    results, safe = [], True
    with engine_context(snapshot['profile']):
        policy = load_policy()
        for a, b in sorted(missing):
            result = assess_prepared_pair(by_id[a], by_id[b], frequencies, links)
            result['id'] = sha256(f'{run.id}:read-only:{a}:{b}'.encode()).hexdigest()
            with closing(DecisionStore()) as store:
                store.add(result)
                store.seal()
                reason = check_component({a, b}, by_id, store, policy, GuardBudget(), frequencies, links)
            safe = safe and not reason and result['decision'] in ACTIONABLE
            results.append({'pair_id': result['id'], 'baseline': result, 'decision': 'UNDECIDED',
                            'revision': 0, 'computed_only': True, 'score_unit': 'rule',
                            'eligibility': {'manual_allowed': not bool(reason), 'auto_allowed': False,
                                            'blocked_reasons': [reason] if reason else []}})
    return results, safe


def review_cluster(db, run, pair_id):
    from masterone.mdm.pipeline.cluster_policy import load_policy
    policy = load_policy()
    normal_limit = policy.max_cluster_size
    seed = db.query(WorkflowPair).filter_by(run_id=run.id, id=pair_id).one_or_none()
    if seed is None:
        raise HTTPException(404, 'Pair not found')
    left = WorkflowPair.baseline['left_id'].as_string()
    right = WorkflowPair.baseline['right_id'].as_string()
    conclusion = effective_column()['decision'].as_string()
    ids = {seed.baseline['left_id'], seed.baseline['right_id']}
    frontier, complete = set(ids), True
    # Expand only actionable links. Each round and response has a hard bound;
    # exceeding it never permits accepting a partial component as a whole group.
    for _ in range(normal_limit):
        rows = db.query(WorkflowPair).filter(WorkflowPair.run_id == run.id,
            or_(left.in_(frontier), right.in_(frontier)),
            ~WorkflowPair.decision.in_(('REJECTED', 'NON_MATCH')),
            or_(conclusion.is_(None), ~conclusion.in_(BLOCKED)),
            or_(conclusion.in_(ACTIONABLE), WorkflowPair.decision.in_(('ACCEPTED', 'DEFERRED')))
        ).order_by(WorkflowPair.id).limit(MAX_PAIRS + 1).all()
        if len(rows) > MAX_PAIRS:
            complete = False
            break
        links = negative_links(db, run.id, rows)
        neighbors = {p.baseline[k] for p in rows for k in ('left_id', 'right_id')
                     if tuple(sorted((p.baseline['left_id'], p.baseline['right_id']))) not in links}
        if len(ids | neighbors) > normal_limit:
            complete = False
            break
        frontier = neighbors - ids
        ids.update(neighbors)
        if not frontier:
            break
    else:
        complete = False
    rows = db.query(WorkflowPair).filter(WorkflowPair.run_id == run.id,
        left.in_(ids), right.in_(ids)).order_by(WorkflowPair.id).limit(MAX_PAIRS + 1).all()
    if len(rows) > MAX_PAIRS:
        complete = False
    rows = rows[:MAX_PAIRS]
    links = negative_links(db, run.id, rows)
    records = [record_read(r) for r in db.query(ResolutionRecord).filter(
        ResolutionRecord.run_id == run.id, ResolutionRecord.id.in_(ids)).order_by(ResolutionRecord.id)]
    reasons, pairs, pending = [], [], []
    if not complete:
        reasons.append('CLUSTER_REVIEW_TOO_LARGE')
    if len(records) != len(ids):
        reasons.append('CLUSTER_REVIEW_MISSING_RECORD')
    endpoints = {tuple(sorted((p.baseline['left_id'], p.baseline['right_id']))) for p in rows}
    missing = set(combinations(sorted(ids), 2)) - endpoints
    additional = []
    if missing and complete:
        additional, safe_missing = missing_pair_evidence(db, run, ids, missing)
        if not safe_missing:
            reasons.append('CLUSTER_REVIEW_MISSING_PAIR')
    if len(endpoints) != len(rows):
        reasons.append('CLUSTER_REVIEW_MISSING_PAIR')
    for row in rows:
        item = pair_read(row)
        if tuple(sorted((row.baseline['left_id'], row.baseline['right_id']))) in links:
            item['eligibility'] = dict(manual_allowed=False, auto_allowed=False,
                                       blocked_reasons=['STEWARD_NEGATIVE_DECISION'])
        baseline = effective_result(row)
        if row.decision in ('REJECTED', 'NON_MATCH') or not item['eligibility']['manual_allowed']:
            reasons.append('CLUSTER_REVIEW_VETO')
        if baseline.get('decision') not in ACTIONABLE:
            reasons.append('CLUSTER_REVIEW_WEAK_PAIR')
        if review_required(row.decision, baseline):
            pending.append(row.id)
        pairs.append(item)
    reasons = sorted(set(reasons))
    from masterone.mdm.generation_staging import digest
    token = digest(dict(run_id=run.id, assessment_id=seed.assessment_id,
                        decision_revision=run.decision_revision, records=records, pairs=pairs,
                        additional_evidence=additional, negative_links=sorted(links), blocked_reasons=reasons,
                        complete=complete))
    return dict(seed_pair_id=pair_id, complete=complete, records=records, pairs=pairs, additional_evidence=additional,
                review_scope_token=token,
                pending_pair_ids=pending, batch_allowed=not reasons and bool(pending),
                blocked_reasons=reasons, decision_revision=run.decision_revision,
                assessment_id=seed.assessment_id, normal_member_limit=normal_limit,
                reviewed_member_limit=policy.max_reviewed_cluster_size)
