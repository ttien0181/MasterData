"""One owned generation reader for materialized and virtual groups."""
from heapq import merge
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import func

from masterone.mdm.generation_staging import digest
from masterone.mdm.workflow_artifacts import iter_list, read_value
from masterone.mdm.workflow_models import WorkflowCluster, WorkflowGeneration, WorkflowMember


def virtual_cluster_id(generation_id, record_id):
    return f's:{generation_id}:{record_id}'


class GenerationReader:
    def __init__(self, db, run, generation):
        if generation.run_id != run.id:
            raise HTTPException(404, 'Generation not found')
        self.db, self.run, self.generation = db, run, generation
        self.virtual = generation.storage_version in ('workflow_virtual_v2', 'workflow_parquet_v2')
        self._profile = None
        self._policy = None
        self._source_artifact = None
    def _ensure_source(self):
        if self.virtual and self._source_artifact is None:
            from masterone.mdm.workflow_models import WorkflowArtifact
            self._source_artifact = self.db.get(WorkflowArtifact, self.generation.coverage['source_artifact_id'])
            row = self._source_artifact
            if (row is None or row.run_id != self.run.id or row.phase != 'SINGLETON_SOURCE'
                    or digest(row.payload) != row.content_hash
                    or row.payload.get('__workflow_logical_hash__', row.content_hash) != self.generation.coverage['source_hash']):
                raise ValueError('WORKFLOW_ARTIFACT_INVALID')

    def records(self, after=None):
        if not self.virtual:
            from masterone.mdm.models import ResolutionRecord
            from masterone.mdm.pipeline.golden_v2 import record_payload
            for row in self.db.query(ResolutionRecord).filter_by(run_id=self.run.id).order_by(ResolutionRecord.id).yield_per(512):
                yield record_payload(row)
            return
        if self.generation.storage_version == 'workflow_parquet_v2':
            from masterone.mdm.workflow_snapshot_storage import open_snapshot
            with open_snapshot(self.db, self.generation.coverage['source_artifact_id'], self.run.id, 'SINGLETON_SOURCE') as view:
                self._ensure_source()
                yield from (view.iter_rows_after('records', after) if after is not None else view.iter_rows('records'))
            return
        self._ensure_source()
        for record in iter_list(self.db, self.generation.coverage['source_artifact_id'], self.run.id,
                                'SINGLETON_SOURCE', ['records']):
            if after is None or record['id'] > after:
                yield record

    def record(self, record_id):
        if self.generation.storage_version == 'workflow_parquet_v2':
            from masterone.mdm.workflow_snapshot_storage import open_snapshot
            with open_snapshot(self.db, self.generation.coverage['source_artifact_id'], self.run.id, 'SINGLETON_SOURCE') as view:
                try:
                    return view.records[record_id]
                except KeyError:
                    raise HTTPException(404, 'Record not in generation') from None
        for record in self.records():
            if record['id'] == record_id:
                return record
        raise HTTPException(404, 'Record not in generation')

    def record_row(self, record_id):
        record = self.record(record_id)
        return SimpleNamespace(**{**record, 'run_id': self.run.id,
                                  'extra_fields': record.get('extra_fields', {})})

    def _default_confirmed(self):
        receipt = self.generation.singleton_confirmation or {}
        return bool(receipt.get('default_confirmed') and receipt.get('source_hash') == self.generation.coverage.get('source_hash')
                    and receipt.get('generation_id') == self.generation.id)

    def _virtual_row(self, record):
        from masterone.mdm.engine_adapter import engine_context
        from masterone.mdm.pipeline.golden import build_golden
        from masterone.mdm.workflow_entities import singleton_entity_id
        rid = record['id']
        if self._profile is None:
            self._ensure_source()
            from masterone.mdm.pipeline.cluster_policy import ClusterPolicy
            if self.generation.storage_version == 'workflow_parquet_v2':
                metadata = self._source_artifact.payload['metadata']
                self._profile, pinned = metadata['profile'], metadata['survivorship_policy']
            else:
                self._profile = read_value(self.db, self.generation.coverage['source_artifact_id'], self.run.id,
                                          'SINGLETON_SOURCE', ['profile'])
                pinned = read_value(self.db, self.generation.coverage['source_artifact_id'], self.run.id,
                                    'SINGLETON_SOURCE', ['survivorship_policy'])
            self._policy = ClusterPolicy(**pinned)
        with engine_context(self._profile):
            golden = build_golden([rid], {rid: record}, policy=self._policy)
        cid = virtual_cluster_id(self.generation.id, rid)
        payload = {'id': cid, 'member_record_ids': [rid], 'singleton': True, 'status': 'SINGLETON',
                   'entity_id': singleton_entity_id(self.run.id, rid), 'identity_version': 1,
                   'anchor_record_id': rid, 'ancestor_entity_ids': [], 'golden_fields': golden,
                   'display_name': record.get('raw_fields', {}).get('name') or record.get('external_id') or rid,
                   'source_snapshot_hash': digest([record]), 'local_evidence_version': 1, 'pair_evidence': [],
                   'workflow_context': {}, 'llm_stats': {}, 'requires_manual_review': [], 'pending_selections': {}}
        return WorkflowCluster(id=cid, generation_id=self.generation.id, payload=payload, golden=golden,
                               selections={}, revision=0, confirmed=self._default_confirmed())

    def row(self, cluster_id):
        row = self.db.query(WorkflowCluster).filter_by(id=cluster_id, generation_id=self.generation.id).one_or_none()
        if row is not None:
            return row
        prefix = f's:{self.generation.id}:'
        if not self.virtual or not cluster_id.startswith(prefix):
            raise HTTPException(404, 'Cluster not found')
        rid = cluster_id[len(prefix):]
        if self.db.query(WorkflowMember.id).filter_by(generation_id=self.generation.id, record_id=rid).first():
            raise HTTPException(404, 'Record is in a materialized group')
        return self._virtual_row(self.record(rid))

    def group(self, cluster_id):
        return self.row(cluster_id).payload

    def persisted_rows(self):
        return self.db.query(WorkflowCluster).filter_by(generation_id=self.generation.id).order_by(WorkflowCluster.id).yield_per(512)

    def rows(self, scope=None):
        scope = scope or {}
        cursor = scope.get('after')
        ids, excluded = scope.get('cluster_ids'), set(scope.get('excluded_cluster_ids', []))
        if ids is not None:
            for cid in sorted(set(ids) - excluded):
                yield self.row(cid)
            return
        if not self.virtual:
            yield from (row for row in self.persisted_rows() if row.id not in excluded)
            return
        # Only explicit memberships occupy DB rows. Default singletons need no
        # per-record membership write or golden hydration for aggregate counts.
        def virtual_rows():
            from itertools import islice
            prefix = f's:{self.generation.id}:'
            after = cursor[len(prefix):] if cursor is not None and cursor.startswith(prefix) else None
            iterator = iter(self.records(after=after))
            while batch := list(islice(iterator, 512)):
                members = {rid for rid, in self.db.query(WorkflowMember.record_id).filter(
                    WorkflowMember.generation_id == self.generation.id,
                    WorkflowMember.record_id.in_([r['id'] for r in batch]))}
                for record in batch:
                    cid = virtual_cluster_id(self.generation.id, record['id'])
                    if record['id'] not in members and cid not in excluded and (cursor is None or cid > cursor):
                        yield self._virtual_row(record)
        persisted = (row for row in self.persisted_rows() if row.id not in excluded and (cursor is None or row.id > cursor))
        yield from merge(persisted, virtual_rows(), key=lambda row: row.id)

    def iter_groups(self, scope=None):
        yield from (row.payload for row in self.rows(scope))

    def group_page(self, limit, cursor=None, mapper=None):
        if not 1 <= limit <= 100:
            raise HTTPException(422, 'Invalid page size')
        items, last_id = [], None
        for row in self.rows({'after': cursor}):
            if cursor is not None and row.id <= cursor:
                continue
            if len(items) == limit:
                return {'items': items, 'total': self.coverage()['clusters'], 'next_cursor': last_id}
            items.append(mapper(row) if mapper else row.payload)
            last_id = row.id
        return {'items': items, 'total': self.coverage()['clusters'], 'next_cursor': None}

    def coverage(self):
        if self.virtual:
            return {key: value for key, value in self.generation.coverage.items() if key in
                    ('clusters', 'singletons', 'multi_record_groups', 'covered_records', 'unresolved_records', 'total_records')}
        clusters, singles = self.db.query(func.count(WorkflowCluster.id),
            func.count(WorkflowCluster.id).filter(WorkflowCluster.payload['singleton'].as_boolean().is_(True))).filter_by(
                generation_id=self.generation.id).one()
        covered = self.db.query(func.count(WorkflowMember.id)).filter_by(generation_id=self.generation.id).scalar()
        return dict(clusters=clusters, singletons=singles, multi_record_groups=clusters-singles,
                    covered_records=covered, total_records=self.run.record_count, unresolved_records=self.run.record_count-covered)

    def unconfirmed_count(self):
        query = self.db.query(WorkflowCluster).filter_by(generation_id=self.generation.id)
        missing = query.filter_by(confirmed=False).count()
        if self.virtual and not self._default_confirmed():
            exceptions = query.filter(WorkflowCluster.payload['singleton'].as_boolean().is_(True)).count()
            missing += self.coverage()['singletons'] - exceptions
        return missing

    def scope_count(self, scope):
        ids, excluded = scope.get('cluster_ids'), set(scope.get('excluded_cluster_ids', []))
        if ids is not None and excluded:
            raise HTTPException(422, 'Choose IDs or all with exclusions')
        for cid in set(ids or []) | excluded:
            self.row(cid)
        count = (self.coverage()['clusters'] if ids is None else len(set(ids))) - len(excluded)
        if not count:
            raise HTTPException(422, 'Empty cluster scope')
        return count

    def materialize(self, row):
        from sqlalchemy import inspect
        if inspect(row).persistent or inspect(row).pending:
            return row
        from masterone.mdm.workflow_entities import assign_entity_ids
        row.payload = next(assign_entity_ids(self.db, self.run, self.generation, [row.payload],
                                             actor=self.run.owner_user_id))
        self.db.add(row)
        self.db.flush()
        self.db.add_all([WorkflowMember(generation_id=self.generation.id, cluster_id=row.id, record_id=rid)
                         for rid in row.payload['member_record_ids']])
        self.db.flush()
        return row

    def confirm_default(self, actor):
        self.generation.singleton_confirmation = {'default_confirmed': True, 'generation_id': self.generation.id,
            'source_hash': self.generation.coverage['source_hash'], 'actor_user_id': actor}


def reader_for_cluster(db, cluster):
    from masterone.mdm.models import ResolutionRun
    generation = db.get(WorkflowGeneration, cluster.generation_id)
    return GenerationReader(db, db.get(ResolutionRun, generation.run_id), generation)


def prepare_virtual_publication(db, run, result, snapshot, generation_id, repair_metadata=None):
    """Retain bounded identity/choice exceptions; leave default singletons implicit."""
    from masterone.mdm.engine_adapter import engine_context
    from masterone.mdm.pipeline.golden import build_golden
    from masterone.mdm.workflow_entities import previous_clusters_by_member, singleton_entity_id
    excluded = set(result.get('conflict_record_ids', []))
    records = sorted((r for r in snapshot['records'] if r['id'] not in excluded), key=lambda r: r['id'])
    by_id = {r['id']: r for r in records}
    groups = [g for g in result['clusters'] if len(g['member_record_ids']) > 1]
    multi = {rid for g in groups for rid in g['member_record_ids']}
    previous = previous_clusters_by_member(db, run, by_id.keys() - multi)
    repairing = repair_metadata is not None and 'repair_record_ids' in repair_metadata
    auto_confirm = set((repair_metadata or {}).get('auto_confirm_record_ids', []))
    candidates = set(previous) | (auto_confirm & by_id.keys() - multi)
    with engine_context(snapshot['profile']):
        for rid in sorted(candidates):
            parent = previous.get(rid)
            if (parent is not None and not repairing and len(parent.payload['member_record_ids']) == 1 and not parent.selections
                    and not parent.payload.get('pending_selections') and not parent.payload.get('requires_manual_review')
                    and parent.payload.get('entity_id') in (None, singleton_entity_id(run.id, rid))):
                continue
            groups.append({'id': virtual_cluster_id(generation_id, rid), 'member_record_ids': [rid],
                'singleton': True, 'status': 'SINGLETON', 'golden_fields': build_golden([rid], by_id),
                'source_snapshot_hash': digest([by_id[rid]]),
                'display_name': by_id[rid].get('raw_fields', {}).get('name') or rid})
    result['clusters'] = groups
    result['virtual_confirmation_default'] = False
    if repairing and run.cluster_revision:
        old_gen = db.query(WorkflowGeneration).filter_by(run_id=run.id, revision=run.cluster_revision).one()
        old_reader = GenerationReader(db, run, old_gen)
        if old_reader.virtual and old_reader._default_confirmed():
            old_records = {r['id']: digest([r]) for r in old_reader.records()}
            explicit = {rid for g in groups for rid in g['member_record_ids']}
            result['virtual_confirmation_default'] = all(
                old_records.get(r['id']) == digest([r]) for r in records if r['id'] not in explicit)
    from dataclasses import asdict

    from masterone.mdm.pipeline.cluster_policy import load_policy
    return {'records': records, 'profile': snapshot['profile'], 'survivorship_policy': asdict(load_policy())}
