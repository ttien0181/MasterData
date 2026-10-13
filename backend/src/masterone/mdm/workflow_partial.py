"""Isolate unresolved candidate components without changing guarded clustering."""
from collections import defaultdict, deque

from masterone.mdm.workflow_review_rules import review_required


def candidate_region(snapshot, seeds):
    adjacency = defaultdict(set)
    links = {tuple(sorted(pair)) for pair in snapshot['negative_pairs']}
    for pair in snapshot['pairs']:
        decision = snapshot['overlays'].get(pair['id'], 'UNDECIDED')
        endpoints = (pair['left_id'], pair['right_id'])
        if decision in ('REJECTED', 'NON_MATCH') or tuple(sorted(endpoints)) in links:
            continue
        if pair['decision'] in ('BLOCKED', 'NOT_MERGEABLE', 'NON_MATCH'):
            continue
        if decision != 'ACCEPTED' and pair['decision'] not in ('MERGEABLE', 'REVIEW', 'INSUFFICIENT_EVIDENCE'):
            continue
        a, b = endpoints
        adjacency[a].add(b)
        adjacency[b].add(a)
    affected = set(seeds)
    pending = deque(affected)
    while pending:
        for neighbor in adjacency[pending.popleft()] - affected:
            affected.add(neighbor)
            pending.append(neighbor)
    return affected & {record['id'] for record in snapshot['records']}


def restricted_snapshot(snapshot, records):
    return {**snapshot,
            'records': [r for r in snapshot['records'] if r['id'] in records],
            'pairs': [p for p in snapshot['pairs'] if {p['left_id'], p['right_id']} <= records]}


def partial_snapshot(snapshot, run_id, compute):
    pending_pairs = [p for p in snapshot['pairs']
                     if review_required(snapshot['overlays'].get(p['id'], 'UNDECIDED'), p)]
    pending_records = candidate_region(snapshot, {rid for p in pending_pairs for rid in (p['left_id'], p['right_id'])})
    known = snapshot.get('known_conflicts', [])
    pair_ids = {p['id'] for p in snapshot['pairs']}
    known_records = candidate_region(snapshot, {rid for c in known for rid in c.get('record_ids', [])})
    retained = [c for c in known if set(c.get('record_ids', [])) & pending_records
                or c['pair_id'] not in pair_ids]
    reevaluated = [c['id'] for c in known if c not in retained]
    conflicts = [{k: v for k, v in c.items() if k != 'id'} for c in retained]
    # Prior conflict components receive a fresh guarded evaluation before they
    # can re-enter publication. Pending components remain completely excluded.
    validation_records = known_records - pending_records
    if validation_records:
        validation = compute(restricted_snapshot(snapshot, validation_records), run_id)
        conflicts.extend(validation['conflicts'])
    unresolved = candidate_region(snapshot, pending_records | {rid for c in conflicts for rid in c['record_ids']})
    eligible = {r['id'] for r in snapshot['records']} - unresolved
    result = compute(restricted_snapshot(snapshot, eligible), run_id)
    conflicts.extend(result['conflicts'])
    unresolved = candidate_region(snapshot, unresolved | {rid for c in conflicts for rid in c['record_ids']})
    safe = [p for p in result['safe_clusters'] if not unresolved.intersection(p['member_record_ids'])]
    result.update(clusters=[] if conflicts or unresolved else safe, safe_clusters=safe,
                  conflicts=conflicts, conflict_record_ids=sorted(unresolved),
                  unresolved_record_ids=sorted(unresolved),
                  unresolved_pair_ids=sorted({p['id'] for p in pending_pairs} | {c['pair_id'] for c in conflicts}),
                  reevaluated_conflict_ids=reevaluated,
                  retained_conflict_ids=[c['id'] for c in retained])
    # Keep post-guard evidence for eligible pairs and original effective evidence
    # for excluded pairs; metrics refer to the complete sealed record universe.
    evaluated = {p['id']: p for p in result['pairs']}
    result['pairs'] = [evaluated.get(p['id'], p) for p in snapshot['pairs']]
    return result


def publication_summary(snapshot, result):
    covered = {rid for c in result['clusters'] for rid in c['member_record_ids']}
    unresolved = {r['id'] for r in snapshot['records']} - covered
    return {'publication_status': 'PARTIAL' if unresolved or result['conflicts'] else 'COMPLETE',
            'clusters': len(result['clusters']),
            'multi_record_groups': sum(len(c['member_record_ids']) > 1 for c in result['clusters']),
            'singletons': sum(len(c['member_record_ids']) == 1 for c in result['clusters']),
            'covered_records': len(covered), 'unresolved_records': len(unresolved),
            'total_records': len(snapshot['records']), 'unresolved_record_ids': sorted(unresolved),
            'unresolved_pair_ids': result.get('unresolved_pair_ids', sorted({c['pair_id'] for c in result['conflicts']}))}
