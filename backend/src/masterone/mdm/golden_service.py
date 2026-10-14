"""Server-owned choices decorate engine drafts; survivorship stays in the engine."""
from copy import deepcopy
from datetime import UTC, datetime

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


def source_record(db, cluster, record_id, reader=None):
    if reader is not None and reader.virtual:
        return reader.record_row(record_id)
    from masterone.mdm.workflow_generation_reads import reader_for_cluster
    from masterone.mdm.workflow_models import WorkflowGeneration
    generation_id = getattr(cluster, 'generation_id', None)
    generation = db.get(WorkflowGeneration, generation_id) if generation_id else None
    if generation is not None and generation.storage_version != 'workflow_json_v1':
        return reader_for_cluster(db, cluster).record_row(record_id)
    return db.get(ResolutionRecord, record_id)


def source_records(db, cluster, reader=None):
    return [source_record(db, cluster, rid, reader) for rid in cluster.payload['member_record_ids']]


def source_value(record, field):
    if field in ('phone', 'email'):
        return record.normalized_fields.get(field) or record.raw_fields.get(field)
    return record.raw_fields.get(field, record.extra_fields.get(field))


def source_choice_hash(record, field):
    from masterone.mdm.pipeline.golden_v2 import observations, record_payload
    return digest({'value': source_value(record, field),
                   'observations': list(observations(field, [record_payload(record)])) if field in RULE_FIELDS else []})


def require_resolved_choices(cluster):
    pending = sorted(set(cluster.payload.get('requires_manual_review', []))
                     | set(cluster.payload.get('pending_selections', {})))
    if pending:
        conflict('GOLDEN_MANUAL_SELECTION_REQUIRED', fields=pending)


def selected_golden(db, cluster, reader=None):
    golden = deepcopy(cluster.golden)
    fields = golden.setdefault('fields', {})
    for field, selection in cluster.selections.items():
        previous = fields.get(field, {})
        if selection['selection_mode'] == 'SOURCE_CHOICE':
            record = source_record(db, cluster, selection['source_record_id'], reader)
            value = source_value(record, field)
            provenance = dict(source_record_id=record.id, source_system=record.source_system, provenance='SOURCE_CHOICE')
            from masterone.mdm.pipeline.golden_v2 import observations, record_payload
            observed = list(observations(field, [record_payload(record)]))
            observation = next((item for item in observed if item['value'] == value), None)
            if observation is None and field not in ('phone', 'email') and observed:
                observation = observed[0]
            observation = observation or {}
            metadata = dict(quality=observation.get('quality', 'UNKNOWN'), verified=observation.get('verified', False),
                            observed_at=observation.get('observed_at'), shared_risk=observation.get('shared_risk', False))
        else:
            value = selection['value']
            provenance = dict(source_record_id=None, source_system=None, provenance='STEWARD_OVERRIDE')
            metadata = dict(quality='STEWARD', verified=False, observed_at=None, shared_risk=False)
        chosen = value[0] if field in ('phone', 'email') and isinstance(value, list) and value else value
        fields[field] = {**previous, **provenance, **metadata, 'value': chosen,
                         'eligible_primary': True, 'excluded_reason': None}
        if field in ('phone', 'email'):
            values = deepcopy(previous.get('values', []))
            chosen_values = value if isinstance(value, list) else [value]
            selected = [{**provenance, **metadata, 'value': v, 'eligible_primary': True, 'excluded_reason': None}
                        for v in chosen_values if v]
            fields[field]['values'] = selected + [v for v in values if v['value'] not in chosen_values]
    return golden


def golden_read(db, run, cluster, *, metadata=None, reader=None):
    if reader is None:
        from masterone.mdm.workflow_generation_reads import reader_for_cluster
        reader = reader_for_cluster(db, cluster)
    golden = selected_golden(db, cluster, reader)
    rows = source_records(db, cluster, reader)
    alternatives = {}
    names = RULE_FIELDS | {k for row in rows for k in row.extra_fields}
    for field in sorted(names):
        alternatives[field] = [{'source_record_id': r.id, 'source_system': r.source_system,
            'value': source_value(r, field), 'auto_rule_available': field in RULE_FIELDS}
            for r in rows if source_value(r, field) is not None]
    from masterone.mdm.workflow_cluster_reads import generation_metadata
    metadata = metadata if metadata is not None else generation_metadata(db, run)
    return {'cluster_id': cluster.id, 'cluster_revision': run.cluster_revision,
            'read_only': metadata['read_only'], 'generation': metadata,
            'golden_revision': run.golden_revision, 'revision': cluster.revision,
            'status': 'FINALIZED' if run.workflow_state == 'FINALIZED' else 'CONFIRMED' if cluster.confirmed else 'DRAFT',
            'fields': golden.get('fields', {}), 'alternatives': alternatives,
            'selections': {field: {key: value for key, value in selection.items()
                                   if key in ('selection_mode', 'source_record_id', 'value')}
                           for field, selection in cluster.selections.items()},
            'requires_manual_review': cluster.payload.get('requires_manual_review', []),
            'pending_selections': cluster.payload.get('pending_selections', {}),
            'discarded_pending_selections': cluster.payload.get('discarded_pending_selections', []),
            'entity_id': cluster.payload.get('entity_id')}


def validate_selections(db, run, cluster, selections):
    members = set(cluster.payload['member_record_ids'])
    for field, selection in selections.items():
        if selection['selection_mode'] == 'DISCARD_PENDING':
            if (field in RULE_FIELDS or not cluster.payload.get('pending_selections', {}).get(field)
                    or not (selection.get('reason') or '').strip()
                    or selection.get('source_record_id') is not None or selection.get('value') is not None
                    or any(source_value(r, field) is not None for r in source_records(db, cluster))):
                raise HTTPException(422, {'code': 'GOLDEN_DISCARD_INVALID', 'field': field})
        elif selection['selection_mode'] == 'SOURCE_CHOICE':
            rid = selection.get('source_record_id')
            if rid not in members:
                raise HTTPException(422, {'code': 'GOLDEN_SOURCE_NOT_MEMBER', 'field': field})
            record = source_record(db, cluster, rid)
            value = source_value(record, field)
            if record.run_id != run.id or value is None or (selection.get('value') is not None and selection['value'] != value):
                raise HTTPException(422, {'code': 'GOLDEN_SOURCE_VALUE_INVALID', 'field': field})
        else:
            value = selection.get('value')
            if field not in RULE_FIELDS or not (isinstance(value, str) or field in ('phone', 'email') and isinstance(value, list) and all(isinstance(v, str) for v in value)):
                raise HTTPException(422, {'code': 'GOLDEN_OVERRIDE_INVALID', 'field': field})


def unresolved_extra_fields(db, cluster):
    rows = source_records(db, cluster)
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
    from masterone.mdm.workflow_generation_reads import GenerationReader
    reader = GenerationReader(db, run, current_generation(db, run))
    discarded = []
    for cluster in clusters:
        if kind == 'SAVE_GOLDEN':
            validate_selections(db, run, cluster, payload['selections'])
            choices = deepcopy(payload['selections'])
            history = deepcopy(cluster.payload.get('discarded_pending_selections', []))
            for field, choice in list(choices.items()):
                if choice['selection_mode'] == 'DISCARD_PENDING':
                    entry = {'field': field, 'reason': choice['reason'].strip(),
                             'actor_user_id': actor, 'created_at': datetime.now(UTC).isoformat(),
                             'candidates': deepcopy(cluster.payload['pending_selections'][field])}
                    history.append(entry)
                    discarded.append({'cluster_id': cluster.id, **entry})
                    del choices[field]
                else:
                    choice.pop('reason', None)
            for field, choice in choices.items():
                if choice['selection_mode'] == 'SOURCE_CHOICE':
                    source = source_record(db, cluster, choice['source_record_id'])
                    choice['value'] = source_value(source, field)
                    choice['source_choice_hash'] = source_choice_hash(source, field)
            cluster.selections = {**{f: v for f, v in cluster.selections.items()
                if payload['selections'].get(f, {}).get('selection_mode') != 'DISCARD_PENDING'}, **choices}
            cluster.confirmed = False
            cluster.payload = {**cluster.payload, 'requires_manual_review':
                [f for f in cluster.payload.get('requires_manual_review', []) if f not in payload['selections']],
                'pending_selections': {f: values for f, values in cluster.payload.get('pending_selections', {}).items()
                                       if f not in payload['selections']},
                'discarded_pending_selections': history}
        elif kind == 'AUTO_GOLDEN':
            # Drafts were made by build_golden under the stored profile, never by new ranking.
            cluster.selections = {k: v for k, v in cluster.selections.items()
                if not payload.get('reset_manual') or k not in RULE_FIELDS}
            cluster.confirmed = False
        elif kind == 'CONFIRM_GOLDEN':
            require_resolved_choices(cluster)
            validate_selections(db, run, cluster, cluster.selections)
            cluster.confirmed = True
            cluster.payload = {**cluster.payload, 'requires_manual_review': []}
        cluster.revision += 1
        reader.materialize(cluster)
        from masterone.mdm.workflow_entities import record_entity_version
        record_entity_version(db, run, cluster, actor=actor)
    run.workflow_revision += 1
    run.golden_revision += 1
    op = new_operation(db, run, kind, request.request_id, payload, actor)
    op.status, op.stage = 'COMPLETED', 'completed'
    from masterone.mdm.workflow_cluster_reads import generation_metadata
    metadata = generation_metadata(db, run)
    op.result = {'items': [golden_read(db, run, c, metadata=metadata) for c in clusters],
                 'workflow_revision': run.workflow_revision, 'golden_revision': run.golden_revision}
    audit(db, run, actor, 'WORKFLOW_' + kind, {'cluster_ids': ids, 'golden_revision': run.golden_revision,
                                           'discarded_pending_selections': discarded})
    db.commit()
    return op.result


def finalize_golden(db, run_id, request, actor):
    payload = request.model_dump()
    run = enabled_run(db, run_id, actor)
    prior = replay(db, run_id, 'FINALIZE_GOLDEN', request.request_id, payload)
    if prior:
        if prior.result.get('streamed_finalization'):
            from masterone.mdm.workflow_service import operation_read
            return operation_read(prior)
        return prior.result
    check_revision(run, payload)
    if run.workflow_state != 'GOLDEN_REVIEW' or active_operation(db, run_id):
        conflict('GOLDEN_LOCKED')
    generation = current_generation(db, run)
    from masterone.mdm.workflow_generation_reads import GenerationReader
    reader = GenerationReader(db, run, generation)
    if not reader.coverage()['clusters'] or reader.unconfirmed_count():
        conflict('GOLDEN_CONFIRMATIONS_REQUIRED')
    if generation.storage_version == 'workflow_parquet_v2':
        from masterone.mdm.workflow_service import enqueue, operation_read
        op = new_operation(db, run, 'FINALIZE_GOLDEN', request.request_id, payload, actor)
        op.result = {'streamed_finalization': True, 'generation_id': generation.id,
                     'expected_cluster_revision': run.cluster_revision, 'expected_golden_revision': run.golden_revision,
                     'expected_decision_revision': run.decision_revision, 'source_hash': generation.coverage['source_hash']}
        enqueue(db, run, op)
        run.workflow_revision += 1
        audit(db, run, actor, 'WORKFLOW_FINALIZE_GOLDEN_QUEUED', {'operation_id': op.id, 'generation_id': generation.id})
        db.commit()
        return operation_read(op)
    clusters = list(reader.rows())
    records = list(reader.records())
    covered = [rid for c in clusters for rid in c.payload['member_record_ids']]
    if (len(covered) != len(set(covered)) or set(covered) != {r['id'] for r in records}
            or reader.coverage()['unresolved_records']):
        conflict('GOLDEN_COVERAGE_INVALID')
    for c in clusters:
        require_resolved_choices(c)
        validate_selections(db, run, c, c.selections)
    result = {'cluster_revision': run.cluster_revision, 'golden_revision': run.golden_revision,
              'clusters': [{**c.payload, 'golden_fields': selected_golden(db, c), 'selections': c.selections} for c in clusters],
              'records': {r['id']: {'source_system': r['source_system'], 'external_id': r['external_id']} for r in records}}
    if all(c.payload.get('identity_version') == 1 and c.payload.get('entity_id') for c in clusters):
        result['master_id_version'] = 'workflow_entity_v1'
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
    """Keep current choices and preserve every ambiguous value as a review draft."""
    old = previous_clusters if previous_clusters is not None else db.query(WorkflowCluster).join(WorkflowGeneration).filter(WorkflowGeneration.run_id == run.id,
                    WorkflowGeneration.revision == run.cluster_revision).all()
    members = set(new_cluster.payload['member_record_ids'])
    choices, candidates, required = {}, {}, set()
    pending = {}
    def remember(field, candidate):
        values = pending.setdefault(field, [])
        if candidate not in values:
            values.append(deepcopy(candidate))
    for cluster in old:
        if not members & set(cluster.payload['member_record_ids']):
            continue
        required.update(cluster.payload.get('requires_manual_review', []))
        for field, values in cluster.payload.get('pending_selections', {}).items():
            for candidate in values:
                remember(field, candidate)
        for field, selection in (cluster.selections or {}).items():
            candidate = {**deepcopy(selection), 'ancestor_cluster_id': cluster.id,
                         'ancestor_entity_id': cluster.payload.get('entity_id')}
            candidates.setdefault(field, []).append(candidate)
            applies, reason = True, ''
            if selection['selection_mode'] == 'SOURCE_CHOICE':
                rid = selection.get('source_record_id')
                if rid not in members:
                    applies, reason = False, 'SOURCE_REMOVED'
                else:
                    source = source_record(db, new_cluster, rid)
                    if (source is None or source.run_id != run.id or not selection.get('source_choice_hash')
                            or selection['source_choice_hash'] != source_choice_hash(source, field)):
                        applies, reason = False, 'SOURCE_CHANGED_OR_UNPINNED'
            elif members != set(cluster.payload['member_record_ids']):
                reason = 'MEMBERSHIP_CHANGED'
                applies = (new_cluster.payload.get('entity_id') is not None
                           and new_cluster.payload['entity_id'] == cluster.payload.get('entity_id')) or (
                               cluster.id in new_cluster.payload.get('retained_ancestor_cluster_ids', []))
            if reason:
                remember(field, {**candidate, 'reason': reason})
                required.add(field)
            if applies:
                choices.setdefault(field, []).append(deepcopy(selection))
    selected = {}
    for field, values in candidates.items():
        # Different source selections are distinct even when scalar values match.
        selections = [{k: v for k, v in value.items() if k not in ('ancestor_cluster_id', 'ancestor_entity_id')}
                      for value in values]
        if len({digest(value) for value in selections}) > 1:
            for value in values:
                remember(field, {**value, 'reason': 'ANCESTOR_CHOICES_CONFLICT'})
            required.add(field)
        elif field in choices:
            selected[field] = choices[field][0]
    new_cluster.selections = selected
    new_cluster.payload = {**new_cluster.payload, 'pending_selections': pending,
                           'requires_manual_review': sorted(required | pending.keys())}
    new_cluster.confirmed = False


def repair_confirmation(cluster, ancestors, metadata):
    """Only repaired records may be automatically confirmed; undo restores drafts."""
    if cluster.payload.get('requires_manual_review') or cluster.payload.get('pending_selections'):
        return False
    if 'repair_record_ids' not in metadata and 'auto_confirm_record_ids' not in metadata:
        return False
    members = set(cluster.payload['member_record_ids'])
    if members.intersection(metadata.get('auto_confirm_record_ids', [])):
        return not cluster.payload.get('requires_manual_review')
    if members.intersection(metadata.get('repair_record_ids', [])):
        return False
    return any(old.confirmed and members == set(old.payload['member_record_ids']) and
               cluster.payload.get('source_snapshot_hash') is not None and
               old.payload.get('source_snapshot_hash') == cluster.payload['source_snapshot_hash'] and
               old.selections == cluster.selections and old.golden == cluster.golden for old in ancestors)


def selected_clusters(db, run, payload):
    from masterone.mdm.workflow_generation_reads import GenerationReader
    generation = current_generation(db, run)
    reader = GenerationReader(db, run, generation)
    ids, excluded = payload.get('cluster_ids'), payload.get('excluded_cluster_ids', [])
    if ids is not None and excluded:
        raise HTTPException(422, 'Choose IDs or all with exclusions')
    for cid in set((ids or []) + excluded):
        reader.row(cid)
    count = (reader.coverage()['clusters'] if ids is None else len(set(ids))) - len(set(excluded))
    if not count:
        raise HTTPException(422, 'Empty cluster scope')
    return list(reader.rows(payload))


def confirm_selected(db, run_id, request, actor):
    payload = request.model_dump()
    run = enabled_run(db, run_id, actor)
    prior = replay(db, run_id, 'CONFIRM_SELECTED', request.request_id, payload)
    if prior:
        return prior.result
    check_revision(run, payload)
    if active_operation(db, run_id) or run.workflow_state not in ('GOLDEN_REVIEW', 'DECIDE_REVIEW'):
        conflict('GOLDEN_LOCKED')
    from masterone.mdm.workflow_generation_reads import GenerationReader
    reader = GenerationReader(db, run, current_generation(db, run))
    default_scope = reader.virtual and payload.get('cluster_ids') is None
    if default_scope:
        excluded = set(payload.get('excluded_cluster_ids', []))
        for cid in excluded:
            reader.materialize(reader.row(cid))
        clusters = [c for c in reader.persisted_rows() if c.id not in excluded]
        confirmed_count = reader.coverage()['clusters'] - len(excluded)
        if not confirmed_count:
            raise HTTPException(422, 'Empty cluster scope')
    else:
        clusters = selected_clusters(db, run, payload)
        confirmed_count = len(clusters)
    for cluster in clusters:
        require_resolved_choices(cluster)
        validate_selections(db, run, cluster, cluster.selections)
        cluster.confirmed = True
        cluster.payload = {**cluster.payload, 'requires_manual_review': []}
        cluster.revision += 1
        reader.materialize(cluster)
        from masterone.mdm.workflow_entities import record_entity_version
        record_entity_version(db, run, cluster, actor=actor)
    if default_scope:
        reader.confirm_default(actor)
    run.workflow_revision += 1
    run.golden_revision += 1
    op = new_operation(db, run, 'CONFIRM_SELECTED', request.request_id, payload, actor)
    op.status, op.stage = 'COMPLETED', 'completed'
    op.result = {'confirmed': confirmed_count, 'workflow_revision': run.workflow_revision, 'golden_revision': run.golden_revision}
    audit(db, run, actor, 'WORKFLOW_CONFIRM_SELECTED', {'cluster_ids': [c.id for c in clusters][:100],
          'confirmed': confirmed_count, 'default_singleton_scope': default_scope})
    db.commit()
    return op.result
