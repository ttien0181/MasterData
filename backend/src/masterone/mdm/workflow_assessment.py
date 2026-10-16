"""Fenced reassessment of existing candidate pairs without rewriting history."""
from collections import Counter
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from sqlalchemy import bindparam, update

from masterone.mdm.engine_adapter import engine_context
from masterone.mdm.generation_staging import config_identity, digest, digest_sequence
from masterone.mdm.models import ResolutionEngineSelection, ResolutionRecord
from masterone.mdm.pipeline.decision_semantics import load_cannot_links
from masterone.mdm.pipeline.golden_v2 import record_payload
from masterone.mdm.pipeline.matching import value_frequencies
from masterone.mdm.pipeline.pair_assessment import VERSION, assess_prepared_pair, assessment_profile, assessment_record
from masterone.mdm.workflow_artifacts import load, seal
from masterone.mdm.workflow_assessment_reads import active_assessment
from masterone.mdm.workflow_models import (
    WorkflowArtifact,
    WorkflowAssessment,
    WorkflowConflict,
    WorkflowOperation,
    WorkflowPair,
)


def assessment_code_identity():
    from masterone.mdm.pipeline import (
        decision_semantics,
        matching,
        pair_assessment,
        pair_grouping,
        review_recovery,
        soft_model,
        standardization,
    )
    return digest({module.__name__: sha256(Path(module.__file__).read_bytes()).hexdigest()
                   for module in (pair_assessment, pair_grouping, matching, decision_semantics, standardization, soft_model, review_recovery)})


def compute_assessment(records, pairs, profile, links, progress=lambda *_: None, model_scope=None):
    from masterone.config import get_settings
    if get_settings().ai_api_only and model_scope is not None:
        raise ValueError('API_ONLY_LOCAL_MODEL_DENIED')
    from masterone.mdm.workflow_telemetry import timed_stage
    prepared = [assessment_record(record) for record in records]
    by_id = {record['id']: record for record in prepared}
    frequencies = value_frequencies(prepared)
    results, groups = [], Counter()
    upgraded = assessment_profile(profile)
    with timed_stage('rule-assessment'), engine_context(upgraded):
        for index, pair in enumerate(pairs):
            result = assess_prepared_pair(by_id[pair['left_id']], by_id[pair['right_id']], frequencies, links)
            result['id'] = pair['id']
            result['soft_evaluation'] = {'status': 'NOT_CONFIGURED'}
            results.append(result)
            groups[result['primary_group']] += 1
            if (index + 1) % 2000 == 0 or index + 1 == len(pairs):
                progress('assess_pairs', index + 1, len(pairs))
    from masterone.config import get_settings
    from masterone.mdm.pipeline.soft_model import ReviewModelError, load_review_model
    model_state = {'status': 'OUT_OF_SCOPE' if model_scope is None else 'NOT_CONFIGURED'}
    if model_scope == 'synthetic-benchmark':
        try:
            model = load_review_model(get_settings().pair_review_model_path)
            if model:
                with timed_stage('splink-inference'):
                    predictions = model.evaluate(prepared, results, progress)
                for result in results:
                    result['soft_evaluation'] = predictions[result['id']]
                model_state = {'status': 'EVALUATED', 'scope': model.manifest['scope'],
                               'model_hash': model.model_hash, 'report_hash': model.manifest['evaluation_report_sha256']}
        except ReviewModelError as error:
            model_state = {'status': 'BLOCKED', 'reason': str(error)}
    for result in results:
        if result['soft_evaluation']['status'] == 'NOT_CONFIGURED':
            result['soft_evaluation'] = model_state
    with engine_context(upgraded):
        identity = digest(config_identity(VERSION))
    return {'pairs': results, 'records': prepared, 'input_hash': digest(prepared),
            'source_input_hash': digest(records), 'source_profile_identity': digest(profile),
            'profile': upgraded, 'profile_identity': digest(upgraded), 'policy_version': VERSION,
            'execution_identity': identity, 'negative_pairs_hash': digest(sorted(links)),
            'groups': dict(groups), 'model': model_state, 'assessment_code_identity': assessment_code_identity(),
            'contact_frequency_version': 1, 'contact_frequencies': {field: dict(counts) for field, counts in frequencies.items()}}


def _create_assessment(db, run, payload, artifact_id):
    current = active_assessment(db, run.id)
    if current:
        current.active = False
        db.flush()
    configuration = {key: value for key, value in payload.items() if key not in {'pairs', 'records', 'contact_frequencies'}}
    assessment = WorkflowAssessment(id=str(uuid4()), run_id=run.id, artifact_id=artifact_id,
                                    active=True, configuration=configuration)
    db.add(assessment)
    db.flush()
    return assessment


def publish_initial_assessment(db, run, op, pairs, payload, artifact_id):
    """Publish a fresh universe once, with baseline and assessment in each insert.

    Only DECIDE uses this path. Reassessment still preserves existing overlays and
    detects accepted conflicts through publish_assessment below.
    """
    results = {pair['id']: pair for pair in payload['pairs']}
    ids = {pair['id'] for pair in pairs}
    if ids != results.keys() or len(ids) != len(pairs) or len(results) != len(payload['pairs']):
        raise ValueError('ASSESSMENT_PAIR_UNIVERSE_CHANGED')
    if db.query(WorkflowPair.id).filter_by(run_id=run.id).first() is not None:
        raise ValueError('INITIAL_PAIR_UNIVERSE_NOT_EMPTY')
    assessment = _create_assessment(db, run, payload, artifact_id)
    for offset in range(0, len(pairs), 1000):
        db.execute(WorkflowPair.__table__.insert(), [
            {'id': pair['id'], 'run_id': run.id, 'baseline': pair,
             'assessment': results[pair['id']], 'assessment_id': assessment.id}
            for pair in pairs[offset:offset + 1000]])
    op.result = {'assessment_id': assessment.id, 'groups': payload['groups'], 'model': payload['model']}
    run.insufficient_count = sum(pair['decision'] == 'INSUFFICIENT_EVIDENCE' for pair in payload['pairs'])
    return assessment


def publish_assessment(db, run, op, payload, artifact_id):
    assessment = _create_assessment(db, run, payload, artifact_id)
    rows = dict(db.query(WorkflowPair.id, WorkflowPair.decision).filter_by(run_id=run.id))
    if set(rows) != {pair['id'] for pair in payload['pairs']}:
        raise ValueError('ASSESSMENT_PAIR_UNIVERSE_CHANGED')
    for result in payload['pairs']:
        if rows[result['id']] == 'ACCEPTED' and result['decision'] in {'NOT_MERGEABLE', 'BLOCKED', 'NON_MATCH'}:
            db.add(WorkflowConflict(run_id=run.id, operation_id=op.id, pair_id=result['id'],
                payload={'reason': 'ASSESSMENT_ACCEPTED_CONFLICT', 'record_ids': [result['left_id'], result['right_id']]}))
    table = WorkflowPair.__table__
    statement = update(table).where(table.c.id == bindparam('_pair_id'), table.c.run_id == run.id).values(
        assessment=bindparam('_assessment'), assessment_id=assessment.id, advice=None)
    for offset in range(0, len(payload['pairs']), 1000):
        db.execute(statement, [{'_pair_id': p['id'], '_assessment': p} for p in payload['pairs'][offset:offset + 1000]])
    op.result = {'assessment_id': assessment.id, 'groups': payload['groups'], 'model': payload['model']}
    run.insufficient_count = sum(pair['decision'] == 'INSUFFICIENT_EVIDENCE' for pair in payload['pairs'])
    return assessment


def run_assessment(operation_id, factory, progress):
    from masterone.mdm.workflow_jobs import finish, job_identity
    with factory.begin() as db:
        run, op, _ = job_identity(db, operation_id)
        run_id, revision, decision_revision = run.id, run.workflow_revision, run.decision_revision
        records = [record_payload(row) for row in db.query(ResolutionRecord).filter_by(run_id=run.id).order_by(ResolutionRecord.id)]
        pairs = [dict(id=pid, left_id=left, right_id=right) for pid, left, right in db.query(
            WorkflowPair.id, WorkflowPair.baseline['left_id'].as_string(), WorkflowPair.baseline['right_id'].as_string()
            ).filter(WorkflowPair.run_id == run.id).order_by(WorkflowPair.id)]
        selected = db.get(ResolutionEngineSelection, run.id)
        profile, links = selected.configuration if selected else {}, load_cannot_links(db, run.id)
        model_scope = op.payload.get('model_scope')
    payload = compute_assessment(records, pairs, profile, links, progress, model_scope)
    artifact_id = seal(factory, run_id, 'PAIR_ASSESSMENT', payload)
    agents_result = None
    with factory() as db:
        operation = db.get(WorkflowOperation, operation_id)
        if operation.payload.get('matching_binding_id'):
            originals = [row.baseline for row in db.query(WorkflowPair).filter_by(run_id=run_id).order_by(WorkflowPair.id)]
            source_artifact = db.query(WorkflowArtifact).filter_by(run_id=run_id, phase='DECIDE').order_by(WorkflowArtifact.created_at.desc()).first()
        else:
            originals, source_artifact = None, None
    if originals is not None:
        from masterone.mdm.matching_agents.workflow_agents import run_agents
        agents_result = run_agents(factory, run_id, operation_id, records, originals, {}, progress,
            source_artifact_id=source_artifact.id, effective_pairs=payload['pairs'])
        if agents_result:
            from masterone.mdm.matching_agents.workflow_agents import config_for_operation, coordinated_payload
            with factory() as db:
                from masterone.mdm.models import ResolutionRun
                config = config_for_operation(db, db.get(ResolutionRun, run_id), db.get(WorkflowOperation, operation_id))
            if config['kind'] == 'RULE_HYBRID_DETERMINISTIC':
                payload = coordinated_payload(factory, run_id, payload, agents_result, config)
                artifact_id = seal(factory, run_id, 'PAIR_ASSESSMENT', payload)
    with factory.begin() as db:
        run, op, task = job_identity(db, operation_id)
        if (run.workflow_revision, run.decision_revision) != (revision, decision_revision):
            raise ValueError('ASSESSMENT_REVISION_CHANGED')
        current = [record_payload(row) for row in db.query(ResolutionRecord).filter_by(run_id=run.id).order_by(ResolutionRecord.id)]
        if digest(current) != payload['source_input_hash'] or digest(sorted(load_cannot_links(db, run.id))) != payload['negative_pairs_hash']:
            raise ValueError('ASSESSMENT_INPUT_CHANGED')
        selected = db.get(ResolutionEngineSelection, run.id)
        if digest(selected.configuration if selected else {}) != payload['source_profile_identity'] or assessment_code_identity() != payload['assessment_code_identity']:
            raise ValueError('ASSESSMENT_CONFIG_CHANGED')
        publish_assessment(db, run, op, payload, artifact_id)
        if agents_result:
            op.result = {**op.result, 'matching_agents': agents_result}
        if op.payload.get('matching_binding_id'):
            from masterone.mdm.matching_foundation_service import append_binding_stage, latest_binding
            parent = latest_binding(db, run.id)
            append_binding_stage(db, run, op, 'ASSESSED', parent.candidate_artifact_id,
                assessment_id=op.result['assessment_id'], input_hash=payload['source_input_hash'])
        op.progress = {'processed': len(pairs), 'total': len(pairs), 'unit': 'pairs'}
        finish(db, run, op, task, 'DECIDE_REVIEW')


def assessment_snapshot(db, run, records):
    assessment = active_assessment(db, run.id)
    if assessment is None:
        return None
    payload = load(db, assessment.artifact_id, run.id, 'PAIR_ASSESSMENT')
    if digest(records) != payload['source_input_hash']:
        raise ValueError('ASSESSMENT_INPUT_CHANGED')
    return {**payload, 'assessment_id': assessment.id}


def validate_assessed_snapshot(db, run_id, snapshot):
    if not snapshot.get('assessment_id'):
        return False
    assessment = active_assessment(db, run_id)
    if assessment is None or assessment.id != snapshot['assessment_id']:
        raise ValueError('ASSESSMENT_CHANGED')
    if assessment.configuration['execution_identity'] != snapshot['execution_identity']:
        raise ValueError('ASSESSMENT_CONFIG_CHANGED')
    if assessment.configuration['assessment_code_identity'] != assessment_code_identity():
        raise ValueError('ASSESSMENT_CODE_CHANGED')
    from masterone.mdm.workflow_snapshot_producers import record_stream
    if digest_sequence(record_stream(db, run_id)) != snapshot['source_input_hash']:
        raise ValueError('ASSESSMENT_INPUT_CHANGED')
    selected = db.get(ResolutionEngineSelection, run_id)
    if digest(selected.configuration if selected else {}) != snapshot['source_profile_identity']:
        raise ValueError('WORKFLOW_PROFILE_CHANGED')
    return True
