from fastapi import HTTPException

from masterone.mdm.workflow_models import WorkflowCluster, WorkflowGeneration, WorkflowMember
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
    context = db.query(WorkflowArtifact.payload['workflow_context']).filter_by(id=generation.artifact_id).scalar() or {}
    source_assessment = context.get('assessment_id')
    if 'assessment_id' not in context and context.get('snapshot_hash'):
        # Existing snapshots predate the assessment freshness field.
        source_assessment = db.query(WorkflowArtifact.payload['assessment_id'].as_string()).filter_by(
            run_id=run.id, phase='DECISION_SNAPSHOT', content_hash=context['snapshot_hash']).limit(1).scalar()
    current_assessment = active_assessment(db, run.id)
    current_id = current_assessment.id if current_assessment else None
    return {'source_decision_revision': context.get('decision_revision'), 'source_assessment_id': source_assessment,
        'stale': context.get('decision_revision') != run.decision_revision or source_assessment != current_id}


def generation_metadata(db, run, generation=None):
    from sqlalchemy import func
    generation = generation or current_generation(db, run, allow_stale=True)
    context = generation_context(db, run, generation)
    stale = context['stale']
    clusters, singles = db.query(func.count(WorkflowCluster.id),
        func.count(WorkflowCluster.id).filter(WorkflowCluster.payload['singleton'].as_boolean().is_(True))).filter_by(
            generation_id=generation.id).one()
    covered = db.query(func.count(WorkflowMember.id)).filter_by(generation_id=generation.id).scalar()
    total = run.record_count
    return {**context, 'revision': generation.revision,
        'stale': stale, 'read_only': stale or run.workflow_state not in ('DECIDE_REVIEW', 'GOLDEN_REVIEW'),
        'publication_status': 'COMPLETE' if covered == total else 'PARTIAL',
        'clusters': clusters, 'singletons': singles, 'multi_record_groups': clusters - singles,
        'covered_records': covered, 'unresolved_records': total - covered, 'total_records': total}


def unresolved_records(db, run, limit, cursor):
    from sqlalchemy import exists

    from masterone.mdm.data_profile import record_read
    from masterone.mdm.models import ResolutionRecord
    from masterone.mdm.workflow_review import paginate
    generation = current_generation(db, run, allow_stale=True)
    member = exists().where(WorkflowMember.generation_id == generation.id, WorkflowMember.record_id == ResolutionRecord.id)
    query = db.query(ResolutionRecord).filter(ResolutionRecord.run_id == run.id, ~member)
    return {**paginate(query, ResolutionRecord.id, limit, cursor, record_read),
            'generation': generation_metadata(db, run, generation)}


def cluster_row(db, run, cluster_id, *, allow_stale=False):
    generation = current_generation(db, run, allow_stale=allow_stale)
    row = db.query(WorkflowCluster).filter_by(id=cluster_id, generation_id=generation.id).one_or_none()
    if row is None:
        raise HTTPException(404, 'Cluster not found')
    return row


def evidence_page(items, limit=25, cursor=0):
    end = cursor + limit
    return {'items': items[cursor:end], 'total': len(items),
            'next_cursor': str(end) if end < len(items) else None}


def cluster_read(row, evidence_limit=25, evidence_cursor=0):
    return {'cluster_id': row.id, 'member_record_ids': row.payload['member_record_ids'],
            'member_count': len(row.payload['member_record_ids']), 'singleton': row.payload['singleton'],
            'status': row.payload['status'], 'evidence': evidence_page(row.payload.get('edge_order', []), evidence_limit, evidence_cursor)}


def list_clusters(db, run, limit, cursor):
    from masterone.mdm.workflow_review import paginate
    generation = current_generation(db, run, allow_stale=True)
    return {**paginate(db.query(WorkflowCluster).filter_by(generation_id=generation.id), WorkflowCluster.id,
                    limit, cursor, cluster_read), 'generation': generation_metadata(db, run, generation)}


def cluster_detail(db, run, cluster_id, limit, cursor, evidence_limit=25, evidence_cursor=0):
    from masterone.mdm.models import ResolutionRecord
    from masterone.mdm.workflow_review import paginate
    row = cluster_row(db, run, cluster_id, allow_stale=True)
    query = db.query(ResolutionRecord).join(WorkflowMember, WorkflowMember.record_id == ResolutionRecord.id).filter(WorkflowMember.cluster_id == row.id)
    from masterone.mdm.workflow_artifacts import load
    generation = current_generation(db, run, allow_stale=True)
    artifact = load(db, generation.artifact_id, run.id, 'CLUSTER')
    members = set(row.payload['member_record_ids'])
    pairs = [pair for pair in artifact['pairs'] if {pair['left_id'], pair['right_id']} <= members]
    from masterone.mdm.pipeline.semantic_address import with_annotations
    annotated = list(with_annotations(pairs, artifact.get('semantic_annotations', {})))
    return {**cluster_read(row, evidence_limit, evidence_cursor), 'generation': generation_metadata(db, run, generation),
        'pair_evidence': evidence_page(annotated, evidence_limit, evidence_cursor),
        'llm_stats': artifact.get('llm_stats', {}),
        'workflow_context': artifact.get('workflow_context', {}),
        'members': paginate(query, ResolutionRecord.id, limit, cursor,
        lambda r: {'record_id': r.id, 'source_system': r.source_system, 'raw_fields': r.raw_fields,
                   'normalized_fields': r.normalized_fields, 'extra_fields': r.extra_fields})}
