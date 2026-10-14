"""Bounded component evaluation over a context-owned frozen disk snapshot."""
import zlib
from hashlib import sha256

from masterone.mdm.generation_staging import digest
from masterone.mdm.workflow_snapshot_index import unpack
from masterone.mdm.workflow_snapshot_storage import canonical_payload


class StreamedPlan:
    def __init__(self, view, run_id, revision):
        from masterone.mdm.pipeline.cluster_policy import GuardBudget
        from masterone.mdm.workflow_cluster_index import SnapshotIndex
        from masterone.mdm.workflow_review_rules import review_required
        self.view, self.run_id, self.revision = view, run_id, revision
        self.index = SnapshotIndex(view)
        self.connection = self.index.connection
        self.budget = GuardBudget()
        self.connection.executescript('''
            CREATE TABLE plan_groups(id TEXT PRIMARY KEY,payload BLOB);
            CREATE TABLE plan_singleton_groups(id TEXT PRIMARY KEY);
            CREATE TABLE plan_members(id TEXT PRIMARY KEY,group_id TEXT);
            CREATE INDEX plan_member_group ON plan_members(group_id,id);
            CREATE TABLE plan_excluded(id TEXT PRIMARY KEY);
            CREATE TABLE plan_pending(id TEXT PRIMARY KEY);
            CREATE TABLE plan_pending_pairs(id TEXT PRIMARY KEY,a TEXT,b TEXT);
            CREATE INDEX plan_pending_left ON plan_pending_pairs(a);
            CREATE INDEX plan_pending_right ON plan_pending_pairs(b);
            CREATE TABLE plan_issues(id TEXT PRIMARY KEY,payload BLOB);
            CREATE TABLE plan_reviews(id TEXT PRIMARY KEY,payload BLOB);
            CREATE TABLE plan_conflict_state(id TEXT PRIMARY KEY,retained INTEGER);
        ''')
        for pair in view.pairs:
            if review_required(view.overlays.get(pair['id'], 'UNDECIDED'), pair):
                self.connection.executemany('INSERT OR IGNORE INTO plan_pending VALUES (?)',
                    [(pair['left_id'],), (pair['right_id'],)])
                self.connection.execute('INSERT INTO plan_pending_pairs VALUES (?,?,?)',
                    (pair['id'], pair['left_id'], pair['right_id']))
        pending_region = self.index.region(rid for (rid,) in self.connection.execute('SELECT id FROM plan_pending'))
        self.connection.execute('INSERT INTO plan_excluded SELECT id FROM regions WHERE token=?', (pending_region.token,))
        for conflict in view.iter_rows('known_conflicts'):
            missing = not self.connection.execute('SELECT 1 FROM pairs WHERE id=?', (conflict['pair_id'],)).fetchone()
            intersects = any(self.connection.execute('SELECT 1 FROM plan_excluded WHERE id=?', (rid,)).fetchone()
                             for rid in conflict.get('record_ids', []))
            retained = missing or intersects
            self.connection.execute('INSERT INTO plan_conflict_state VALUES (?,?)', (conflict['id'], int(retained)))
            if retained:
                held = self.index.region(conflict.get('record_ids', []))
                self.connection.execute('INSERT OR IGNORE INTO plan_excluded SELECT id FROM regions WHERE token=?', (held.token,))
                self._put('plan_issues', 'retained:' + conflict['id'], {k: v for k, v in conflict.items() if k != 'id'})
        seen = 'plan-seen'
        from masterone.mdm.workflow_cluster_engine import compute_components
        for seed in self.index.seeds('actionable'):
            if self.connection.execute('SELECT 1 FROM regions WHERE token=? AND id=?', (seen, seed)).fetchone():
                continue
            region = self.index.region([seed])
            self.connection.execute('INSERT OR IGNORE INTO regions SELECT ?,id FROM regions WHERE token=?', (seen, region.token))
            pending = self.connection.execute('SELECT 1 FROM regions r JOIN plan_excluded p ON r.id=p.id WHERE r.token=? LIMIT 1',
                                              (region.token,)).fetchone()
            if pending or len(region) > 100:
                self.connection.execute('INSERT OR IGNORE INTO plan_excluded SELECT id FROM regions WHERE token=?', (region.token,))
                if not pending:
                    summary = region.summary(100)
                    pair_id = self.connection.execute('SELECT p.id FROM pairs p JOIN regions r ON r.id=p.a WHERE r.token=? ORDER BY p.id LIMIT 1',
                                                       (region.token,)).fetchone()[0]
                    self._put('plan_issues', seed, {'pair_id': pair_id, 'reason': 'CLUSTER_SIZE_LIMIT',
                        'record_ids': summary['record_ids'], 'record_count': summary['record_count'],
                        'excluded_region': seed, 'approvable': False})
                continue
            local = self.index.subset(region)
            result = compute_components(local, run_id, frequencies=self.index.frequencies, guard_budget=self.budget)
            excluded = set(result['conflict_record_ids'])
            # A failed union holds its complete actionable region for review.
            if excluded:
                self.connection.execute('INSERT OR IGNORE INTO plan_excluded SELECT id FROM regions WHERE token=?', (region.token,))
            for i, issue in enumerate(result['conflicts']):
                self._put('plan_issues', seed + ':' + str(i), issue)
            for review in result.get('review_groups', []):
                self._put('plan_reviews', review['fingerprint'], {**review, 'records': local['records'],
                    'profile': local['profile'], 'pairs': local['pairs']})
            if excluded:
                continue
            for group in result['safe_clusters']:
                group = {**group, 'id': sha256(f'{run_id}:{revision}:{group["id"]}'.encode()).hexdigest(), 'singleton': False}
                members = group['member_record_ids']
                records = [self.index.by_id[rid] for rid in sorted(members)]
                group['source_snapshot_hash'] = digest(records)
                group['member_count'] = len(members)
                group['display_name'] = next((r['raw_fields'].get('name') for r in records if r['raw_fields'].get('name')), members[0])
                group['preview_members'] = [{'record_id': r['id'], 'external_id': r.get('external_id'),
                    'source_system': r.get('source_system'), 'name': r['raw_fields'].get('name'),
                    'fields': {field: r['raw_fields'].get(field) for field in ('name', 'phone', 'email', 'address', 'dob', 'government_id')}} for r in records[:25]]
                self._put('plan_groups', group['id'], group)
                self.connection.executemany('INSERT INTO plan_members VALUES (?,?)', ((rid, group['id']) for rid in members))
            self.index._check_disk()
        self.connection.commit()

    def _put(self, table, key, value):
        self.connection.execute(f'INSERT INTO {table} VALUES (?,?)', (key, zlib.compress(canonical_payload(value).encode(), 1)))
        if table == 'plan_groups' and value.get('singleton'):
            self.connection.execute('INSERT INTO plan_singleton_groups VALUES (?)', (key,))

    def iter_groups(self):
        for (payload,) in self.connection.execute('SELECT payload FROM plan_groups ORDER BY id'):
            yield unpack(payload)

    def iter_reviews(self):
        for key, payload in self.connection.execute('SELECT id,payload FROM plan_reviews ORDER BY id'):
            yield {'id': key, **unpack(payload)}

    def iter_eligible_records(self):
        for (payload,) in self.connection.execute('SELECT r.payload FROM records r WHERE NOT EXISTS '
            '(SELECT 1 FROM plan_excluded e WHERE e.id=r.id) ORDER BY r.id'):
            yield unpack(payload)

    def iter_excluded(self):
        for (rid,) in self.connection.execute('SELECT id FROM plan_excluded ORDER BY id'):
            yield {'id': rid}

    def issues(self, limit=25):
        return [unpack(payload) for (payload,) in self.connection.execute('SELECT payload FROM plan_issues ORDER BY id LIMIT ?', (limit,))]

    def counts(self):
        total = len(self.index.by_id)
        excluded = self.connection.execute('SELECT COUNT(*) FROM plan_excluded').fetchone()[0]
        members = self.connection.execute('SELECT COUNT(*) FROM plan_members m WHERE NOT EXISTS '
            '(SELECT 1 FROM plan_singleton_groups s WHERE s.id=m.group_id)').fetchone()[0]
        multi = self.connection.execute('SELECT COUNT(*) FROM plan_groups g WHERE NOT EXISTS '
            '(SELECT 1 FROM plan_singleton_groups s WHERE s.id=g.id)').fetchone()[0]
        singles = total - excluded - members
        return {'total_records': total, 'covered_records': total-excluded, 'unresolved_records': excluded,
                'clusters': multi+singles, 'multi_record_groups': multi, 'singletons': singles,
                'publication_status': 'PARTIAL' if excluded else 'COMPLETE'}

    def pending_summaries(self):
        from heapq import heappush, heapreplace
        seen, summaries = 'pending-summary-seen', []
        for seed, in self.connection.execute('SELECT id FROM plan_pending ORDER BY id'):
            if self.connection.execute('SELECT 1 FROM regions WHERE token=? AND id=?', (seen, seed)).fetchone():
                continue
            region = self.index.region([seed])
            self.connection.execute('INSERT OR IGNORE INTO regions SELECT ?,id FROM regions WHERE token=?', (seen, region.token))
            query = ('SELECT DISTINCT p.id FROM plan_pending_pairs p WHERE EXISTS '
                     '(SELECT 1 FROM regions r WHERE r.token=? AND (r.id=p.a OR r.id=p.b))')
            count = self.connection.execute('SELECT COUNT(*) FROM (' + query + ')', (region.token,)).fetchone()[0]
            pairs = [pid for pid, in self.connection.execute(query + ' ORDER BY p.id LIMIT 25', (region.token,))]
            summary = {**region.summary(100), 'pending_pair_ids': pairs, 'pending_pair_count': count}
            summary.pop('approvable')
            item = len(region), seed, summary
            if len(summaries) < 25:
                heappush(summaries, item)
            elif item[:2] > summaries[0][:2]:
                heapreplace(summaries, item)
        self.index._check_disk()
        return sorted((summary for _, _, summary in summaries), key=lambda r: (-r['record_count'], r['record_ids']))
