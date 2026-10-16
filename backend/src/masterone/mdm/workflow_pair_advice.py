"""Bounded, revision-fenced LLM advice. Never writes decisions, edges or eligibility."""
from uuid import uuid4
from types import SimpleNamespace
from sqlalchemy import and_, or_

from masterone.config import get_settings
from masterone.llm.gateway import PROMPT_VERSION, SCHEMA_VERSION, generate
from masterone.llm.policy import PolicyDenied, digest, fence
from masterone.mdm.models import ResolutionRecord
from masterone.mdm.pipeline.golden_v2 import record_payload
from masterone.mdm.workflow_artifacts import seal
from masterone.mdm.workflow_assessment_reads import active_assessment, effective_result
from masterone.mdm.workflow_models import WorkflowPair


def advice_route(pair):
    result = effective_result(pair)
    if pair.decision not in {'UNDECIDED', 'DEFERRED'}:
        return 'ALREADY_REVIEWED'
    if result['decision'] in {'NOT_MERGEABLE', 'BLOCKED', 'NON_MATCH'}:
        return 'HARD_CONSTRAINT'
    states = {entry['field']: entry['state'] for entry in result.get('field_evidence', [])}
    usable = {f: state for f, state in states.items() if state not in {'MI', 'IV', 'NC', 'SH'}}
    if states.get('name') not in {'EX', 'NX', 'NA', 'HS', 'MS', 'WS', 'DF', 'CF'} or len(usable) < 2:
        return 'NO_USEFUL_INFORMATION'
    model = result.get('soft_evaluation', {})
    if model.get('status') == 'EVALUATED' and model.get('recommendation') != 'INSUFFICIENT_BASIS':
        if not (result.get('primary_group') == 'D' and model['recommendation'] == 'LIKELY_SAME_PERSON'):
            return 'MODEL_CLEAR'
    if result.get('primary_group') == 'A':
        return 'RULE_CLEAR'
    if states.get('name') in {'DF', 'CF'} and not any(s in {'EX', 'NX', 'HS', 'MS'} for f, s in usable.items() if f != 'name'):
        return 'RULE_CLEAR_DIFFERENT'
    return 'ELIGIBLE'


def advice_payload(result, left, right, token):
    # Whitelist projection: raw_fields/contact/ID/DOB values and gold never enter the gateway.
    return {'pair_token': token,
        'left': {field: str(left['normalized_fields'].get(field) or '')[:300] for field in ('name', 'address')},
        'right': {field: str(right['normalized_fields'].get(field) or '')[:300] for field in ('name', 'address')},
        'signals': {entry['field']: entry['state'] for entry in result.get('field_evidence', [])
                    if entry['field'] in {'name', 'phone', 'email', 'dob', 'address', 'government_id'}},
        'unverified_identifier_relation': result.get('unverified_identifier_relation', 'NOT_COMPARABLE')}


def run_pair_advice(operation_id, factory, progress):
    from masterone.mdm.workflow_jobs import finish, job_identity
    settings = get_settings()
    with factory.begin() as db:
        run, op, _ = job_identity(db, operation_id)
        assessment = active_assessment(db, run.id)
        if assessment is None:
            raise ValueError('PAIR_ADVICE_REQUIRES_ASSESSMENT')
        run_id, assessment_id, revision = run.id, assessment.id, run.workflow_revision
        rows = db.query(WorkflowPair.id, WorkflowPair.revision, WorkflowPair.assessment, WorkflowPair.advice).filter(
            WorkflowPair.run_id == run_id).order_by(WorkflowPair.id)
        if op.payload.get('pair_ids'):
            rows = rows.filter(WorkflowPair.id.in_(op.payload['pair_ids']))
        snapshots, skipped = [], {}
        total_in_scope = rows.order_by(None).count()
        evaluated = WorkflowPair.assessment['soft_evaluation']['status'].as_string()
        recommendation = WorkflowPair.assessment['soft_evaluation']['recommendation'].as_string()
        group = WorkflowPair.assessment['primary_group'].as_string()
        rows = rows.filter(WorkflowPair.decision.in_(('UNDECIDED', 'DEFERRED')),
            WorkflowPair.assessment['decision'].as_string().notin_(('NOT_MERGEABLE', 'BLOCKED', 'NON_MATCH')),
            group.notin_(('A', 'BLOCKED')), or_(evaluated.is_(None), evaluated != 'EVALUATED',
                recommendation == 'INSUFFICIENT_BASIS', and_(group == 'D', recommendation == 'LIKELY_SAME_PERSON')))
        skipped['CLEAR_BLOCKED_OR_REVIEWED'] = total_in_scope - rows.order_by(None).count()
        limit = min(op.payload.get('limit', 100), settings.llm_max_pair_assessment_calls_per_run)
        for pid, pair_revision, result, advice in rows.yield_per(500):
            pair = SimpleNamespace(id=pid, revision=pair_revision, assessment=result, advice=advice, decision='UNDECIDED')
            route = advice_route(pair)
            if pair.advice and pair.advice.get('assessment_id') == assessment_id and pair.advice.get('status') == 'COMPLETED':
                route = 'ALREADY_ASSESSED'
            if route != 'ELIGIBLE' or len(snapshots) >= limit:
                key = route if route != 'ELIGIBLE' else 'BATCH_LIMIT'
                skipped[key] = skipped.get(key, 0) + 1
                continue
            snapshots.append({'pair_id': pair.id, 'pair_revision': pair.revision, 'result': effective_result(pair)})
        ids = {rid for p in snapshots for rid in (p['result']['left_id'], p['result']['right_id'])}
        records = {r.id: record_payload(r) for r in db.query(ResolutionRecord).filter(
            ResolutionRecord.run_id == run_id, ResolutionRecord.id.in_(ids))} if ids else {}
        context = {'run_id': run_id, 'user_id': op.actor_user_id, 'fence': fence(db, run_id),
            'input_hash': digest(records), 'review_hash': digest(snapshots),
            'reference_version': PROMPT_VERSION['PAIR_ASSESSMENT'], 'reference_hash': digest(assessment.configuration)}
    results = []
    for index, snapshot in enumerate(snapshots):
        progress('pair_advice', index, len(snapshots))
        result = snapshot['result']
        payload = advice_payload(result, records[result['left_id']], records[result['right_id']],
                                 'pair-' + digest([assessment_id, snapshot['pair_id']])[:24])
        advice = {'assessment_id': assessment_id, 'pair_revision': snapshot['pair_revision'],
                  'prompt_version': PROMPT_VERSION['PAIR_ASSESSMENT'], 'schema_version': SCHEMA_VERSION['PAIR_ASSESSMENT'],
                  'input_hash': digest(payload), 'status': 'UNAVAILABLE'}
        try:
            from masterone.mdm.workflow_telemetry import timed_stage
            with timed_stage('gemini-pair-advice'):
                response = generate('PAIR_ASSESSMENT', payload, settings, context=context, factory=factory)
            from masterone.llm.pair_advice_contract import grounded_output
            output = grounded_output(response['output'])
            advice.update(status='COMPLETED', **output, provider=response['provider'],
                          model_version=response['model_version'], request_id=response['request_id'])
        except PolicyDenied as error:
            advice['error_code'] = error.code
            if error.code not in {'PAYLOAD_DENIED', 'SCHEMA_INVALID'}:
                results.append({**snapshot, 'advice': advice})
                break  # Disabled provider, stale state or exhausted budget cannot trigger further calls.
        results.append({**snapshot, 'advice': advice})
    artifact_id = seal(factory, run_id, 'PAIR_ADVICE', {'assessment_id': assessment_id,
        'results': [{key: p[key] for key in ('pair_id', 'pair_revision', 'advice')} for p in results], 'skipped': skipped})
    with factory.begin() as db:
        run, op, task = job_identity(db, operation_id)
        active = active_assessment(db, run_id)
        if run.workflow_revision != revision or not active or active.id != assessment_id:
            raise ValueError('PAIR_ADVICE_STALE')
        for item in results:
            pair = db.get(WorkflowPair, item['pair_id'])
            if pair.revision != item['pair_revision'] or digest(effective_result(pair)) != digest(item['result']):
                raise ValueError('PAIR_ADVICE_STALE')
            pair.advice = item['advice']
        op.artifact_id, op.result = artifact_id, {'assessment_id': assessment_id, 'skipped': skipped,
            'assessed': sum(p['advice']['status'] == 'COMPLETED' for p in results),
            'unavailable': sum(p['advice']['status'] != 'COMPLETED' for p in results)}
        op.progress = {'processed': len(results), 'total': len(snapshots), 'unit': 'pairs'}
        finish(db, run, op, task, 'DECIDE_REVIEW')
