from fastapi import HTTPException

from masterone.mdm.workflow_models import WorkflowCluster, WorkflowGeneration, WorkflowMember
from masterone.mdm.workflow_service import conflict


def current_generation(db, run):
    if run.workflow_state not in ('GOLDEN_REVIEW', 'FINALIZED', 'DECIDE_REVIEW') or not run.cluster_revision:
        conflict('CLUSTERS_NOT_READY', state=run.workflow_state)
    generation = db.query(WorkflowGeneration).filter_by(run_id=run.id, revision=run.cluster_revision).one()
    from masterone.mdm.workflow_models import WorkflowArtifact
    revision = db.query(WorkflowArtifact.payload['workflow_context']['decision_revision'].as_integer()).filter_by(id=generation.artifact_id).scalar()
    if revision != run.decision_revision:
        conflict('CLUSTERS_NOT_READY', state=run.workflow_state)
    return generation


def cluster_row(db, run, cluster_id):
    generation = current_generation(db, run)
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
    generation = current_generation(db, run)
    return paginate(db.query(WorkflowCluster).filter_by(generation_id=generation.id), WorkflowCluster.id,
                    limit, cursor, cluster_read)


def cluster_detail(db, run, cluster_id, limit, cursor, evidence_limit=25, evidence_cursor=0):
    from masterone.mdm.models import ResolutionRecord
    from masterone.mdm.workflow_review import paginate
    row = cluster_row(db, run, cluster_id)
    query = db.query(ResolutionRecord).join(WorkflowMember, WorkflowMember.record_id == ResolutionRecord.id).filter(WorkflowMember.cluster_id == row.id)
    from masterone.mdm.workflow_artifacts import load
    generation = current_generation(db, run)
    artifact = load(db, generation.artifact_id, run.id, 'CLUSTER')
    members = set(row.payload['member_record_ids'])
    pairs = [pair for pair in artifact['pairs'] if {pair['left_id'], pair['right_id']} <= members]
    from masterone.mdm.pipeline.semantic_address import with_annotations
    annotated = list(with_annotations(pairs, artifact.get('semantic_annotations', {})))
    return {**cluster_read(row, evidence_limit, evidence_cursor),
        'pair_evidence': evidence_page(annotated, evidence_limit, evidence_cursor),
        'llm_stats': artifact.get('llm_stats', {}),
        'workflow_context': artifact.get('workflow_context', {}),
        'members': paginate(query, ResolutionRecord.id, limit, cursor,
        lambda r: {'record_id': r.id, 'source_system': r.source_system, 'raw_fields': r.raw_fields,
                   'normalized_fields': r.normalized_fields, 'extra_fields': r.extra_fields})}
