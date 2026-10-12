"""Server-owned choices decorate engine drafts; survivorship stays in the engine."""
from copy import deepcopy

from fastapi import HTTPException

from masterone.mdm.generation_staging import digest
from masterone.mdm.models import ResolutionRecord
from masterone.mdm.workflow_cluster_reads import cluster_row, current_generation
from masterone.mdm.workflow_models import WorkflowCluster, WorkflowGeneration
from masterone.mdm.workflow_service import (
    active_operation,
    audit,
    check_revision,
    conflict,
    enabled_run,
    get_workflow,
    new_operation,
    replay,
)

RULE_FIELDS = frozenset(('name', 'phone', 'email', 'address', 'dob', 'government_id'))


def source_value(record, field):
    if field in ('phone', 'email'):
        return record.normalized_fields.get(field) or record.raw_fields.get(field)
    return record.raw_fields.get(field, record.extra_fields.get(field))


def selected_golden(db, cluster):
    golden = deepcopy(cluster.golden)
    fields = golden.setdefault('fields', {})
    for field, selection in cluster.selections.items():
        previous = fields.get(field, {})
        if selection['selection_mode'] == 'SOURCE_CHOICE':
            record = db.get(ResolutionRecord, selection['source_record_id'])
            value = source_value(record, field)
            provenance = dict(source_record_id=record.id, source_system=record.source_system, provenance='SOURCE_CHOICE')
        else:
            value = selection['value']
            provenance = dict(source_record_id=None, provenance='STEWARD_OVERRIDE')
        chosen = value[0] if isinstance(value, list) and value else value
        fields[field] = {**previous, **provenance, 'value': chosen}
        if field in ('phone', 'email'):
            values = deepcopy(previous.get('values', []))
            chosen_values = value if isinstance(value, list) else [value]
            selected = [{**provenance, 'value': v} for v in chosen_values if v]
            fields[field]['values'] = selected + [v for v in values if v['value'] not in chosen_values]
    return golden


def golden_read(db, run, cluster):
    golden = selected_golden(db, cluster)
    rows = db.query(ResolutionRecord).filter(ResolutionRecord.id.in_(cluster.payload['member_record_ids'])).all()
    alternatives = {}
    names = RULE_FIELDS | {k for row in rows for k in row.extra_fields}
    for field in sorted(names):
        alternatives[field] = [{'source_record_id': r.id, 'source_system': r.source_system,
            'value': source_value(r, field), 'auto_rule_available': field in RULE_FIELDS}
            for r in rows if source_value(r, field) is not None]
    return {'cluster_id': cluster.id, 'cluster_revision': run.cluster_revision,
            'golden_revision': run.golden_revision, 'revision': cluster.revision,
            'status': 'FINALIZED' if run.workflow_state == 'FINALIZED' else 'CONFIRMED' if cluster.confirmed else 'DRAFT',
            'fields': golden.get('fields', {}), 'alternatives': alternatives,
            'selections': cluster.selections, 'requires_manual_review': cluster.payload.get('requires_manual_review', [])}


def validate_selections(db, run, cluster, selections):
    members = set(cluster.payload['member_record_ids'])
    for field, selection in selections.items():
        if selection['selection_mode'] == 'SOURCE_CHOICE':
            rid = selection.get('source_record_id')
            if rid not in members:
                raise HTTPException(422, {'code': 'GOLDEN_SOURCE_NOT_MEMBER', 'field': field})
            record = db.get(ResolutionRecord, rid)
            value = source_value(record, field)
            if record.run_id != run.id or value is None or (selection.get('value') is not None and selection['value'] != value):
                raise HTTPException(422, {'code': 'GOLDEN_SOURCE_VALUE_INVALID', 'field': field})
        else:
            value = selection.get('value')
            if field not in RULE_FIELDS or not (isinstance(value, str) or field in ('phone', 'email') and isinstance(value, list) and all(isinstance(v, str) for v in value)):
                raise HTTPException(422, {'code': 'GOLDEN_OVERRIDE_INVALID', 'field': field})


def unresolved_extra_fields(db, cluster):
    rows = db.query(ResolutionRecord).filter(ResolutionRecord.id.in_(cluster.payload['member_record_ids'])).all()
    return [field for field in {k for r in rows for k in r.extra_fields} - RULE_FIELDS
            if field not in cluster.selections and len({digest(source_value(r, field)) for r in rows if source_value(r, field) is not None}) > 1]


def golden_command(db, run_id, request, actor, kind, cluster_id=None):
    payload = request.model_dump()
    if cluster_id:
        payload['cluster_id'] = cluster_id
    run = enabled_run(db, run_id, actor)
    prior = replay(db, run_id, kind, request.request_id, payload)
    if prior:
        return prior.result
    check_revision(run, payload)
    if run.workflow_state not in ('GOLDEN_REVIEW', 'DECIDE_REVIEW') or active_operation(db, run_id):
        conflict('GOLDEN_LOCKED')
    ids = payload.get('cluster_ids', [cluster_id])
    clusters = [cluster_row(db, run, cid) for cid in dict.fromkeys(ids)]
    for cluster in clusters:
        if kind == 'SAVE_GOLDEN':
            validate_selections(db, run, cluster, payload['selections'])
            cluster.selections = {**cluster.selections, **payload['selections']}
            cluster.confirmed = False
            cluster.payload = {**cluster.payload, 'requires_manual_review':
                [f for f in cluster.payload.get('requires_manual_review', []) if f not in payload['selections']]}
        elif kind == 'AUTO_GOLDEN':
            # Drafts were made by build_golden under the stored profile, never by new ranking.
            cluster.selections = {k: v for k, v in cluster.selections.items()
                if not payload.get('reset_manual') or k not in RULE_FIELDS}
            cluster.confirmed = False
        elif kind == 'CONFIRM_GOLDEN':
            validate_selections(db, run, cluster, cluster.selections)
            cluster.confirmed = True
            cluster.payload = {**cluster.payload, 'requires_manual_review': []}
        cluster.revision += 1
    run.workflow_revision += 1
    run.golden_revision += 1
    op = new_operation(db, run, kind, request.request_id, payload, actor)
    op.status, op.stage = 'COMPLETED', 'completed'
    op.result = {'items': [golden_read(db, run, c) for c in clusters],
                 'workflow_revision': run.workflow_revision, 'golden_revision': run.golden_revision}
    audit(db, run, actor, 'WORKFLOW_' + kind, {'cluster_ids': ids, 'golden_revision': run.golden_revision})
    db.commit()
    return op.result


def finalize_golden(db, run_id, request, actor):
    payload = request.model_dump()
    run = enabled_run(db, run_id, actor)
    prior = replay(db, run_id, 'FINALIZE_GOLDEN', request.request_id, payload)
    if prior:
        return prior.result
    check_revision(run, payload)
    if run.workflow_state != 'GOLDEN_REVIEW' or active_operation(db, run_id):
        conflict('GOLDEN_LOCKED')
    generation = current_generation(db, run)
    clusters = db.query(WorkflowCluster).filter_by(generation_id=generation.id).order_by(WorkflowCluster.id).all()
    if not clusters or any(not c.confirmed for c in clusters):
        conflict('GOLDEN_CONFIRMATIONS_REQUIRED')
    records = db.query(ResolutionRecord).filter_by(run_id=run_id).all()
    covered = [rid for c in clusters for rid in c.payload['member_record_ids']]
    if len(covered) != len(set(covered)) or set(covered) != {r.id for r in records}:
        conflict('GOLDEN_COVERAGE_INVALID')
    for c in clusters:
        validate_selections(db, run, c, c.selections)
    result = {'cluster_revision': run.cluster_revision, 'golden_revision': run.golden_revision,
              'clusters': [{**c.payload, 'golden_fields': selected_golden(db, c), 'selections': c.selections} for c in clusters],
              'records': {r.id: {'source_system': r.source_system, 'external_id': r.external_id} for r in records}}
    generation.finalized_result, generation.result_hash = result, digest(result)
    run.workflow_state = 'FINALIZED'
    run.workflow_revision += 1
    op = new_operation(db, run, 'FINALIZE_GOLDEN', request.request_id, payload, actor)
    op.status, op.stage = 'COMPLETED', 'completed'
    op.result = {'workflow_state': 'FINALIZED', 'workflow_revision': run.workflow_revision,
                 'cluster_revision': run.cluster_revision, 'golden_revision': run.golden_revision, 'result_hash': generation.result_hash}
    audit(db, run, actor, 'WORKFLOW_FINALIZE_GOLDEN', op.result)
    db.commit()
    return op.result


def reopen_decisions(db, run_id, request, actor):
    payload = request.model_dump()
    run = enabled_run(db, run_id, actor)
    prior = replay(db, run_id, 'REOPEN_DECISIONS', request.request_id, payload)
    if prior:
        return prior.result
    check_revision(run, payload)
    if run.workflow_state not in ('GOLDEN_REVIEW', 'FINALIZED') or active_operation(db, run_id):
        conflict('REOPEN_NOT_ALLOWED')
    run.workflow_state = 'DECIDE_REVIEW'
    run.workflow_revision += 1
    run.decision_revision += 1
    run.golden_revision += 1
    op = new_operation(db, run, 'REOPEN_DECISIONS', request.request_id, payload, actor)
    op.status, op.stage = 'COMPLETED', 'completed'
    op.result = get_workflow(db, run_id, actor)
    audit(db, run, actor, 'WORKFLOW_REOPEN_DECISIONS', {'reason': request.reason, 'cluster_revision': run.cluster_revision})
    db.commit()
    return op.result


def carry_choices(db, run, new_cluster, previous_clusters=None):
    """Reuse only choices with current sources; confirmation always requires review."""
    old = previous_clusters if previous_clusters is not None else db.query(WorkflowCluster).join(WorkflowGeneration).filter(WorkflowGeneration.run_id == run.id,
                    WorkflowGeneration.revision == run.cluster_revision).all()
    members = set(new_cluster.payload['member_record_ids'])
    choices = {}
    collisions = set()
    dropped = set()
    for cluster in old:
        if not members & set(cluster.payload['member_record_ids']):
            continue
        dropped.update(cluster.payload.get('requires_manual_review', []))
        for field, selection in cluster.selections.items():
            if selection['selection_mode'] == 'SOURCE_CHOICE' and selection.get('source_record_id') not in members:
                dropped.add(field)
                continue
            if selection['selection_mode'] == 'MANUAL_OVERRIDE' and members != set(cluster.payload['member_record_ids']):
                dropped.add(field)
                continue
            if field in choices and choices[field] != selection:
                collisions.add(field)
            else:
                choices[field] = deepcopy(selection)
    new_cluster.selections = {k: v for k, v in choices.items() if k not in collisions}
    if dropped or collisions:
        new_cluster.payload = {**new_cluster.payload, 'requires_manual_review': sorted(dropped | collisions)}
    new_cluster.confirmed = False


def repair_confirmation(cluster, ancestors, metadata):
    """Only repaired records may be automatically confirmed; undo restores drafts."""
    if 'repair_record_ids' not in metadata and 'auto_confirm_record_ids' not in metadata:
        return False
    members = set(cluster.payload['member_record_ids'])
    if members.intersection(metadata.get('auto_confirm_record_ids', [])):
        return not cluster.payload.get('requires_manual_review')
    if members.intersection(metadata.get('repair_record_ids', [])):
        return False
    return any(old.confirmed and members == set(old.payload['member_record_ids']) and
               old.selections == cluster.selections and old.golden == cluster.golden for old in ancestors)


def selected_clusters(db, run, payload):
    generation = current_generation(db, run)
    query = db.query(WorkflowCluster).filter_by(generation_id=generation.id)
    ids, excluded = payload.get('cluster_ids'), payload.get('excluded_cluster_ids', [])
    if ids is not None and excluded:
        raise HTTPException(422, 'Choose IDs or all with exclusions')
    available = {cid for cid, in query.with_entities(WorkflowCluster.id)}
    if not set((ids or []) + excluded) <= available:
        raise HTTPException(404, 'Cluster not found')
    scope = (available if ids is None else set(ids)) - set(excluded)
    if not scope:
        raise HTTPException(422, 'Empty cluster scope')
    return query.filter(WorkflowCluster.id.in_(scope)).order_by(WorkflowCluster.id).all()


def confirm_selected(db, run_id, request, actor):
    payload = request.model_dump()
    run = enabled_run(db, run_id, actor)
    prior = replay(db, run_id, 'CONFIRM_SELECTED', request.request_id, payload)
    if prior:
        return prior.result
    check_revision(run, payload)
    if active_operation(db, run_id) or run.workflow_state not in ('GOLDEN_REVIEW', 'DECIDE_REVIEW'):
        conflict('GOLDEN_LOCKED')
    clusters = selected_clusters(db, run, payload)
    for cluster in clusters:
        validate_selections(db, run, cluster, cluster.selections)
        cluster.confirmed = True
        cluster.payload = {**cluster.payload, 'requires_manual_review': []}
        cluster.revision += 1
    run.workflow_revision += 1
    run.golden_revision += 1
    op = new_operation(db, run, 'CONFIRM_SELECTED', request.request_id, payload, actor)
    op.status, op.stage = 'COMPLETED', 'completed'
    op.result = {'confirmed': len(clusters), 'workflow_revision': run.workflow_revision, 'golden_revision': run.golden_revision}
    audit(db, run, actor, 'WORKFLOW_CONFIRM_SELECTED', {'cluster_ids': [c.id for c in clusters]})
    db.commit()
    return op.result
