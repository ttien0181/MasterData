"""Shared reviewed-component consistency with legacy automatic-engine adapters."""
from contextlib import closing
from copy import deepcopy
from hashlib import sha256
from types import SimpleNamespace

from masterone.mdm.engine_adapter import feature


def _field_frequencies(index, members):
    from masterone.mdm.pipeline.matching import _contact_items
    return {field: {value: index.frequencies.get(field, {}).get(value, 0)
                    for rid in members for item in _contact_items(index.by_id[rid], field)
                    if (value := item.get('value'))} for field in ('phone', 'email')}


def compute_components(snapshot, run_id, *, index=None, frequencies=None, guard_budget=None):
    from masterone.mdm.pipeline.decision_store import DecisionStore
    from masterone.mdm.workflow_cluster_index import SnapshotIndex
    index = index or SnapshotIndex(snapshot, frequencies=frequencies)
    with closing(DecisionStore()) as store:
        return _compute_components(snapshot, run_id, index, store, guard_budget)


def _compute_components(snapshot, run_id, index, store, guard_budget=None):
    from itertools import combinations

    from masterone.mdm.pipeline.cluster_guards import check_component, has_blocking_violation, run_cluster_guards
    from masterone.mdm.pipeline.cluster_policy import GuardBudget, load_policy
    from masterone.mdm.pipeline.clustering import build_proposals
    from masterone.mdm.pipeline.decision_semantics import ReviewDecision, evaluate_prepared
    from masterone.mdm.pipeline.golden import build_golden
    from masterone.mdm.pipeline.golden_v2 import golden_payload
    from masterone.mdm.pipeline.matching import compare_and_score_rule
    budget = guard_budget if guard_budget is not None else GuardBudget()
    budget_start = budget.checked
    records = snapshot['records']
    frequencies = index.frequencies
    by_id = index.by_id
    overlays = snapshot['overlays']
    pairs = deepcopy([p for p in snapshot['pairs'] if p['decision'] != 'INSUFFICIENT_EVIDENCE' or overlays.get(p['id']) == 'ACCEPTED'])
    vetoes = frozenset(tuple(sorted((p['left_id'], p['right_id']))) for p in snapshot['pairs'] if overlays.get(p['id']) in ('REJECTED', 'NON_MATCH'))
    links = frozenset(tuple(p) for p in snapshot['negative_pairs'])
    # Accepted edges enter the atomic reviewed path once. Pending manual edges
    # also stay out of automatic union; original evidence remains unchanged.
    automatic_pairs = [{**p, 'decision': 'REVIEW'} if p['decision'] == 'MERGEABLE'
                       and (overlays.get(p['id']) == 'ACCEPTED' or p.get('triage_status') == 'REVIEW')
                       else p for p in pairs]
    for pair in automatic_pairs:
        store.add({**pair, 'user_decision': overlays.get(pair['id'], 'UNDECIDED'),
                   'review_reason': snapshot.get('review_reasons', {}).get(pair['id'])})
    store.seal()
    result = build_proposals(records, store if feature('cluster_safety_v2_enabled') else automatic_pairs, run_id, snapshot['policy_version'], negative_pairs=links,
                             execution_blocked_pairs=vetoes, frequencies=frequencies, guard_budget=budget)
    automatic_checked = budget.checked - budget_start
    if feature('cluster_safety_v2_enabled'):
        # Keep post-guard automatic evidence, restoring the observed decision of
        # edges routed through the reviewed path. Indexed cache remains shared.
        pairs = [{**store.get_id(pair['id']), 'decision': pair['decision']} for pair in pairs]
    for pair in pairs:
        store.update({**pair, 'user_decision': overlays.get(pair['id'], 'UNDECIDED'),
                      'review_reason': snapshot.get('review_reasons', {}).get(pair['id'])})
    proposals = result['proposals']
    conflicts = []
    from masterone.mdm.workflow_group_approvals import approval_fingerprint, validate_group
    approval_groups = {p['id']: p for p in proposals}
    approval_members = {rid: p['id'] for p in proposals for rid in p['member_record_ids']}
    for approval in snapshot.get('group_approvals', []):
        ids = set(approval['record_ids'])
        if not ids <= by_id.keys():
            continue
        local = index.subset(ids)
        if not approval.get('reason', '').strip() or approval.get('fingerprint') != approval_fingerprint(local, ids):
            continue
        if validate_group(local, sorted(ids), review_reason=approval['reason'], frequencies=frequencies, guard_budget=budget):
            continue
        # An exact exception may not swallow members of an outside automatic group.
        roots = {approval_members[rid] for rid in ids if rid in approval_members}
        if any(not set(approval_groups[root]['member_record_ids']) <= ids for root in roots):
            continue
        for root in roots:
            approval_groups.pop(root)
        group = {'id': sha256(f"{run_id}:approved:{','.join(sorted(ids))}".encode()).hexdigest(),
            'member_record_ids': sorted(ids), 'status': 'REVIEW_APPROVED', 'origin': 'GROUP_REVIEW',
            'group_approval_id': approval['operation_id'], 'group_approval_reason': approval['reason'],
            'fingerprint': approval['fingerprint'],
            'field_frequencies': _field_frequencies(index, ids),
            'golden_fields': build_golden(sorted(ids), {rid: golden_payload(SimpleNamespace(**by_id[rid])) for rid in ids})}
        approval_groups[group['id']] = group
        for rid in ids:
            approval_members[rid] = group['id']
    proposals = list(approval_groups.values())
    approval_checked = budget.checked - budget_start - automatic_checked
    policy = load_policy(legacy=not feature('cluster_safety_v2_enabled'))
    active_proposals = {p['id']: p for p in proposals}
    membership = {rid: p['id'] for p in proposals for rid in p['member_record_ids']}
    permissions = {}
    saved = {tuple(sorted((p['left_id'], p['right_id']))): p for p in pairs}
    review_groups = []
    for pair in pairs:
        if (overlays.get(pair['id']) != 'ACCEPTED' and pair['decision'] == 'MERGEABLE'
                and pair.get('triage_status') == 'REVIEW' and overlays.get(pair['id']) not in ('REJECTED', 'NON_MATCH')):
            conflicts.append({'pair_id': pair['id'], 'reason': pair['triage_reason'],
                              'record_ids': sorted((pair['left_id'], pair['right_id']))})
    # Union accepted connectivity with existing automatic memberships first.
    # Validate each reviewed whole exactly once, independent of edge scores.
    adjacency = {rid: set(neighbors) for rid, neighbors in index.accepted.items()}
    for proposal in proposals:
        ids = proposal['member_record_ids']
        for rid in ids[1:]:
            adjacency.setdefault(ids[0], set()).add(rid)
            adjacency.setdefault(rid, set()).add(ids[0])
    seen = set()
    for component in index.accepted_components():
        if component[0] in seen:
            continue
        members, pending = set(), [component[0]]
        while pending:
            rid = pending.pop()
            if rid in members:
                continue
            members.add(rid)
            pending.extend(adjacency.get(rid, set()) - members)
        seen.update(members)
        internal = [p for rid in sorted(members) for p in index.incident[rid]
                    if p['left_id'] == rid and p['right_id'] in members and overlays.get(p['id']) == 'ACCEPTED']
        pair_ids = sorted(p['id'] for p in internal)
        pair = min(internal, key=lambda p: (p['left_id'], p['right_id']))
        roots = {membership[rid] for rid in members if rid in membership}
        exact = next((active_proposals[root] for root in roots
                      if active_proposals[root].get('origin') == 'GROUP_REVIEW'
                      and set(active_proposals[root]['member_record_ids']) == members), None)
        if exact:
            continue
        reason = ''
        if any(p.get('primary_group') == 'D' and not (snapshot.get('review_reasons', {}).get(p['id']) or '').strip() for p in internal):
            reason = 'SOFT_ACCEPT_REASON_REQUIRED'
        elif len(members) > policy.max_cluster_size:
            reason = 'CLUSTER_SIZE_LIMIT'
        elif any(tuple(sorted((a, b))) in vetoes | links for a, b in combinations(sorted(members), 2)):
            reason = 'CLUSTER_REVIEW_VETO'
        elif any(saved.get((a, b), {}).get('decision') in ('BLOCKED', 'NOT_MERGEABLE', 'NON_MATCH')
                 for a, b in combinations(sorted(members), 2)):
            reason = 'CLUSTER_PAIR_PERMISSION_CONFLICT'
        if not reason and feature('decision_semantics_v2_enabled'):
            # Reuse the evaluator used by require_manual_merge, including absent candidate edges.
            for a, b in combinations(sorted(members), 2):
                if (a, b) not in permissions:
                    prior = saved.get((a, b))
                    review = ReviewDecision(overlays.get(prior['id'], 'UNDECIDED')) if prior else ReviewDecision()
                    evaluated = compare_and_score_rule(by_id[a], by_id[b], set(), frequencies, links)
                    permissions[a, b] = evaluate_prepared(evaluated, by_id[a], by_id[b], review=review,
                                                          negative_pairs=links)[2].manual_allowed
                if not permissions[a, b]:
                    reason = 'CLUSTER_PAIR_PERMISSION_CONFLICT'
                    break
        if not reason and feature('cluster_safety_v2_enabled'):
            reason = check_component(members, by_id, store, policy, budget, frequencies, links, vetoes)
        elif not reason:
            violations = run_cluster_guards(frozenset({pair['left_id']}), frozenset(members - {pair['left_id']}), by_id,
                                           negative_pairs=links, pairs=[saved[a, b] for a, b in combinations(sorted(members), 2)
                                                                       if (a, b) in saved], policy=policy)
            if has_blocking_violation(violations):
                reason = next(v.reason for v in violations if v.severity == 'BLOCK')
        if reason:
            conflicts.append({'pair_id': pair['id'], 'pair_ids': pair_ids, 'reason': reason, 'record_ids': sorted(members)})
            if reason in ('CLUSTER_BRIDGE_MISSING_EVIDENCE', 'CLUSTER_SIZE_LIMIT'):
                from masterone.mdm.generation_staging import digest
                approvable = len(members) <= policy.max_reviewed_cluster_size
                fingerprint = (approval_fingerprint(index.subset(members), members) if approvable
                               else digest({'members': sorted(members), 'reason': reason}))
                field_frequencies = _field_frequencies(index, members) if approvable else {}
                review_groups.append({'member_record_ids': sorted(members)[:100], 'member_count': len(members),
                                      'reason': reason, 'fingerprint': fingerprint,
                                      'pair_ids': pair_ids[:100], 'pair_count': len(pair_ids), 'approvable': approvable,
                                      'field_frequencies': field_frequencies})
            continue
        for root in roots:
            active_proposals.pop(root)
        ids = sorted(members)
        proposal = {'id': sha256(f'{run_id}:review:{",".join(ids)}'.encode()).hexdigest(),
            'member_record_ids': ids, 'status': 'REVIEW_APPROVED', 'origin': 'REVIEW',
            'golden_fields': build_golden(ids, {rid: golden_payload(SimpleNamespace(**by_id[rid])) for rid in ids})}
        active_proposals[proposal['id']] = proposal
        for rid in ids:
            membership[rid] = proposal['id']
    proposals = sorted(active_proposals.values(), key=lambda p: p['member_record_ids'])
    # A conflict blocks its complete connected region, not disconnected valid groups.
    affected = {rid for c in conflicts for rid in c['record_ids']}
    affected_roots = {membership[rid] for rid in affected if rid in membership}
    affected.update(rid for root in affected_roots for rid in active_proposals[root]['member_record_ids'])
    safe = [p for p in proposals if not affected.intersection(p['member_record_ids'])]
    from dataclasses import asdict
    metrics = result.get('cluster_metrics', {})
    metrics.update(manual_guards=asdict(budget), total_checked=budget.checked - budget_start,
                   automatic_checked=automatic_checked, approval_checked=approval_checked,
                   manual_checked=budget.checked - budget_start - automatic_checked - approval_checked)
    return {'clusters': [] if conflicts else proposals, 'conflicts': conflicts, 'pairs': pairs,
            'safe_clusters': safe, 'conflict_record_ids': sorted(affected),
            'cluster_metrics': metrics, 'review_groups': review_groups}
