"""Run-scoped stable identity planning and transactional entity history."""
from collections import defaultdict
from datetime import UTC
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import delete, select

from masterone.mdm.models import ResolutionEntity, ResolutionEntityMember, ResolutionEntityVersion
from masterone.mdm.workflow_models import WorkflowArtifact, WorkflowCluster, WorkflowGeneration

ENTITY_NAMESPACE = uuid5(NAMESPACE_URL, 'p119/workflow/entities/v1')
SINGLETON_NAMESPACE = uuid5(NAMESPACE_URL, 'p119/workflow/singletons/v1')


def singleton_entity_id(run_id, record_id):
    return str(uuid5(SINGLETON_NAMESPACE, f'{run_id}:{record_id}'))


def previous_clusters_by_member(db, run, record_ids):
    """Latest published choice for each requested member, including partial gaps."""
    remaining, found = set(record_ids), {}
    if not remaining:
        return found
    query = db.query(WorkflowCluster).join(WorkflowGeneration).filter(
        WorkflowGeneration.run_id == run.id, WorkflowGeneration.revision <= run.cluster_revision
    ).order_by(WorkflowGeneration.revision.desc(), WorkflowCluster.id).yield_per(512)
    for cluster in query:
        for rid in remaining.intersection(cluster.payload['member_record_ids']):
            found[rid] = cluster
        remaining.difference_update(cluster.payload['member_record_ids'])
        if not remaining:
            break
    if remaining:
        from masterone.mdm.workflow_generation_reads import GenerationReader
        from masterone.mdm.workflow_models import WorkflowMember
        generations = db.query(WorkflowGeneration).filter(WorkflowGeneration.run_id == run.id,
            WorkflowGeneration.revision <= run.cluster_revision,
            WorkflowGeneration.storage_version.in_(('workflow_virtual_v2', 'workflow_parquet_v2'))).order_by(
                WorkflowGeneration.revision.desc()).yield_per(32)
        for generation in generations:
            reader = GenerationReader(db, run, generation)
            for record in reader.records():
                rid = record['id']
                if rid in remaining and not db.query(WorkflowMember.id).filter_by(
                        generation_id=generation.id, record_id=rid).first():
                    found[rid] = reader._virtual_row(record)
                    remaining.remove(rid)
                if not remaining:
                    return found
    return found


def _parent_state(db, run, record_ids):
    previous = previous_clusters_by_member(db, run, record_ids)
    entities = {e.id: e for e in db.query(ResolutionEntity).filter_by(run_id=run.id)}
    parents, record_parents, generation_dates = {}, {}, {}
    for rid, cluster in previous.items():
        eid = cluster.payload.get('entity_id') or (singleton_entity_id(run.id, rid)
            if len(cluster.payload['member_record_ids']) == 1 else str(uuid5(ENTITY_NAMESPACE, f'{run.id}:legacy:{cluster.id}')))
        entity = entities.get(eid)
        if eid not in parents:
            if not entity and cluster.generation_id not in generation_dates:
                generation = db.get(WorkflowGeneration, cluster.generation_id)
                generation_dates[cluster.generation_id] = db.get(WorkflowArtifact, generation.artifact_id).created_at
            created = entity.created_at if entity else generation_dates[cluster.generation_id]
            parents[eid] = {'entity_id': eid, 'members': set(),
                            'anchor_record_id': cluster.payload.get('anchor_record_id') or min(cluster.payload['member_record_ids']),
                            'created_at': created}
        parents[eid]['members'].update(cluster.payload['member_record_ids'])
        record_parents[rid] = eid
    # Existing active memberships preserve identity for records omitted by a
    # partial publication; eligibility still comes solely from the frozen plan.
    query = db.query(ResolutionEntityMember, ResolutionEntity).join(ResolutionEntity).filter(
        ResolutionEntity.run_id == run.id, ResolutionEntity.status == 'ACTIVE').yield_per(512)
    for member, entity in query:
        if member.record_id not in record_ids:
            continue
        if entity.id not in parents:
            version = db.query(ResolutionEntityVersion).filter_by(entity_id=entity.id).order_by(
                ResolutionEntityVersion.version.desc()).first()
            metadata = version.golden_fields.get('_workflow_identity', {}) if version else {}
            parents[entity.id] = {'entity_id': entity.id, 'members': set(),
                                  'anchor_record_id': metadata.get('anchor_record_id') or member.record_id,
                                  'created_at': entity.created_at}
        parents[entity.id]['members'].add(member.record_id)
        record_parents[member.record_id] = entity.id
    return parents, record_parents, previous


def plan_entity_ids(db, run, generation, groups):
    """Plan the whole generation before mutating any entity membership."""
    groups = list(groups)
    if not groups:
        return []
    ids = [rid for g in groups for rid in g['member_record_ids']]
    if len(ids) != len(set(ids)) or len({g['id'] for g in groups}) != len(groups):
        raise ValueError('WORKFLOW_ENTITY_IDENTITY_INVALID')
    parents, record_parents, previous = _parent_state(db, run, set(ids))
    overlaps = defaultdict(lambda: defaultdict(int))
    by_id = {g['id']: g for g in groups}
    for g in groups:
        for rid in g['member_record_ids']:
            if rid in record_parents:
                overlaps[record_parents[rid]][g['id']] += 1
    assigned = {}
    def parent_order(parent):
        created = parent['created_at']
        timestamp = created.replace(tzinfo=UTC).timestamp() if created.tzinfo is None else created.timestamp()
        return timestamp, parent['entity_id']
    for parent in sorted(parents.values(), key=parent_order):
        children = overlaps[parent['entity_id']]
        if not children:
            continue
        preferred = min(children, key=lambda gid: (parent['anchor_record_id'] not in by_id[gid]['member_record_ids'],
                                                    -children[gid], min(by_id[gid]['member_record_ids'])))
        if preferred not in assigned:
            assigned[preferred] = parent['entity_id']
    result = []
    for g in groups:
        members = sorted(g['member_record_ids'])
        eid = assigned.get(g['id']) or (singleton_entity_id(run.id, members[0]) if len(members) == 1 else
                                       str(uuid5(ENTITY_NAMESPACE, f'{run.id}:{generation.revision}:{g["id"]}')))
        if g.get('entity_id') and g['entity_id'] != eid:
            raise ValueError('WORKFLOW_ENTITY_IDENTITY_CHANGED')
        parent = parents.get(eid)
        anchor = parent['anchor_record_id'] if parent and parent['anchor_record_id'] in members else min(members)
        ancestors = sorted({record_parents[rid] for rid in members if rid in record_parents})
        retained = sorted({previous[rid].id for rid in members if rid in previous and record_parents.get(rid) == eid})
        result.append({**g, 'entity_id': eid, 'anchor_record_id': anchor, 'ancestor_entity_ids': ancestors,
                       'retained_ancestor_cluster_ids': retained, 'identity_version': 1})
    if len({g['entity_id'] for g in result}) != len(result):
        raise ValueError('WORKFLOW_ENTITY_IDENTITY_INVALID')
    return result


def _version(db, entity, generation, group, actor, golden=None):
    metadata = {k: group[k] for k in ('anchor_record_id', 'ancestor_entity_ids', 'identity_version', 'member_record_ids')}
    metadata.update(generation_id=generation.id, generation_revision=generation.revision, cluster_id=group['id'])
    db.add(ResolutionEntityVersion(entity_id=entity.id, version=entity.version,
        golden_fields={**(golden or group['golden_fields']), '_workflow_identity': metadata}, changed_by=actor))


def record_entity_version(db, run, cluster, *, actor):
    """Store selected golden and draft provenance without changing older versions."""
    eid = cluster.payload.get('entity_id')
    if not eid:
        return  # Historical generations keep their existing identity contract.
    entity = db.get(ResolutionEntity, eid)
    if entity is None or entity.run_id != run.id:
        raise ValueError('WORKFLOW_ENTITY_IDENTITY_INVALID')
    generation = db.get(WorkflowGeneration, cluster.generation_id)
    entity.version += 1
    selected_entity_version(db, entity, generation, cluster, actor=actor)


def selected_entity_version(db, entity, generation, cluster, *, actor):
    from masterone.mdm.golden_service import selected_golden
    group = {**cluster.payload, 'id': cluster.id, 'golden_fields': cluster.golden}
    golden = selected_golden(db, cluster)
    golden['_workflow_choices'] = {'selections': cluster.selections, 'confirmed': cluster.confirmed,
                                   'pending_selections': cluster.payload.get('pending_selections', {}),
                                   'discarded_pending_selections': cluster.payload.get('discarded_pending_selections', [])}
    _version(db, entity, generation, group, actor, golden)


def assign_entity_ids(db, run, generation, groups, *, actor, record_versions=True):
    """Persist an already globally planned assignment in the caller's transaction."""
    planned = plan_entity_ids(db, run, generation, groups)
    if not planned:
        return iter(())
    covered = {rid for group in planned for rid in group['member_record_ids']}
    prior_entities = {e.id: e for e in db.query(ResolutionEntity).filter_by(run_id=run.id)}
    successors = defaultdict(set)
    for g in planned:
        for eid in g['ancestor_entity_ids']:
            successors[eid].add(g['entity_id'])
    active = {g['entity_id'] for g in planned}
    for offset in range(0, len(planned), 500):
        if db.query(ResolutionEntity.id).filter(ResolutionEntity.id.in_(
                [g['entity_id'] for g in planned[offset:offset + 500]]), ResolutionEntity.run_id != run.id).first():
            raise ValueError('WORKFLOW_ENTITY_IDENTITY_INVALID')
    held = {eid for eid, rid in db.query(ResolutionEntityMember.entity_id, ResolutionEntityMember.record_id).join(
        ResolutionEntity).filter(ResolutionEntity.run_id == run.id).yield_per(512) if rid not in covered}
    for eid, children in successors.items():
        if eid in prior_entities and eid not in active and eid not in held:
            entity = prior_entities[eid]
            entity.status = 'MERGED' if len(children) == 1 else 'SPLIT'
    # Remove only covered memberships. An excluded source may keep its last
    # identity while its current publication remains unresolved.
    records = sorted(covered)
    owners = select(ResolutionEntity.id).where(ResolutionEntity.run_id == run.id)
    for offset in range(0, len(records), 500):
        db.execute(delete(ResolutionEntityMember).where(ResolutionEntityMember.entity_id.in_(owners),
            ResolutionEntityMember.record_id.in_(records[offset:offset + 500])).execution_options(synchronize_session=False))
    new_entities = []
    for g in planned:
        entity = prior_entities.get(g['entity_id'])
        if entity is None:
            entity = ResolutionEntity(id=g['entity_id'], run_id=run.id, version=1, status='ACTIVE', entity_type='UNKNOWN')
            new_entities.append(entity)
            prior_entities[entity.id] = entity
        else:
            entity.version += 1
            entity.status = 'ACTIVE'
    for offset in range(0, len(new_entities), 500):
        db.add_all(new_entities[offset:offset + 500])
        db.flush()
    pending_members, pending_versions = 0, 0
    for g in planned:
        entity = prior_entities[g['entity_id']]
        for rid in g['member_record_ids']:
            db.add(ResolutionEntityMember(entity_id=entity.id, record_id=rid, added_by=actor))
            pending_members += 1
        if record_versions:
            _version(db, entity, generation, g, actor)
        pending_versions += 1
        if pending_members >= 2000 or pending_versions >= 500:
            db.flush()
            pending_members = pending_versions = 0
    db.flush()
    return iter(planned)
