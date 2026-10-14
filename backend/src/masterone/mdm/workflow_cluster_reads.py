
from masterone.mdm.workflow_models import WorkflowGeneration, WorkflowMember
from masterone.mdm.workflow_service import conflict


def current_generation(db, run, *, allow_stale=False):
    states = ('GOLDEN_REVIEW', 'FINALIZED', 'DECIDE_REVIEW', 'CLUSTER_QUEUED', 'CLUSTER_RUNNING') if allow_stale else ('GOLDEN_REVIEW', 'FINALIZED', 'DECIDE_REVIEW')
    if run.workflow_state not in states or not run.cluster_revision:
        conflict('CLUSTERS_NOT_READY', state=run.workflow_state)
    generation = db.query(WorkflowGeneration).filter_by(run_id=run.id, revision=run.cluster_revision).one()
    if not allow_stale and generation_context(db, run, generation)['stale']:
        conflict('CLUSTERS_NOT_READY', state=run.workflow_state)
    return generation


def generation_context(db, run, generation):
    from masterone.mdm.workflow_assessment_reads import active_assessment
    from masterone.mdm.workflow_models import WorkflowArtifact
    context_field = (WorkflowArtifact.payload['metadata']['workflow_context'] if generation.storage_version == 'workflow_parquet_v2'
                     else WorkflowArtifact.payload['workflow_context'])
    context = db.query(context_field).filter_by(id=generation.artifact_id).scalar() or {}
    source_assessment = context.get('assessment_id')
    if 'assessment_id' not in context and context.get('snapshot_hash'):
        from masterone.mdm.workflow_artifacts import logical_artifact_id
        # Existing snapshots predate the assessment freshness field.
        identifier = logical_artifact_id(db, run.id, 'DECISION_SNAPSHOT', context['snapshot_hash'])
        source_assessment = db.query(WorkflowArtifact.payload['assessment_id'].as_string()).filter_by(id=identifier).scalar()
    current_assessment = active_assessment(db, run.id)
    current_id = current_assessment.id if current_assessment else None
    return {'source_decision_revision': context.get('decision_revision'), 'source_assessment_id': source_assessment,
        'stale': context.get('decision_revision') != run.decision_revision or source_assessment != current_id}


def generation_metadata(db, run, generation=None):
    from masterone.mdm.workflow_generation_reads import GenerationReader
    generation = generation or current_generation(db, run, allow_stale=True)
    context = generation_context(db, run, generation)
    stale = context['stale']
    counts = GenerationReader(db, run, generation).coverage()
    covered, total = counts['covered_records'], counts['total_records']
    return {**context, 'revision': generation.revision,
        'stale': stale, 'read_only': stale or run.workflow_state not in ('DECIDE_REVIEW', 'GOLDEN_REVIEW')
            or run.status in ('QUEUED', 'RUNNING'),
        'publication_status': 'COMPLETE' if covered == total else 'PARTIAL',
        **counts}


def unresolved_records(db, run, limit, cursor):
    from sqlalchemy import exists

    from masterone.mdm.data_profile import record_read
    from masterone.mdm.models import ResolutionRecord
    from masterone.mdm.workflow_review import paginate
    generation = current_generation(db, run, allow_stale=True)
    from masterone.mdm.workflow_generation_reads import GenerationReader
    reader = GenerationReader(db, run, generation)
    if generation.storage_version == 'workflow_parquet_v2':
        from itertools import islice
        from types import SimpleNamespace

        from masterone.mdm.workflow_snapshot_storage import open_snapshot
        with open_snapshot(db, generation.artifact_id, run.id, 'CLUSTER') as output:
            ids = list(islice(output.iter_rows_after('excluded', cursor or ''), limit+1))
            context = output.get('workflow_context')
            with open_snapshot(db, context['snapshot_artifact_id'], run.id, 'DECISION_SNAPSHOT') as source:
                if source.row.content_hash != context['snapshot_hash']:
                    raise ValueError('WORKFLOW_ARTIFACT_INVALID')
                items = [record_read(SimpleNamespace(**record)) for record in source.record_page(item['id'] for item in ids[:limit])]
            total = output.descriptors.get('excluded', {}).get('count', 0)
        return {'items': items, 'total': total, 'next_cursor': ids[limit-1]['id'] if len(ids)>limit else None,
                'generation': generation_metadata(db, run, generation)}
    if reader.virtual:
        from masterone.mdm.workflow_artifacts import load
        output = load(db, generation.artifact_id, run.id, 'CLUSTER')
        ids = output.get('conflict_record_ids', [])
        query = db.query(ResolutionRecord).filter(ResolutionRecord.run_id == run.id, ResolutionRecord.id.in_(ids))
        return {**paginate(query, ResolutionRecord.id, limit, cursor, record_read),
                'generation': generation_metadata(db, run, generation)}
    member = exists().where(WorkflowMember.generation_id == generation.id, WorkflowMember.record_id == ResolutionRecord.id)
    query = db.query(ResolutionRecord).filter(ResolutionRecord.run_id == run.id, ~member)
    return {**paginate(query, ResolutionRecord.id, limit, cursor, record_read),
            'generation': generation_metadata(db, run, generation)}


def cluster_row(db, run, cluster_id, *, allow_stale=False):
    from masterone.mdm.workflow_generation_reads import GenerationReader
    generation = current_generation(db, run, allow_stale=allow_stale)
    return GenerationReader(db, run, generation).row(cluster_id)


def evidence_page(items, limit=25, cursor=0):
    end = cursor + limit
    return {'items': items[cursor:end], 'total': len(items),
            'next_cursor': str(end) if end < len(items) else None}


def cluster_read(row, evidence_limit=25, evidence_cursor=0):
    return {'cluster_id': row.id, 'member_record_ids': row.payload['member_record_ids'],
            'entity_id': row.payload.get('entity_id'), 'ancestor_entity_ids': row.payload.get('ancestor_entity_ids', []),
            'member_count': len(row.payload['member_record_ids']), 'singleton': row.payload['singleton'],
            'display_name': row.payload.get('display_name'), 'preview_members': row.payload.get('preview_members'),
            'status': row.payload['status'], 'evidence': evidence_page(row.payload.get('edge_order', []), evidence_limit, evidence_cursor)}


def list_clusters(db, run, limit, cursor):
    from masterone.mdm.workflow_generation_reads import GenerationReader
    generation = current_generation(db, run, allow_stale=True)
    return {**GenerationReader(db, run, generation).group_page(limit, cursor, cluster_read),
            'generation': generation_metadata(db, run, generation)}


def cluster_detail(db, run, cluster_id, limit, cursor, evidence_limit=25, evidence_cursor=0):
    from masterone.mdm.models import ResolutionRecord
    from masterone.mdm.workflow_review import paginate
    row = cluster_row(db, run, cluster_id, allow_stale=True)
    query = db.query(ResolutionRecord).join(WorkflowMember, WorkflowMember.record_id == ResolutionRecord.id).filter(WorkflowMember.cluster_id == row.id)
    from masterone.mdm.workflow_artifacts import load
    generation = current_generation(db, run, allow_stale=True)
    from masterone.mdm.workflow_generation_reads import GenerationReader
    reader = GenerationReader(db, run, generation)
    if row.payload.get('local_evidence_version') == 1:
        artifact = row.payload
        annotated = artifact['pair_evidence']
    else:
        artifact = load(db, generation.artifact_id, run.id, 'CLUSTER')
        members = set(row.payload['member_record_ids'])
        pairs = [pair for pair in artifact['pairs'] if {pair['left_id'], pair['right_id']} <= members]
        from masterone.mdm.pipeline.semantic_address import with_annotations
        annotated = list(with_annotations(pairs, artifact.get('semantic_annotations', {})))
    if reader.virtual:
        records = [reader.record(rid) for rid in sorted(row.payload['member_record_ids']) if cursor is None or rid > cursor]
        page = records[:limit]
        members_page = {'items': [{'record_id': r['id'], 'source_system': r['source_system'],
            'raw_fields': r['raw_fields'], 'normalized_fields': r['normalized_fields'], 'extra_fields': r.get('extra_fields', {})}
            for r in page], 'total': len(row.payload['member_record_ids']),
            'next_cursor': page[-1]['id'] if len(records) > limit else None}
    else:
        members_page = paginate(query, ResolutionRecord.id, limit, cursor,
            lambda r: {'record_id': r.id, 'source_system': r.source_system, 'raw_fields': r.raw_fields,
                       'normalized_fields': r.normalized_fields, 'extra_fields': r.extra_fields})
    return {**cluster_read(row, evidence_limit, evidence_cursor), 'generation': generation_metadata(db, run, generation),
        'pair_evidence': evidence_page(annotated, evidence_limit, evidence_cursor),
        'llm_stats': artifact.get('llm_stats', {}),
        'workflow_context': artifact.get('workflow_context', {}),
        'members': members_page}
