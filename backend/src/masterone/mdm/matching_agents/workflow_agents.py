"""Evidence sidecar over production rule results; no candidate/routing changes."""
import time
from collections import Counter
from dataclasses import asdict

from masterone.config import get_settings
from masterone.mdm.generation_staging import digest
from masterone.mdm.matching_foundation_models import MatchingProfile
from masterone.mdm.matching_foundation_service import verify_operation_binding
from masterone.mdm.workflow_artifacts import load
from masterone.mdm.workflow_models import WorkflowArtifact, WorkflowOperation

from .contracts import EncoderCheckpointError, EncoderIntegrityError, PairKey, eligible_text
from .coordinator import join_evidence
from .embedding_agent import EmbeddingAgent
from .embedding_runtime import EncoderRuntime
from .encoder_accounting import encode_accounted


def restore_api_budget(runtime, factory, run_id):
    if not getattr(runtime, 'external', False):
        return
    with factory() as db:
        receipts = [load(db, artifact.id, run_id, 'AGENT_ENCODER_USAGE')
                    for artifact in db.query(WorkflowArtifact).filter_by(run_id=run_id, phase='AGENT_ENCODER_USAGE')]
    runtime.restore_budget(receipts)


def config_for_operation(db, run, op):
    binding = verify_operation_binding(db, run, op)
    if binding is None:
        return None
    profile = db.get(MatchingProfile, binding.profile_id)
    return profile.config_json if profile.config_json['kind'] in ('RULE_WITH_EMBEDDING_EVIDENCE', 'RULE_HYBRID_DETERMINISTIC') else None


def latest_result(db, run_id):
    op = db.query(WorkflowOperation).filter(WorkflowOperation.run_id == run_id,
        WorkflowOperation.operation_type.in_(('DECIDE', 'ASSESS_PAIRS', 'REBUILD_CANDIDATES')),
        WorkflowOperation.status.in_(('COMPLETED', 'DEGRADED'))).order_by(
        WorkflowOperation.created_at.desc(), WorkflowOperation.id.desc()).first()
    return op.result.get('matching_agents') if op else None


def seal_agent(factory, run_id, op_id, phase, payload):
    from masterone.mdm.workflow_artifacts import _partition
    from masterone.mdm.workflow_jobs import job_identity
    with factory.begin() as db:
        run, op, _ = job_identity(db, op_id)
        if run.id != run_id:
            raise ValueError('AGENT_ARTIFACT_OWNER_MISMATCH')
        stored = _partition(db, run_id, payload)
        artifact = WorkflowArtifact(run_id=run_id, phase=phase, payload=stored, content_hash=digest(stored))
        db.add(artifact)
        db.flush()
        # Last database-clock fence after artifact inserts.
        from masterone.mdm.job_queue import guard
        guard(db, run_id)
        return artifact.id


def run_agents(factory, run_id, op_id, records, pairs, scoring_identity, progress, *, source_artifact_id,
               effective_pairs=None, skip_advisory=False):
    from masterone.mdm.pipeline import matching, standardization
    from masterone.mdm.pipeline.scoring_engine import file_hash
    from masterone.mdm.workflow_jobs import job_identity
    started = time.monotonic()
    with factory.begin() as db:
        run, op, _ = job_identity(db, op_id)
        config = config_for_operation(db, run, op)
        if config is None:
            return None
        owner = run.owner_user_id
        binding = verify_operation_binding(db, run, op)
        binding_identity = {'profile_id': binding.profile_id, 'profile_digest': binding.profile_digest,
                            'binding_id': binding.id, 'binding_revision': binding.revision}
        from masterone.mdm.pipeline.decision_semantics import load_cannot_links
        skip_evidence = skip_advisory and config['kind'] == 'RULE_WITH_EMBEDDING_EVIDENCE'
        cannot_links = set() if skip_evidence else load_cannot_links(db, run_id)
    if skip_advisory and config['kind'] == 'RULE_WITH_EMBEDDING_EVIDENCE':
        warning = 'Demo fast mode: advisory embedding was not computed; rule assessment is unchanged.'
        report = {**binding_identity, 'contract_version': 'matching_agents_v1', 'status': 'SKIPPED',
            'warning': warning, 'error_code': 'DEMO_ADVISORY_EMBEDDING_SKIPPED',
            'input_hash': digest(records), 'candidate_hash': digest([
                asdict(PairKey(owner, run_id, p['left_id'], p['right_id'])) for p in pairs]),
            'rule_identity': scoring_identity, 'encoder_identity': config['encoder_identity'],
            'preprocessing_identity': digest(config['preprocessing']), 'runtime': {},
            'runtime_seconds': time.monotonic() - started, 'coverage': {
                'expected_pairs': len(pairs), 'rule_pairs': 0, 'embedding_pairs': 0},
            'field_coverage': {}, 'automation_allowed': False, 'pairs': []}
        artifact_id = seal_agent(factory, run_id, op_id, 'MATCHING_AGENTS', report)
        return {'artifact_id': artifact_id, 'status': 'SKIPPED', 'warning': warning,
                'coverage': report['coverage'], 'profile_digest': binding.profile_digest, 'automation_allowed': False}
    input_hash = digest(records)
    frequencies = {'phone': Counter(), 'email': Counter()}
    for record in records:
        matching.update_value_frequencies(frequencies, record)
    identity = dict(scoring_identity)
    identity['frequency_hash'] = digest({k: dict(v) for k, v in frequencies.items()})
    identity['normalization_hash'] = file_hash(standardization.__file__)
    identity['cannot_links_hash'] = digest(sorted(cannot_links))
    if identity.get('model_version') not in (None, 'p119_rule_v1'):
        raise ValueError('AGENT_RULE_SCORE_UNIT_UNSUPPORTED')
    keys = [PairKey(owner, run_id, p['left_id'], p['right_id']) for p in pairs]
    rules = []
    effective = {p['id']: p for p in (effective_pairs or [])}
    for key, pair in zip(keys, pairs, strict=True):
        result = effective.get(pair['id'], pair)
        rules.append({'key': key, 'status': 'OK', 'score_unit': 'p119_rule_v1',
            'rule_score': result.get('rule_score', result.get('score')),
            'baseline_decision': pair['decision'], 'assessment_decision': result['decision'],
            'original_baseline_reference': {'artifact_id': source_artifact_id, 'pair_id': pair['id'], 'hash': digest(pair)},
            'field_evidence': result.get('field_evidence', []), 'comparisons': result.get('comparisons', {}),
            'normalization_flags': result.get('normalization_tags', []),
            'hard_constraint_evidence': {k: v for k, v in result.items() if 'constraint' in k or 'conflict' in k},
            'cannot_link': (key.left_id, key.right_id) in cannot_links,
            'reason': result.get('reason'), 'group': result.get('primary_group'), 'subgroup': result.get('subgroup'),
            'trace_id': key.digest})
    cache = {}
    with factory() as db:
        for artifact in db.query(WorkflowArtifact).filter_by(run_id=run_id, phase='AGENT_VECTOR_CACHE'):
            content = load(db, artifact.id, run_id, 'AGENT_VECTOR_CACHE')
            if content['input_hash'] == input_hash and content['profile_digest'] == binding_identity['profile_digest']:
                cache.update(content['vectors'])
    persisted = set(cache)
    def flush_cache():
        new = {key: vector for key, vector in cache.items() if key not in persisted}
        if new:
            seal_agent(factory, run_id, op_id, 'AGENT_VECTOR_CACHE', {'input_hash': input_hash,
                'profile_digest': binding_identity['profile_digest'], 'vectors': new})
            persisted.update(new)
    embeddings, runtime_stats, failure = [], {}, None
    field_coverage = {}
    for field, text_field in [('name', 'name_unicode'), ('address', 'address')]:
        eligible = sum(eligible_text(row['normalized_fields'].get(text_field),
            row['normalized_fields'].get('_field_quality', {}).get(field, 'MISSING'))[1] is None for row in records)
        field_coverage[field] = {'eligible_records': eligible, 'error_records': eligible,
                                'available_records': 0, 'masked_records': len(records)-eligible,
                                'eligible_success_ratio': 0. if eligible else None}
    try:
        if not get_settings().advanced_matching_agents_enabled:
            raise ValueError('ENCODER_FEATURE_DISABLED')
        with EncoderRuntime(config['encoder_identity'], config['resource_budget'], progress) as runtime:
            restore_api_budget(runtime, factory, run_id)
            def encode(texts, field):
                flush_cache()
                return encode_accounted(runtime, texts, field, lambda receipt: seal_agent(
                    factory, run_id, op_id, 'AGENT_ENCODER_USAGE', {**receipt, 'position': 'evidence'}))
            agent = EmbeddingAgent(encode, encoder_digest=config['encoder_identity']['bundle_digest'],
                preprocessing_digest=digest(config['preprocessing']), dimension=config['encoder_identity']['dimension'],
                runtime_digest=digest(config['encoder_identity']),
                batch_size=config['resource_budget']['batch_size'], cache=cache,
                cache_version=config['preprocessing'].get('cache_version', 'record_vectors_v1'),
                on_batch_stored=flush_cache)
            embeddings = agent.evaluate(records, keys, owner_id=owner, run_id=run_id)
            field_coverage = agent.last_coverage
            flush_cache()
            runtime_stats = {'encoder_peak_rss_bytes': runtime.peak_rss, 'encoder_batch_calls': runtime.calls,
                             'encoder_batch_retries': runtime.retry_count,
                             'encoder_batch_attempts': runtime.batch_attempts, 'encoder_usage': runtime.usage,
                             'field_coverage': agent.last_coverage}
    except Exception as error:
        # Lease/security/integrity failures must not be relabeled as degraded model failures.
        from masterone.mdm.job_queue import OwnershipLostError
        if isinstance(error, (OwnershipLostError, EncoderIntegrityError, EncoderCheckpointError)) or 'IDENTITY' in str(error) or 'MISMATCH' in str(error):
            raise
        failure = str(error) if str(error).startswith('EMBEDDING_') else 'ENCODER_' + type(error).__name__.upper()
        if config['fallback_mode'] == 'fail_operation':
            raise ValueError(failure) from error
    joined = join_evidence(keys, rules, embeddings, expected_encoder_digest=config['encoder_identity']['bundle_digest'],
        expected_preprocessing_digest=digest(config['preprocessing']), fallback_mode=config['fallback_mode'],
        snapshot_coverage=field_coverage, copy_evidence=False)
    if failure:
        joined['status'] = 'DEGRADED'
    for row in joined['pairs']:
        row['key'] = asdict(row['key'])
        row['rule'].pop('key')
        if row['embedding']:
            row['embedding'].pop('key')
            row['embedding']['fields'] = {k: asdict(v) for k, v in row['embedding']['fields'].items()}
    report = {**joined, **binding_identity, 'contract_version': 'matching_agents_v1',
        'input_hash': input_hash, 'candidate_hash': digest([asdict(key) for key in keys]),
        'rule_identity': identity, 'full_snapshot_contact_frequencies': {k: dict(v) for k, v in frequencies.items()},
        'encoder_identity': config['encoder_identity'],
        'preprocessing_identity': digest(config['preprocessing']), 'runtime': runtime_stats,
        'runtime_seconds': time.monotonic()-started, 'error_code': failure,
        'warning': 'Embedding coverage incomplete; automation disabled.' if joined['status'] == 'DEGRADED' else None}
    artifact_id = seal_agent(factory, run_id, op_id, 'MATCHING_AGENTS', report)
    return {'artifact_id': artifact_id, 'status': report['status'], 'warning': report['warning'],
            'coverage': report['coverage'], 'profile_digest': report['profile_digest'], 'automation_allowed': False}


def vectors_for_operation(factory, run_id, op_id, records, progress):
    """Encode snapshot before retrieval; incomplete vectors fail the candidate operation."""
    from masterone.mdm.workflow_jobs import job_identity

    from .contracts import FieldSimilarity
    with factory.begin() as db:
        run, op, _ = job_identity(db, op_id)
        config = config_for_operation(db, run, op)
        if config is None or config['kind'] != 'RULE_HYBRID_DETERMINISTIC':
            raise ValueError('RETRIEVAL_PROFILE_IDENTITY_MISMATCH')
        owner = run.owner_user_id
        binding = verify_operation_binding(db, run, op)
        profile_digest = binding.profile_digest
    if not get_settings().advanced_matching_integration_enabled or not get_settings().advanced_matching_agents_enabled:
        raise ValueError('RETRIEVAL_FEATURE_DISABLED')
    input_hash, cache = digest(records), {}
    with factory() as db:
        for artifact in db.query(WorkflowArtifact).filter_by(run_id=run_id, phase='AGENT_VECTOR_CACHE'):
            payload = load(db, artifact.id, run_id, 'AGENT_VECTOR_CACHE')
            if payload['input_hash'] == input_hash and payload['profile_digest'] == profile_digest:
                cache.update(payload['vectors'])
    persisted = set(cache)
    def flush():
        pending = {k: v for k, v in cache.items() if k not in persisted}
        if pending:
            seal_agent(factory, run_id, op_id, 'AGENT_VECTOR_CACHE',
                       {'input_hash': input_hash, 'profile_digest': profile_digest, 'vectors': pending})
            persisted.update(pending)
    with EncoderRuntime(config['encoder_identity'], config['resource_budget'], progress) as runtime:
        restore_api_budget(runtime, factory, run_id)
        def encode(texts, field):
            flush()
            return encode_accounted(runtime, texts, field, lambda receipt: seal_agent(
                factory, run_id, op_id, 'AGENT_ENCODER_USAGE', {**receipt, 'position': 'retrieval'}))
        agent = EmbeddingAgent(encode, encoder_digest=config['encoder_identity']['bundle_digest'],
            preprocessing_digest=digest(config['preprocessing']), dimension=config['encoder_identity']['dimension'],
            runtime_digest=digest(config['encoder_identity']), batch_size=config['resource_budget']['batch_size'], cache=cache,
            cache_version=config['preprocessing'].get('cache_version', 'record_vectors_v1'), on_batch_stored=flush)
        agent.evaluate(records, [], owner_id=owner, run_id=run_id)
        if any(field['error_records'] for field in agent.last_coverage.values()):
            raise ValueError('RETRIEVAL_VECTOR_COVERAGE_INCOMPLETE')
        flush()
        seal_agent(factory, run_id, op_id, 'AGENT_RETRIEVAL_USAGE',
                   {'input_hash': input_hash, 'profile_digest': profile_digest,
                    'encoder_batch_calls': runtime.calls, 'encoder_batch_attempts': runtime.batch_attempts,
                    'encoder_batch_retries': runtime.retry_count, 'encoder_usage': runtime.usage,
                    'encoder_peak_rss_bytes': runtime.peak_rss, 'field_coverage': agent.last_coverage,
                    'external_requests': sum(row.get('external_requests', 0) for row in runtime.usage.values()),
                    'api_cost_usd': (sum(row.get('reported_cost_usd', 0) for row in runtime.usage.values())
                                     if not any(row.get('unknown_cost_requests') for row in runtime.usage.values()) else None)})
    return {field: {'vectors': {rid: value for rid, value in values.items() if not isinstance(value, FieldSimilarity)},
                    'encoder_digest': config['encoder_identity']['bundle_digest'],
                    'preprocessing_digest': digest(config['preprocessing'])}
            for field, values in agent.last_vectors.items()}


def coordinated_payload(factory, run_id, payload, agents_result, config):
    from masterone.mdm.pipeline.decision_semantics import load_cannot_links, person_constraints
    from masterone.mdm.workflow_artifacts import iter_list
    from masterone.mdm.workflow_models import WorkflowPair

    from .decision_coordinator import coordinate_assessment
    records = {r['id']: r for r in payload['records']}
    evidence = []
    with factory() as db:
        links = load_cannot_links(db, run_id)
        overlays = dict(db.query(WorkflowPair.id, WorkflowPair.decision).filter_by(run_id=run_id))
        for row in iter_list(db, agents_result['artifact_id'], run_id, 'MATCHING_AGENTS', ['pairs']):
            key = row['key']
            hard = person_constraints(records[key['left_id']], records[key['right_id']], links)
            evidence.append({'pair_id': row['rule']['original_baseline_reference']['pair_id'],
                             'fields': row['embedding']['fields'] if row['embedding'] else {},
                             'constraints': {'codes': list(hard.violations), 'cannot_link': hard.cannot_link}})
    return coordinate_assessment(payload, {'pairs': evidence}, overlays, config)
