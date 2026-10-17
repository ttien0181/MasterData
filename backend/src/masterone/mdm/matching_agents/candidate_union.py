"""Production candidate passes plus approved fallback/retrieval; no silent pruning."""
import json
from collections import defaultdict
from hashlib import sha256
from itertools import combinations

from masterone.mdm.pipeline.candidate_engine import (
    CandidateConfig,
    CandidateEngine,
    CandidateResult,
    CandidateWorkloadExceededError,
)
from masterone.mdm.pipeline.candidate_planner import WorkloadPolicy
from masterone.mdm.pipeline.tfidf_blocking import GlobalTfidfIndex, SparseTfidfConfig

from .contracts import eligible_text
from .retrieval import RetrievalBudget, retrieve_vectors


def _person_recovery(records, emit, meter):
    """Bounded review candidates, invariant to name order and omitted tokens.

    Full DOB + one token recovers shortened names. Two tokens + month/day
    allows a corrupt year into review; it does not override DOB merge guards.
    Policy v1 uses the existing composite block ceiling of 30. Oversized
    blocks fail closed, and unusually long names cannot create quadratic keys.
    """
    from masterone.mdm.pipeline.person_blocking import person_recovery_keys

    blocks = defaultdict(set)
    counts = defaultdict(int)
    workspace = 0
    excluded = 0

    def add(key, rid):
        nonlocal workspace
        # Conservative incremental estimate includes postings, keys and final logs.
        # Keep at most 30 IDs: once oversized, only its exact count is needed.
        extra = (1024 + sum(len(str(p).encode('utf8')) * 4 for p in key)) if key not in counts else 0
        if counts.get(key, 0) < 30:
            extra += 256
        meter.reserve(workspace=workspace + extra)
        workspace += extra
        counts[key] += 1
        if counts[key] <= 30:
            blocks[key].add(rid)
        elif counts[key] == 31:
            blocks[key].clear()

    for row in records:
        meter.reserve(workspace=workspace)
        keys = list(person_recovery_keys(row))
        if not keys:
            excluded += 1
            continue
        for source, values in keys:
            add((source, *values), row['id'])
    logs = []
    observation_bytes = 0
    for key, size in sorted(counts.items()):
        meter.reserve(workspace=workspace + observation_bytes)
        ids = blocks[key]
        eligible = 2 <= size <= 30
        pin = sha256(json.dumps(key).encode()).hexdigest()
        logs.append({'source': key[0], 'block_key_sha256': pin, 'block_size': size,
                     'reason': 'max_block_size' if size > 30 else 'eligible' if eligible else 'singleton'})
        if eligible:
            for left, right in combinations(sorted(ids), 2):
                # Account for provenance even when another pass already found the pair.
                observation_bytes += 1024
                meter.reserve(workspace=workspace + observation_bytes)
                emit(key[0], left, right, {'block_key_sha256': pin})
    return {'version': 'person_name_dob_v1', 'max_block_size': 30, 'max_name_tokens': 12,
            'excluded_records': excluded, 'oversized_blocks': sum(size > 30 for size in counts.values()),
            'estimated_workspace_bytes': workspace + observation_bytes,
            'blocks': logs}


def build_union(records: list[dict], config: dict, field_vectors: dict | None,
                retained_pairs: list[dict], progress) -> CandidateResult:
    experiment = config['experiment']
    if experiment not in ('E0', 'E1', 'E2', 'E3'):
        raise ValueError('UNAPPROVED_EXPERIMENT')
    meter = RetrievalBudget(config)
    # Enforce the pinned union limit while building the rule set, before
    # allocating per-pair provenance. The old engine default allowed 50M pairs.
    engine = CandidateEngine(CandidateConfig(max_unique_pairs=config['execution_budget']['unique_pairs']))
    try:
        legacy = engine.generate(records) if experiment != 'E2' else None
    except CandidateWorkloadExceededError as error:
        raise ValueError('CANDIDATE_BUDGET_EXCEEDED') from error
    if legacy is None:
        engine.add_records(records)
    pairs, observations = defaultdict(set), []
    passes, pass_keys = {}, defaultdict(set)

    def emit(source, left, right, observation):
        if left == right:
            return
        key = tuple(sorted((left, right)))
        fresh = key not in pairs
        pairs[key].add(source)
        pass_keys[source].add(key)
        stats = passes.setdefault(source, {'observations': 0, 'directed_observations': 0, 'new_unique': 0})
        stats['observations'] += 1
        stats['directed_observations'] += 'query_id' in observation
        stats['new_unique'] += fresh
        observations.append({'pass': source, 'left_id': key[0], 'right_id': key[1], **observation})
        if len(pairs) > config['execution_budget']['unique_pairs']:
            raise ValueError('CANDIDATE_BUDGET_EXCEEDED')
        meter.reserve()

    memberships = defaultdict(set)
    blocks = {}
    oversized = set()
    logs = {(log['source'], log['block_key_sha256']): log for log in legacy.block_log} if legacy else {}
    for (source, value), ids in engine.blocks.items():
        pin = sha256(json.dumps(value).encode()).hexdigest()
        key = source + ':' + pin
        blocks[key] = {'source': source, 'block_key_sha256': pin}
        if logs.get((source, pin), {}).get('reason') in ('max_block_size', 'max_pairs_per_block'):
            oversized.add(key)
        for rid in ids:
            memberships[rid].add(key)
    if legacy:
        for (left, right), sources in legacy.pairs.items():
            common = memberships[left] & memberships[right]
            for source in sorted(sources):
                emit(source, left, right, {'block_origins': [blocks[b] for b in sorted(common)
                    if blocks[b]['source'] == source and b not in oversized]})

    recovery = None
    if config['retrieval'].get('person_recovery', False) and experiment != 'E2':
        recovery = _person_recovery(records, emit, meter)
        progress('person_recovery', len(records), len(records))
    fallback = {'oversized_blocks': len(oversized), 'fields': {}}
    if experiment in ('E1', 'E3') and oversized:
        for field in ('name', 'address'):
            projected = []
            available = 0
            for row in records:
                normalized = row['normalized_fields']
                text, missing = eligible_text(normalized.get(field), normalized.get('_field_quality', {}).get(field, 'MISSING'))
                available += missing is None
                projected.append({**row, 'normalized_fields': {field: text or ''}})
            state = {'available_records': available, 'unavailable_reason': 'NO_ELIGIBLE_TEXT' if not available else None}
            fallback['fields'][field] = state
            if not available:
                continue
            cfg = SparseTfidfConfig(fields=(field,), **config['retrieval']['tfidf'],
                                   query_batch_size=config['retrieval']['query_batch_size'],
                                   target_batch_size=config['retrieval']['target_batch_size'])
            policy = WorkloadPolicy(memory_budget_bytes=config['execution_budget']['workspace_bytes'],
                                    max_similarity_cells=config['execution_budget']['similarity_cells'])
            index = GlobalTfidfIndex(projected, field=field, config=cfg, policy=policy)
            meter.reserve(cells=len(index.documents)**2, workspace=index.memory_estimate)
            if index.matrix is None:
                state['unavailable_reason'] = 'EMPTY_VOCABULARY'
            for left, right, score in index.search():
                common = sorted(memberships[left] & memberships[right] & oversized)
                if common:
                    emit('tfidf_fallback_' + field, left, right,
                         {'query_id': left, 'target_id': right, 'field': field, 'similarity': score,
                          'fallback_blocks': [blocks[b] for b in common], 'index_digest': index.snapshot_digest})
            state['index'] = index.metrics
            progress('tfidf_fallback_' + field, available, available)
    embedding = None
    if experiment in ('E1', 'E3') and config['retrieval'].get('global_lexical', False):
        # A recovery pass independent of DOB/province and oversized-block
        # membership. Entity types are separate corpora before top-K, so other
        # types cannot consume a query's neighbor budget. Existing rule pairs
        # and all provenance remain intact; no score authorizes a merge.
        partitions = defaultdict(list)
        for row in records:
            entity_type = row.get('entity_type', 'PERSON')
            if entity_type not in ('PERSON', 'ORGANIZATION'):
                raise ValueError('CANDIDATE_ENTITY_TYPE_INVALID')
            partitions[entity_type].append(row)
        for entity_type, rows in sorted(partitions.items()):
            for field in ('name', 'address'):
                projected = []
                for row in rows:
                    normalized = row['normalized_fields']
                    text, _ = eligible_text(normalized.get(field),
                        normalized.get('_field_quality', {}).get(field, 'MISSING'))
                    projected.append({'id': row['id'], 'normalized_fields': {field: text or ''}})
                cfg = SparseTfidfConfig(fields=(field,), **config['retrieval']['tfidf'],
                    query_batch_size=config['retrieval']['query_batch_size'],
                    target_batch_size=config['retrieval']['target_batch_size'])
                policy = WorkloadPolicy(memory_budget_bytes=config['execution_budget']['workspace_bytes'],
                    max_similarity_cells=config['execution_budget']['similarity_cells'])
                index = GlobalTfidfIndex(projected, field=field, config=cfg, policy=policy)
                meter.reserve(cells=len(index.documents)**2, workspace=index.memory_estimate)
                for left, right, score in index.search():
                    emit('tfidf_global_' + field, left, right,
                        {'query_id': left, 'target_id': right, 'field': field, 'entity_type': entity_type,
                         'similarity': score, 'index_digest': index.snapshot_digest})
                progress('tfidf_global_' + field, len(rows), len(rows))
    if experiment in ('E2', 'E3'):
        if field_vectors is None:
            raise ValueError('RETRIEVAL_VECTOR_COVERAGE_INCOMPLETE')
        embedding = retrieve_vectors(records, field_vectors, config, progress, budget=meter)
        for observation in embedding['observations']:
            emit(observation['pass'], observation['left_id'], observation['right_id'], observation)
    retrieved = len(pairs)
    universe = {r['id'] for r in records}
    for pair in retained_pairs:
        if pair['left_id'] not in universe or pair['right_id'] not in universe:
            raise ValueError('RETAINED_PAIR_ENDPOINT_MISSING')
        emit('retained_history', pair['left_id'], pair['right_id'], {'historical_pair_id': pair['id']})
    legacy_order = sorted({s for reasons in (legacy.pairs.values() if legacy else []) for s in reasons})
    order = legacy_order + [s for s in ('person_token_full_dob', 'person_tokens_month_day', 'tfidf_fallback_name', 'tfidf_fallback_address', 'tfidf_global_name', 'tfidf_global_address', 'embedding_name', 'embedding_address', 'retained_history') if s in passes]
    order = list(dict.fromkeys(order))
    seen = set()
    for source in order:
        passes[source]['new_unique'] = len(pass_keys[source] - seen)
        seen.update(pass_keys[source])
    for source, stats in passes.items():
        stats['unique_pairs'] = len(pass_keys[source])
    metrics = {'contract_version': 'candidate_union_v1', 'record_count': len(records),
               'candidate_count': len(pairs), 'unique_candidates': len(pairs), 'retrieved_current': retrieved,
               'retained_history': len(retained_pairs), 'passes': passes, 'pass_order': order, 'provenance': observations,
               'fallback': fallback, 'embedding': {k: v for k, v in (embedding or {}).items() if k != 'observations'},
               'similarity_cells': meter.cells, 'legacy': legacy.metrics if legacy else None,
               'warning': 'CANDIDATE_PAIR_WARNING' if len(pairs) >= config['execution_budget']['warning_pairs'] else None}
    if recovery is not None:
        metrics['person_recovery'] = recovery
    return CandidateResult(dict(sorted(pairs.items())), metrics, legacy.block_log if legacy else [])
