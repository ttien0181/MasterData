"""Durable accepted-edge queue, derived from decisions and published membership.

No queue is cleared by enqueue/HTTP success. An Accept is consumed only when its
endpoints occur together in the current published generation.
"""
from copy import deepcopy

from masterone.mdm.generation_staging import digest, config_identity
from masterone.mdm.models import ResolutionEngineSelection, ResolutionRecord
from masterone.mdm.workflow_assessment_reads import active_assessment, effective_result
from masterone.mdm.workflow_models import WorkflowCluster, WorkflowGeneration, WorkflowPair
from masterone.mdm.workflow_service import conflict


def code_identity():
    from hashlib import sha256
    from pathlib import Path
    return sha256(Path(__file__).read_bytes()).hexdigest()


def published_groups(db, run):
    if not run.cluster_revision:
        return []
    generation=db.query(WorkflowGeneration).filter_by(run_id=run.id,revision=run.cluster_revision).one()
    if generation.storage_version not in ('workflow_json_v1','workflow_virtual_v2'):
        conflict('UNSUPPORTED_STORAGE_VERSION')
    return db.query(WorkflowCluster).filter_by(generation_id=generation.id).all()


def accepted_edges(db, run_id):
    return db.query(WorkflowPair.id,WorkflowPair.baseline['left_id'].as_string(),
                    WorkflowPair.baseline['right_id'].as_string()).filter_by(run_id=run_id,decision='ACCEPTED').all()


def queue_summary(db, run):
    membership={rid:group.id for group in published_groups(db,run) for rid in group.payload['member_record_ids']}
    accepted=accepted_edges(db,run.id)
    pending=[pid for pid,left,right in accepted if left not in membership or membership[left]!=membership.get(right)]
    return {'pending_count':len(pending),'accepted_count':len(accepted),'published_count':len(accepted)-len(pending),
            'decision_revision':run.decision_revision,'cluster_revision':run.cluster_revision}


def accepted_snapshot(db, run, mode):
    """Read metadata + source records + explicit decisions, never all candidate JSON."""
    from masterone.mdm.engine_adapter import engine_context
    from masterone.mdm.pipeline.golden_v2 import record_payload
    from masterone.mdm.pipeline.pair_assessment import assessment_record
    from masterone.mdm.pipeline.decision_semantics import load_cannot_links
    records=[record_payload(row) for row in db.query(ResolutionRecord).filter_by(run_id=run.id).order_by(ResolutionRecord.id)]
    selected=db.get(ResolutionEngineSelection,run.id)
    assessment=active_assessment(db,run.id)
    if assessment:
        metadata=assessment.configuration
        if digest(records)!=metadata['source_input_hash']:
            raise ValueError('ASSESSMENT_INPUT_CHANGED')
        prepared=[assessment_record(record) for record in records]
        if digest(prepared)!=metadata['input_hash']:
            raise ValueError('ASSESSMENT_INPUT_CHANGED')
    else:
        from masterone.mdm.workflow_artifacts import read_value
        from masterone.mdm.workflow_models import WorkflowArtifact
        artifact=db.query(WorkflowArtifact).filter_by(run_id=run.id,phase='DECIDE').order_by(WorkflowArtifact.created_at.desc()).first()
        if artifact is None:
            raise ValueError('DECISIONS_NOT_READY')
        metadata={key:read_value(db,artifact.id,run.id,'DECIDE',(key,)) for key in
                  ('input_hash','profile_identity','execution_identity','policy_version')}
        if digest(records)!=metadata['input_hash'] or digest(selected.configuration)!=metadata['profile_identity']:
            raise ValueError('WORKFLOW_INPUT_CHANGED')
        metadata['profile']=selected.configuration
        prepared=records
    profile=metadata['profile']
    with engine_context(profile):
        if digest(config_identity(metadata['policy_version']))!=metadata['execution_identity']:
            raise ValueError('WORKFLOW_CONFIG_CHANGED')
    rows=db.query(WorkflowPair).filter(WorkflowPair.run_id==run.id,
                                     WorkflowPair.decision.in_(('ACCEPTED','REJECTED','NON_MATCH'))).order_by(WorkflowPair.id).all()
    groups=published_groups(db,run)
    membership={rid:group.id for group in groups for rid in group.payload['member_record_ids']}
    accepted=[pair for pair in rows if pair.decision=='ACCEPTED']
    pending=[pair for pair in accepted if pair.baseline['left_id'] not in membership or
             membership[pair.baseline['left_id']]!=membership.get(pair.baseline['right_id'])]
    # Closure includes complete existing groups and every accepted connection.
    affected={rid for pair in pending for rid in (pair.baseline['left_id'],pair.baseline['right_id'])}
    if mode=='all':affected={record['id'] for record in records}
    else:
        from masterone.mdm.workflow_cluster_reads import generation_context
        if run.cluster_revision:
            generation=db.query(WorkflowGeneration).filter_by(run_id=run.id,revision=run.cluster_revision).one()
            if generation_context(db,run,generation)['source_assessment_id']!=(assessment.id if assessment else None):
                raise ValueError('QUEUE_REASSESSMENT_REQUIRES_ALL')
        while True:
            previous=len(affected)
            for group in groups:
                members=set(group.payload['member_record_ids'])
                if members & affected:affected.update(members)
            for pair in accepted:
                ends={pair.baseline['left_id'],pair.baseline['right_id']}
                if ends & affected:affected.update(ends)
            if previous==len(affected):break
    negatives=sorted(load_cannot_links(db,run.id))
    for left,right in negatives:
        if left in membership and membership[left]==membership.get(right):
            raise ValueError('QUEUE_PUBLISHED_GROUP_CONFLICT')
    pairs=[pair for pair in rows if {pair.baseline['left_id'],pair.baseline['right_id']}<=affected]
    # A no_merge veto inside a published group also needs explicit repair.
    if any(pair.decision=='REJECTED' and pair.baseline['left_id'] in membership and
           membership[pair.baseline['left_id']]==membership.get(pair.baseline['right_id']) for pair in rows):
        raise ValueError('QUEUE_PUBLISHED_GROUP_CONFLICT')
    from masterone.mdm.workflow_models import WorkflowOperation
    approvals=[op.result for op in db.query(WorkflowOperation).filter_by(run_id=run.id,operation_type='GROUP_APPROVAL',status='COMPLETED')]
    for op in db.query(WorkflowOperation).filter_by(run_id=run.id,operation_type='CLUSTER',status='COMPLETED'):
        approvals.extend(op.result.get('structure_group_approvals',[]))
    snapshot={'records':prepared,'pairs':[effective_result(pair) for pair in pairs],'profile':profile,
              'overlays':{pair.id:pair.decision for pair in pairs},
              'review_reasons':{pair.id:pair.review_reason for pair in pairs if pair.review_reason},
              'negative_pairs':negatives,'policy_version':metadata['policy_version'],
              'input_hash':metadata['input_hash'],'profile_identity':metadata['profile_identity'],
              'execution_identity':metadata['execution_identity'],'workflow_revision':run.workflow_revision,
              'decision_revision':run.decision_revision,'group_approvals':approvals,
              'queue_mode':mode,'queue_code_identity':code_identity(),'queue_affected_records':sorted(affected),
              'queue_previous_groups':[deepcopy(group.payload) for group in groups],
              'queue_pair_ids':[pair.id for pair in pending]}
    if assessment:
        snapshot.update(assessment_id=assessment.id,source_input_hash=metadata['source_input_hash'],
                        source_profile_identity=metadata['source_profile_identity'])
    return snapshot


def compute_queue_publication(snapshot,run_id,revision):
    from masterone.mdm.workflow_finalize import compute_publication
    affected=set(snapshot['queue_affected_records'])
    local={**snapshot,'records':[record for record in snapshot['records'] if record['id'] in affected]}
    local['negative_pairs']=[pair for pair in snapshot['negative_pairs'] if set(pair)<=affected]
    local['group_approvals']=[approval for approval in snapshot['group_approvals'] if set(approval['record_ids'])<=affected]
    from masterone.mdm.pipeline.matching import value_frequencies
    result=compute_publication(local,run_id,revision,virtual_singletons=True,
                               frequencies=value_frequencies(snapshot['records']))
    for previous in snapshot['queue_previous_groups']:
        members=set(previous['member_record_ids'])
        if members & affected:
            if not any(members<=set(group['member_record_ids']) for group in result['clusters']):
                raise ValueError('QUEUE_EXISTING_GROUP_CONFLICT')
            continue
        preserved={key:deepcopy(value) for key,value in previous.items() if key not in
                   ('entity_id','ancestor_entity_ids','retained_ancestor_cluster_ids','identity_version','anchor_record_id')}
        preserved['id']=digest([run_id,revision,sorted(members)])
        result['clusters'].append(preserved)
        result['pairs'].extend(previous.get('pair_evidence',[]))
    result['safe_clusters']=result['clusters']
    excluded=set(result['conflict_record_ids'])
    multi=sum(len(group['member_record_ids']) for group in result['clusters'])
    singles=len(snapshot['records'])-len(excluded)-multi
    result['publication'].update(total_records=len(snapshot['records']),covered_records=len(snapshot['records'])-len(excluded),
        unresolved_records=len(excluded),multi_record_groups=len(result['clusters']),singletons=singles,
        clusters=len(result['clusters'])+singles,publication_status='PARTIAL' if excluded else 'COMPLETE')
    result['cluster_metrics'].update({key:result['publication'][key] for key in ('total_records','covered_records','unresolved_records')})
    result['region_cache']={'reused_records':len(snapshot['records'])-len(affected),'recomputed_records':len(affected)}
    return result
