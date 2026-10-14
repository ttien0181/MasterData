"""One frozen decision universe and its actionable connectivity indexes."""
from collections import defaultdict, deque

from masterone.mdm.pipeline.matching import value_frequencies


class SnapshotIndex:
    def __new__(cls, snapshot, *, frequencies=None):
        if getattr(snapshot, 'legacy', True) is False:
            from masterone.mdm.workflow_snapshot_index import DiskSnapshotIndex
            return DiskSnapshotIndex(snapshot, frequencies=frequencies)
        return super().__new__(cls)

    def __init__(self, snapshot, *, frequencies=None):
        if getattr(snapshot, 'legacy', False):
            snapshot = snapshot.payload
        self.snapshot = snapshot
        self.by_id = {r['id']: r for r in snapshot['records']}
        self.frequencies = frequencies if frequencies is not None else value_frequencies(snapshot['records'])
        self.incident = defaultdict(list)
        self.pairs = {}
        self.adjacency = defaultdict(set)
        self.accepted = defaultdict(set)
        self.negative_incident = defaultdict(list)
        links = {tuple(sorted(p)) for p in snapshot['negative_pairs']}
        for a, b in links:
            self.negative_incident[a].append((a, b))
            self.negative_incident[b].append((a, b))
        for pair in snapshot['pairs']:
            a, b = pair['left_id'], pair['right_id']
            self.pairs[tuple(sorted((a, b)))] = pair
            self.incident[a].append(pair)
            self.incident[b].append(pair)
            decision = snapshot['overlays'].get(pair['id'], 'UNDECIDED')
            # Even a forbidden accepted edge participates in the unresolved whole.
            if decision == 'ACCEPTED':
                self.accepted[a].add(b)
                self.accepted[b].add(a)
                self.adjacency[a].add(b)
                self.adjacency[b].add(a)
                continue
            if decision in ('REJECTED', 'NON_MATCH') or tuple(sorted((a, b))) in links:
                continue
            if pair['decision'] in ('BLOCKED', 'NOT_MERGEABLE', 'NON_MATCH'):
                continue
            if decision not in ('ACCEPTED', 'DEFERRED') and pair['decision'] != 'MERGEABLE':
                continue
            self.adjacency[a].add(b)
            self.adjacency[b].add(a)

    def region(self, seeds):
        affected = set(seeds) & self.by_id.keys()
        pending = deque(affected)
        while pending:
            for neighbor in self.adjacency[pending.popleft()] - affected:
                affected.add(neighbor)
                pending.append(neighbor)
        return affected & self.by_id.keys()

    def accepted_components(self):
        seen = set()
        for seed in sorted(self.accepted):
            if seed in seen:
                continue
            members, pending = set(), [seed]
            while pending:
                rid = pending.pop()
                if rid in members:
                    continue
                members.add(rid)
                pending.extend(self.accepted[rid] - members)
            seen.update(members)
            yield sorted(members)

    def subset(self, record_ids):
        ids = set(record_ids) & self.by_id.keys()
        pairs = {p['id']: p for rid in ids for p in self.incident[rid]
                 if p['left_id'] in ids and p['right_id'] in ids}
        negatives = {p for rid in ids for p in self.negative_incident[rid] if set(p) <= ids}
        return {**self.snapshot, 'records': [self.by_id[rid] for rid in sorted(ids)],
                'pairs': sorted(pairs.values(), key=lambda p: p['id']), 'negative_pairs': sorted(negatives),
                'known_conflicts': [c for c in self.snapshot.get('known_conflicts', [])
                                    if ids.intersection(c.get('record_ids', []))],
                'group_approvals': [a for a in self.snapshot.get('group_approvals', []) if set(a['record_ids']) <= ids]}
