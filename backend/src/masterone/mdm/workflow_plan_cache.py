"""Reuse only disconnected, previously checked regions under identical global inputs."""
from collections import defaultdict, deque
from copy import deepcopy


def reuse_regions(snapshot, previous, run_id, compute):
    if not previous:
        return None
    old, saved = previous['snapshot'], previous['computed']
    fixed = ('profile', 'policy_version', 'assessment_id', 'execution_identity', 'negative_pairs', 'known_conflicts')
    if (any(old.get(key) != snapshot.get(key) for key in fixed)
            or bool(old.get('allow_partial')) != bool(snapshot.get('allow_partial'))
            or old['records'] != snapshot['records'] or old['pairs'] != snapshot['pairs']):
        return None
    metrics = saved.get('cluster_metrics', {})
    limit = metrics.get('policy', {}).get('max_pair_checks_per_run')
    if (not limit or not metrics.get('total_checked') or metrics.get('blocked') or
            metrics.get('manual_guards', {}).get('blocked')):
        return None
    changed_pairs = {pair['id'] for pair in snapshot['pairs'] if any(
        old.get(key, {}).get(pair['id']) != snapshot.get(key, {}).get(pair['id'])
        for key in ('overlays', 'review_reasons'))}
    affected = {rid for pair in snapshot['pairs'] if pair['id'] in changed_pairs
                for rid in (pair['left_id'], pair['right_id'])}
    approvals_old = {a['operation_id']: a for a in old.get('group_approvals', [])}
    approvals_new = {a['operation_id']: a for a in snapshot.get('group_approvals', [])}
    for key in approvals_old.keys() | approvals_new.keys():
        if approvals_old.get(key) != approvals_new.get(key):
            affected.update(approvals_old.get(key, {}).get('record_ids', []))
            affected.update(approvals_new.get(key, {}).get('record_ids', []))
    # All candidate edges, including weak/negative edges, bound the reuse region.
    # Publication eligibility still uses its narrower dependency graph.
    adjacency = defaultdict(set)
    for pair in snapshot['pairs']:
        a, b = pair['left_id'], pair['right_id']
        adjacency[a].add(b)
        adjacency[b].add(a)
    for issue in [*saved['conflicts'], *snapshot.get('known_conflicts', [])]:
        ids = issue.get('record_ids', [])
        for rid in ids[1:]:
            adjacency[ids[0]].add(rid)
            adjacency[rid].add(ids[0])
    queue = deque(affected)
    while queue:
        for rid in adjacency[queue.popleft()] - affected:
            affected.add(rid)
            queue.append(rid)
    universe = {r['id'] for r in snapshot['records']}
    affected &= universe
    if affected == universe:
        return None
    if not affected:
        result = deepcopy(saved)
        result['region_cache'] = {'reused_records': len(universe), 'recomputed_records': 0}
        return result
    local = {**snapshot, 'records': [r for r in snapshot['records'] if r['id'] in affected],
             'pairs': [p for p in snapshot['pairs'] if p['left_id'] in affected and p['right_id'] in affected],
             'known_conflicts': [c for c in snapshot.get('known_conflicts', [])
                                if affected.intersection(c.get('record_ids', []))],
             'group_approvals': [a for a in snapshot.get('group_approvals', [])
                                if set(a['record_ids']) <= affected]}
    changed = compute(local, run_id)
    # Conservatively charge all previous checks plus this recomputation. A
    # previous budget failure never grants a certificate to an unchecked region.
    if metrics.get('total_checked', 0) + changed.get('cluster_metrics', {}).get('total_checked', 0) > limit:
        return None
    result = deepcopy(saved)
    result['safe_clusters'] = sorted(
        [p for p in saved['safe_clusters'] if not affected.intersection(p['member_record_ids'])]
        + changed['safe_clusters'], key=lambda p: p['member_record_ids'])
    result['conflicts'] = [c for c in saved['conflicts'] if not affected.intersection(c['record_ids'])] + changed['conflicts']
    result['review_groups'] = [g for g in saved.get('review_groups', [])
                               if not affected.intersection(g['member_record_ids'])] + changed.get('review_groups', [])
    from masterone.mdm.pipeline.decision_store import edge_key
    order = {p['id']: edge_key(p) for p in snapshot['pairs']}
    known = {c['pair_id']: index for index, c in enumerate(snapshot.get('known_conflicts', []))}
    result['conflicts'].sort(key=lambda c: (0, known[c['pair_id']]) if c['pair_id'] in known
                             else (1, order.get(c['pair_id'], (True, 0, '', ''))))
    unresolved = (set(saved['conflict_record_ids']) - affected) | set(changed['conflict_record_ids'])
    result['conflict_record_ids'] = sorted(unresolved)
    combined = {p['id']: p for p in saved['pairs'] if p['left_id'] not in affected}
    combined.update({p['id']: p for p in changed['pairs']})
    result['pairs'] = [combined[p['id']] for p in snapshot['pairs'] if p['id'] in combined]
    if snapshot.get('allow_partial'):
        result['unresolved_record_ids'] = sorted(unresolved)
        local_ids = {p['id'] for p in local['pairs']}
        result['unresolved_pair_ids'] = sorted((set(saved.get('unresolved_pair_ids', [])) - local_ids)
                                               | set(changed.get('unresolved_pair_ids', [])))
        for key in ('reevaluated_conflict_ids', 'retained_conflict_ids'):
            local_conflicts = {c['id'] for c in local['known_conflicts']}
            result[key] = sorted((set(saved.get(key, [])) - local_conflicts) | set(changed.get(key, [])))
    result['region_cache'] = {'reused_records': len(universe - affected), 'recomputed_records': len(affected)}
    result['cluster_metrics']['total_checked'] = metrics.get('total_checked', 0) + changed.get('cluster_metrics', {}).get('total_checked', 0)
    return result
