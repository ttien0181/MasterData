"""Global identity overlap planning on the plan's owned scratch database."""
import zlib
from datetime import UTC
from uuid import uuid5

from sqlalchemy import delete, select

from masterone.mdm.models import ResolutionEntity, ResolutionEntityMember, ResolutionEntityVersion
from masterone.mdm.workflow_entities import ENTITY_NAMESPACE, singleton_entity_id
from masterone.mdm.workflow_models import WorkflowArtifact, WorkflowCluster, WorkflowGeneration
from masterone.mdm.workflow_snapshot_index import unpack
from masterone.mdm.workflow_snapshot_storage import canonical_payload


def age(value):
    return (value.replace(tzinfo=UTC) if value.tzinfo is None else value).timestamp()


class StreamedEntities:
    def __init__(self, db, run, plan):
        self.db, self.run, self.plan = db, run, plan
        self.sql = plan.connection
        self.sql.executescript('''
            CREATE TABLE identity_parents(id TEXT PRIMARY KEY,created REAL,anchor TEXT);
            CREATE TABLE identity_previous(id TEXT PRIMARY KEY,payload BLOB);
            CREATE TABLE identity_members(id TEXT PRIMARY KEY,cluster_id TEXT,entity_id TEXT);
            CREATE INDEX identity_member_entity ON identity_members(entity_id);
            CREATE TABLE identity_implicit(id TEXT PRIMARY KEY);
            CREATE TABLE identity_overlaps(entity_id TEXT,group_id TEXT,n INTEGER,anchor INTEGER,smallest TEXT,
                                           PRIMARY KEY(entity_id,group_id));
            CREATE TABLE identity_assigned(group_id TEXT PRIMARY KEY,entity_id TEXT UNIQUE);
        ''')
        self._read_parents()

    def _eligible(self, rid):
        return self.sql.execute('SELECT 1 FROM records r WHERE r.id=? AND NOT EXISTS '
                                '(SELECT 1 FROM plan_excluded e WHERE e.id=r.id)', (rid,)).fetchone() is not None

    def _read_parents(self):
        # Only one <=100-member prior group is held at a time. Latest member
        # ownership spans partial gaps, just as in the legacy global planner.
        query = self.db.query(WorkflowCluster, WorkflowArtifact.created_at).join(
            WorkflowGeneration, WorkflowCluster.generation_id == WorkflowGeneration.id).join(
            WorkflowArtifact, WorkflowGeneration.artifact_id == WorkflowArtifact.id).filter(
                WorkflowGeneration.run_id == self.run.id, WorkflowGeneration.revision <= self.run.cluster_revision
            ).order_by(WorkflowGeneration.revision.desc(), WorkflowCluster.id).yield_per(512)
        for cluster, published_at in query:
            members = cluster.payload['member_record_ids']
            relevant = [rid for rid in members if self._eligible(rid) and not self.sql.execute(
                'SELECT 1 FROM identity_members WHERE id=?', (rid,)).fetchone()]
            if not relevant:
                continue
            eid = cluster.payload.get('entity_id') or (singleton_entity_id(self.run.id, members[0]) if len(members) == 1
                else str(uuid5(ENTITY_NAMESPACE, f'{self.run.id}:legacy:{cluster.id}')))
            entity = self.db.get(ResolutionEntity, eid)
            self.sql.execute('INSERT OR IGNORE INTO identity_parents VALUES (?,?,?)',
                (eid, age(entity.created_at if entity else published_at), cluster.payload.get('anchor_record_id') or min(members)))
            stored = {'id': cluster.id, 'generation_id': cluster.generation_id, 'payload': cluster.payload,
                      'golden': cluster.golden, 'selections': cluster.selections, 'confirmed': cluster.confirmed,
                      'revision': cluster.revision}
            self.sql.execute('INSERT OR IGNORE INTO identity_previous VALUES (?,?)',
                             (cluster.id, zlib.compress(canonical_payload(stored).encode(), 1)))
            self.sql.executemany('INSERT INTO identity_members VALUES (?,?,?)', ((rid, cluster.id, eid) for rid in relevant))
        anchor = select(ResolutionEntityVersion.golden_fields['_workflow_identity']['anchor_record_id'].as_string()).where(
            ResolutionEntityVersion.entity_id == ResolutionEntity.id).order_by(ResolutionEntityVersion.version.desc()).limit(1).scalar_subquery()
        query = self.db.query(ResolutionEntityMember.record_id, ResolutionEntity.id, ResolutionEntity.created_at, anchor).join(
            ResolutionEntity, ResolutionEntityMember.entity_id == ResolutionEntity.id).filter(
                ResolutionEntity.run_id == self.run.id, ResolutionEntity.status == 'ACTIVE').yield_per(512)
        for rid, eid, created, anchor_id in query:
            if not self._eligible(rid):
                continue
            self.sql.execute('INSERT OR IGNORE INTO identity_parents VALUES (?,?,?)', (eid, age(created), anchor_id or rid))
            self.sql.execute('INSERT INTO identity_members VALUES (?,NULL,?) ON CONFLICT(id) '
                             'DO UPDATE SET entity_id=excluded.entity_id', (rid, eid))
        self._read_implicit_parents()
        self.sql.commit()
        self.plan.index._check_disk()

    def _read_implicit_parents(self):
        from itertools import islice

        from masterone.mdm.workflow_generation_reads import GenerationReader
        from masterone.mdm.workflow_models import WorkflowMember
        generations = self.db.query(WorkflowGeneration, WorkflowArtifact.created_at).join(
            WorkflowArtifact, WorkflowGeneration.artifact_id == WorkflowArtifact.id).filter(
                WorkflowGeneration.run_id == self.run.id, WorkflowGeneration.revision <= self.run.cluster_revision,
                WorkflowGeneration.storage_version.in_(('workflow_virtual_v2', 'workflow_parquet_v2'))).order_by(
                    WorkflowGeneration.revision.desc()).yield_per(32)
        for generation, created in generations:
            reader = GenerationReader(self.db, self.run, generation)
            iterator = iter(reader.records())
            while batch := list(islice(iterator, 512)):
                occupied = {rid for rid, in self.db.query(WorkflowMember.record_id).filter(
                    WorkflowMember.generation_id == generation.id, WorkflowMember.record_id.in_([r['id'] for r in batch]))}
                for record in batch:
                    rid = record['id']
                    if rid in occupied or not self._eligible(rid) or self.sql.execute(
                            'SELECT 1 FROM identity_members WHERE id=?', (rid,)).fetchone():
                        continue
                    eid = singleton_entity_id(self.run.id, rid)
                    self.sql.execute('INSERT INTO identity_parents VALUES (?,?,?)', (eid, age(created), rid))
                    self.sql.execute('INSERT INTO identity_members VALUES (?,NULL,?)', (rid, eid))
                    self.sql.execute('INSERT INTO identity_implicit VALUES (?)', (rid,))
                self.sql.commit()
                self.plan.index._check_disk()

    def prepare(self, generation):
        self.sql.execute('''INSERT INTO identity_overlaps
            SELECT m.entity_id,g.group_id,COUNT(*),MAX(m.id=p.anchor),
                   (SELECT MIN(child.id) FROM plan_members child WHERE child.group_id=g.group_id)
            FROM identity_members m JOIN plan_members g ON m.id=g.id JOIN identity_parents p ON p.id=m.entity_id
            GROUP BY m.entity_id,g.group_id''')
        for eid, in self.sql.execute('SELECT p.id FROM identity_parents p WHERE EXISTS '
            '(SELECT 1 FROM identity_overlaps o WHERE o.entity_id=p.id) ORDER BY p.created,p.id'):
            child = self.sql.execute('SELECT group_id FROM identity_overlaps WHERE entity_id=? '
                'ORDER BY anchor DESC,n DESC,smallest,group_id LIMIT 1', (eid,)).fetchone()[0]
            self.sql.execute('INSERT OR IGNORE INTO identity_assigned VALUES (?,?)', (child, eid))
        for group in self.plan.iter_groups():
            members, gid = sorted(group['member_record_ids']), group['id']
            assigned = self.sql.execute('SELECT entity_id FROM identity_assigned WHERE group_id=?', (gid,)).fetchone()
            eid = assigned[0] if assigned else (singleton_entity_id(self.run.id, members[0]) if len(members) == 1 else
                                               str(uuid5(ENTITY_NAMESPACE, f'{self.run.id}:{generation.revision}:{gid}')))
            if group.get('entity_id') and group['entity_id'] != eid:
                raise ValueError('WORKFLOW_ENTITY_IDENTITY_CHANGED')
            if not assigned:
                self.sql.execute('INSERT INTO identity_assigned VALUES (?,?)', (gid, eid))
            parent = self.sql.execute('SELECT anchor FROM identity_parents WHERE id=?', (eid,)).fetchone()
            anchor_id = parent[0] if parent and parent[0] in members else members[0]
            ancestors = [e for e, in self.sql.execute('SELECT entity_id FROM identity_overlaps WHERE group_id=? ORDER BY entity_id', (gid,))]
            retained = [cid for cid, in self.sql.execute('SELECT DISTINCT m.cluster_id FROM identity_members m '
                'JOIN plan_members g ON m.id=g.id WHERE g.group_id=? AND m.entity_id=? AND m.cluster_id IS NOT NULL '
                'ORDER BY m.cluster_id', (gid, eid))]
            planned = {**group, 'entity_id': eid, 'anchor_record_id': anchor_id, 'ancestor_entity_ids': ancestors,
                       'retained_ancestor_cluster_ids': retained, 'identity_version': 1}
            self.sql.execute('UPDATE plan_groups SET payload=? WHERE id=?',
                             (zlib.compress(canonical_payload(planned).encode(), 1), gid))
        self.sql.commit()
        self.plan.index._check_disk()

    def prepare_singletons(self, generation, repair_metadata=None):
        """Keep lineage/choice exceptions before the global overlap assignment."""
        from masterone.mdm.engine_adapter import engine_context
        from masterone.mdm.generation_staging import digest
        from masterone.mdm.pipeline.golden import build_golden
        from masterone.mdm.workflow_generation_reads import virtual_cluster_id
        repairing = 'repair_record_ids' in (repair_metadata or {})
        automatic = set((repair_metadata or {}).get('auto_confirm_record_ids', []))
        self.sql.execute('CREATE TABLE identity_singleton_candidates(id TEXT PRIMARY KEY)')
        self.sql.execute('INSERT INTO identity_singleton_candidates SELECT m.id FROM identity_members m '
            'WHERE NOT EXISTS (SELECT 1 FROM plan_members g WHERE g.id=m.id)')
        self.sql.executemany('INSERT OR IGNORE INTO identity_singleton_candidates VALUES (?)',
                             ((rid,) for rid in automatic if self._eligible(rid)))
        with engine_context(self.plan.view['profile']):
            for rid, in self.sql.execute('SELECT s.id FROM identity_singleton_candidates s WHERE NOT EXISTS '
                '(SELECT 1 FROM plan_members g WHERE g.id=s.id) ORDER BY s.id'):
                if rid not in automatic and self.sql.execute('SELECT 1 FROM identity_implicit WHERE id=?', (rid,)).fetchone():
                    continue
                previous = self.previous_groups([rid])
                parent = previous[0] if previous else None
                if (parent is not None and not repairing and len(parent.payload['member_record_ids']) == 1
                        and not parent.selections and not parent.payload.get('pending_selections')
                        and not parent.payload.get('requires_manual_review')
                        and parent.payload.get('entity_id') in (None, singleton_entity_id(self.run.id, rid))):
                    continue
                record = self.plan.index.by_id[rid]
                gid = virtual_cluster_id(generation.id, rid)
                value = {'id': gid, 'member_record_ids': [rid], 'singleton': True, 'status': 'SINGLETON',
                    'golden_fields': build_golden([rid], {rid: record}), 'source_snapshot_hash': digest([record]),
                    'display_name': record.get('raw_fields', {}).get('name') or rid, 'member_count': 1,
                    'preview_members': [{'record_id': rid, 'external_id': record.get('external_id'),
                        'source_system': record.get('source_system'), 'name': record.get('raw_fields', {}).get('name'),
                        'fields': record.get('raw_fields', {})}]}
                self.plan._put('plan_groups', gid, value)
                self.sql.execute('INSERT INTO plan_members VALUES (?,?)', (rid, gid))
        self.sql.commit()
        self.plan.index._check_disk()

    def previous_groups(self, members):
        from types import SimpleNamespace
        ids = set()
        for rid in members:
            found = self.sql.execute('SELECT cluster_id FROM identity_members WHERE id=?', (rid,)).fetchone()
            if found and found[0]:
                ids.add(found[0])
        return [SimpleNamespace(**unpack(self.sql.execute('SELECT payload FROM identity_previous WHERE id=?', (cid,)).fetchone()[0]))
                for cid in sorted(ids)]

    def persist(self, generation, *, actor, record_versions=True):
        """Write the global assignment in the publication's one transaction."""
        from itertools import islice

        from masterone.mdm.workflow_entities import _version
        self.sql.execute('CREATE TABLE identity_held(id TEXT PRIMARY KEY)')
        query = self.db.query(ResolutionEntityMember.entity_id, ResolutionEntityMember.record_id).join(
            ResolutionEntity).filter(ResolutionEntity.run_id == self.run.id).yield_per(512)
        for eid, rid in query:
            if not self.sql.execute('SELECT 1 FROM plan_members WHERE id=?', (rid,)).fetchone():
                self.sql.execute('INSERT OR IGNORE INTO identity_held VALUES (?)', (eid,))
        for eid, children in self.sql.execute('SELECT o.entity_id,COUNT(DISTINCT o.group_id) FROM identity_overlaps o '
            'WHERE NOT EXISTS (SELECT 1 FROM identity_assigned a WHERE a.entity_id=o.entity_id) '
            'AND NOT EXISTS (SELECT 1 FROM identity_held h WHERE h.id=o.entity_id) GROUP BY o.entity_id'):
            entity = self.db.get(ResolutionEntity, eid)
            if entity is not None:
                entity.status = 'MERGED' if children == 1 else 'SPLIT'
        # Preflight all targets before removing source memberships.
        targets = iter(eid for eid, in self.sql.execute('SELECT entity_id FROM identity_assigned ORDER BY entity_id'))
        while batch := list(islice(targets, 500)):
            if self.db.query(ResolutionEntity.id).filter(ResolutionEntity.id.in_(batch), ResolutionEntity.run_id != self.run.id).first():
                raise ValueError('WORKFLOW_ENTITY_IDENTITY_INVALID')
        owners = select(ResolutionEntity.id).where(ResolutionEntity.run_id == self.run.id)
        members = iter(rid for rid, in self.sql.execute('SELECT id FROM plan_members ORDER BY id'))
        while batch := list(islice(members, 500)):
            self.db.execute(delete(ResolutionEntityMember).where(ResolutionEntityMember.entity_id.in_(owners),
                ResolutionEntityMember.record_id.in_(batch)).execution_options(synchronize_session=False))
        pending = []
        for group in self.plan.iter_groups():
            entity = self.db.get(ResolutionEntity, group['entity_id'])
            if entity is None:
                entity = ResolutionEntity(id=group['entity_id'], run_id=self.run.id, version=1,
                                          status='ACTIVE', entity_type='UNKNOWN')
                self.db.add(entity)
            else:
                entity.version += 1
                entity.status = 'ACTIVE'
            pending.append(entity)
            if len(pending) >= 500:
                self.db.flush()
                pending.clear()
        self.db.flush()
        pending.clear()
        n_members, n_versions = 0, 0
        for group in self.plan.iter_groups():
            entity = self.db.get(ResolutionEntity, group['entity_id'])
            for rid in group['member_record_ids']:
                self.db.add(ResolutionEntityMember(entity_id=entity.id, record_id=rid, added_by=actor))
                n_members += 1
            if record_versions:
                _version(self.db, entity, generation, group, actor)
            n_versions += 1
            yield group
            if n_members >= 2000 or n_versions >= 500:
                self.db.flush()
                n_members, n_versions = 0, 0
        self.db.flush()
