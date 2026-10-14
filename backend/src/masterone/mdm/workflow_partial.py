"""Isolate unresolved candidate components without changing guarded clustering."""
from masterone.mdm.workflow_review_rules import review_required


def candidate_adjacency(snapshot, *, index=None):
    """Only actionable/accepted edges connect publication regions.

    A retained insufficient candidate must not hold unrelated safe records.
    Explicit deferral preserves the reviewer's affected region.
    """
    from masterone.mdm.workflow_cluster_index import SnapshotIndex
    return (index or SnapshotIndex(snapshot, frequencies={})).adjacency


def candidate_region(snapshot, seeds, *, index=None):
    from masterone.mdm.workflow_cluster_index import SnapshotIndex
    return (index or SnapshotIndex(snapshot, frequencies={})).region(seeds)


def restricted_snapshot(snapshot, records, *, index=None):
    from masterone.mdm.workflow_cluster_index import SnapshotIndex
    return (index or SnapshotIndex(snapshot, frequencies={})).subset(records)


def partial_snapshot(snapshot, run_id, compute, *, index=None):
    from masterone.mdm.workflow_cluster_index import SnapshotIndex
    index = index or SnapshotIndex(snapshot)
    pending_pairs = [p for p in snapshot['pairs']
                     if review_required(snapshot['overlays'].get(p['id'], 'UNDECIDED'), p)]
    pending_records = index.region({rid for p in pending_pairs for rid in (p['left_id'], p['right_id'])})
    known = snapshot.get('known_conflicts', [])
    pair_ids = {p['id'] for p in snapshot['pairs']}
    retained = [c for c in known if set(c.get('record_ids', [])) & pending_records
                or c['pair_id'] not in pair_ids]
    reevaluated = [c['id'] for c in known if c not in retained]
    conflicts = [{k: v for k, v in c.items() if k != 'id'} for c in retained]
    # The single fresh evaluation below includes prior conflict components.
    # Pending components remain excluded; no component is checked twice.
    unresolved = index.region(pending_records | {rid for c in conflicts for rid in c['record_ids']})
    eligible = index.by_id.keys() - unresolved
    result = compute(index.subset(eligible), run_id)
    conflicts.extend(result['conflicts'])
    unresolved = index.region(unresolved | {rid for c in conflicts for rid in c['record_ids']})
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
