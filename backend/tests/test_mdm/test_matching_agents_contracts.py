"""Hand calculations, identity safety and equivalence to the production rule scorer."""
import math
from copy import deepcopy

import pytest
from masterone.mdm.matching_agents.contracts import FieldSimilarity, PairKey, cosine, eligible_text
from masterone.mdm.matching_agents.coordinator import join_evidence
from masterone.mdm.matching_agents.rule_agent import adapt_rule
from masterone.mdm.pipeline.scoring_engine import MemorySnapshot, ScoringEngine
from masterone.mdm.pipeline.standardization import standardize


@pytest.mark.parametrize('a,b,value', [([1., 0.], [1., 0.], 1.), ([1., 0.], [0., 1.], 0.),
                                      ([1., 0.], [-1., 0.], -1.), ([1e300, 0.], [1e300, 0.], 1.)])
def test_cosine_hand_calculations(a, b, value):
    assert cosine(a, b, 2) == FieldSimilarity('OK', value)


@pytest.mark.parametrize('a,b,reason', [([0., 0.], [1., 0.], 'ZERO_VECTOR'),
    ([math.nan, 0.], [1., 0.], 'NONFINITE_OR_INVALID_VECTOR'),
    ([math.inf, 0.], [1., 0.], 'NONFINITE_OR_INVALID_VECTOR'),
    ([1.], [1., 0.], 'DIMENSION_MISMATCH')])
def test_invalid_vectors_are_errors_not_zero(a, b, reason):
    assert cosine(a, b, 2) == FieldSimilarity('ERROR', None, reason)


@pytest.mark.parametrize('value,quality', [(None, 'VALID'), ('', 'VALID'), ('Unknown', 'PLACEHOLDER'),
                                         ('shared', 'SHARED'), ('invalid', 'INVALID')])
def test_masked_text_not_encoded(value, quality):
    text, score = eligible_text(value, quality)
    assert text is None and score.status == 'NOT_AVAILABLE' and score.cosine is None


def test_canonical_scope_and_unambiguous_digest():
    key = PairKey.canonical('owner', 'run', 'b', 'a')
    assert key == PairKey('owner', 'run', 'a', 'b')
    assert key.digest != PairKey('other', 'run', 'a', 'b').digest
    assert PairKey('o', 'r', 'a:b', 'c').digest != PairKey('o', 'r', 'a', 'b:c').digest
    with pytest.raises(ValueError, match='NOT_CANONICAL'):
        PairKey.canonical('owner', 'run', 'a', 'a')


def test_production_rule_adapter_output_and_input_equivalence():
    records = []
    for rid in ('a', 'b'):
        raw = {'name': 'Nguyễn Văn An', 'phone': '0901234567', 'email': 'an@example.test', 'address': 'Hà Nội'}
        fields, flags = standardize(raw)
        records.append({'id': rid, 'source_system': 'CRM', 'entity_type': 'PERSON',
                        'raw_fields': raw, 'normalized_fields': fields, 'quality_flags': flags})
    engine = ScoringEngine(MemorySnapshot(records))
    scored = engine.score_batch([{'left_id': 'a', 'right_id': 'b', 'reasons': ['exact_phone']}])[0]
    baseline = engine.decide(scored, engine.snapshot.get_records({'a', 'b'}))
    before = deepcopy((baseline, records, scored))
    adapted = adapt_rule(engine, scored, baseline, owner_id='o', run_id='r')
    assert adapted['original_baseline'] == baseline
    assert adapted['rule_score'] == scored.rule_score
    assert adapted['score_unit'] == 'p119_rule_v1'
    assert adapted['normalization_hash'] and adapted['identity']['frequency_hash']
    assert adapted['frequency_evidence']['a']['phone']['0901234567'] == 2
    assert (baseline, records, scored) == before
    adapted['original_baseline']['decision'] = 'changed'
    assert baseline == before[0]


def test_join_task_order_missing_coverage_and_degraded():
    keys = [PairKey('o', 'r', 'a', 'b'), PairKey('o', 'r', 'a', 'c')]
    rules = [{'key': key, 'status': 'OK'} for key in keys]
    embeddings = [{'key': key, 'encoder_digest': 'm', 'preprocessing_digest': 'p',
                  'fields': {'name': FieldSimilarity('OK', 0.),
                  'address': FieldSimilarity('NOT_AVAILABLE', None, 'MISSING')}} for key in reversed(keys)]
    def join(rows, **kwargs):
        return join_evidence(keys, rules, rows, expected_encoder_digest='m',
                             expected_preprocessing_digest='p', **kwargs)
    assert [row['key'] for row in join(embeddings)['pairs']] == keys
    with pytest.raises(ValueError, match='EMBEDDING_COVERAGE_FAILED'):
        join(embeddings[:1])
    assert join(embeddings[:1], fallback_mode='rule_only')['status'] == 'DEGRADED'
    assert not join(embeddings[:1], fallback_mode='rule_only')['automation_allowed']
    with pytest.raises(ValueError, match='DUPLICATE_AGENT_PAIR'):
        join(embeddings + embeddings[:1])


def test_join_rejects_encoder_identity_mismatch_even_in_rule_only_fallback():
    key = PairKey('o', 'r', 'a', 'b')
    embedding = {'key': key, 'encoder_digest': 'different-model', 'preprocessing_digest': 'p',
                 'fields': {'name': FieldSimilarity('OK', 1.), 'address': FieldSimilarity('OK', 1.)}}
    with pytest.raises(ValueError, match='EMBEDDING_IDENTITY_MISMATCH'):
        join_evidence([key], [{'key': key, 'status': 'OK'}], [embedding],
                      expected_encoder_digest='m', expected_preprocessing_digest='p', fallback_mode='rule_only')


def test_embedding_batch_dedup_preserves_all_record_ids_and_masks():
    from masterone.mdm.matching_agents.embedding_agent import EmbeddingAgent
    calls = []
    def encode(texts, field):
        calls.append((texts, field))
        return [[1., 0.] for _ in texts]
    agent = EmbeddingAgent(encode, encoder_digest='m1', preprocessing_digest='p1', dimension=2, batch_size=1)
    rows = [{'id': rid, 'normalized_fields': {'name_unicode': name, 'address': None,
             '_field_quality': {'name': quality, 'address': 'MISSING'}}}
            for rid, name, quality in [('a', 'Nguyễn An', 'VALID'), ('b', 'Nguyễn An', 'VALID'),
                                      ('c', 'Unknown', 'PLACEHOLDER')]]
    keys = [PairKey('o', 'r', 'a', 'b'), PairKey('o', 'r', 'a', 'c')]
    evidence = agent.evaluate(rows, keys)
    assert calls == [(['Nguyễn An'], 'name')]
    assert evidence[0]['fields']['name'] == FieldSimilarity('OK', 1.)
    assert evidence[0]['fields']['address'].status == 'NOT_AVAILABLE'
    assert evidence[1]['fields']['name'].status == 'NOT_AVAILABLE'
    assert agent.last_coverage['name']['eligible_records'] == 2
    assert agent.last_coverage['name']['unique_texts'] == 1


def test_embedding_wrong_batch_count_and_invalid_vector_are_error():
    from masterone.mdm.matching_agents.embedding_agent import EmbeddingAgent
    rows = [{'id': rid, 'normalized_fields': {'name_unicode': rid, 'address': '',
             '_field_quality': {'name': 'VALID', 'address': 'MISSING'}}} for rid in ('a', 'b')]
    key = PairKey('o', 'r', 'a', 'b')
    agent = EmbeddingAgent(lambda texts, field: [], encoder_digest='m', preprocessing_digest='p', dimension=2)
    assert agent.evaluate(rows, [key])[0]['fields']['name'].status == 'ERROR'
    agent = EmbeddingAgent(lambda texts, field: [[math.nan, 0.] for _ in texts],
                           encoder_digest='m', preprocessing_digest='p', dimension=2)
    assert agent.evaluate(rows, [key])[0]['fields']['name'].status == 'ERROR'


def test_embedding_cache_identity_and_changed_snapshot_never_reuse_vectors():
    from masterone.mdm.matching_agents.embedding_agent import EmbeddingAgent
    calls, cache = [], {}
    def encode(texts, field):
        calls.extend(texts)
        return [[1., 0.] for _ in texts]
    def run(model, text, owner='o'):
        rows = [{'id': rid, 'normalized_fields': {'name_unicode': text, 'address': None,
                 '_field_quality': {'name': 'VALID', 'address': 'MISSING'}}} for rid in ('a', 'b')]
        agent = EmbeddingAgent(encode, encoder_digest=model, preprocessing_digest='p', dimension=2, cache=cache)
        return agent.evaluate(rows, [PairKey(owner, 'r', 'a', 'b')])
    run('m1', 'An')
    run('m1', 'An')
    assert calls == ['An']
    run('m2', 'An')
    run('m1', 'Bình')
    run('m1', 'An', 'other')
    assert calls == ['An', 'An', 'Bình', 'An']


def test_embedding_missing_endpoint_and_corrupt_cache_fail_explicitly():
    from masterone.mdm.matching_agents.embedding_agent import EmbeddingAgent
    rows = [{'id': rid, 'normalized_fields': {'name_unicode': 'An', 'address': None,
             '_field_quality': {'name': 'VALID', 'address': 'MISSING'}}} for rid in ('a', 'b')]
    cache = {}
    agent = EmbeddingAgent(lambda texts, field: [[1., 0.] for _ in texts],
                           encoder_digest='m', preprocessing_digest='p', dimension=2, cache=cache)
    key = PairKey('o', 'r', 'a', 'b')
    with pytest.raises(ValueError, match='ENDPOINT_MISSING'):
        agent.evaluate(rows[:1], [key])
    agent.evaluate(rows, [key])
    for identity in cache:
        cache[identity] = (0., 0.)
    assert agent.evaluate(rows, [key])[0]['fields']['name'] == FieldSimilarity('ERROR', None, 'CACHE_VECTOR_INVALID')


def test_trial_progress_windows_reader_sharing_retry(monkeypatch, tmp_path):
    import importlib.util
    import json
    from pathlib import Path
    source = Path(__file__).resolve().parents[3] / 'scripts/run_m2a_encoder_trial.py'
    spec = importlib.util.spec_from_file_location('encoder_trial', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    replace, attempts = Path.replace, []
    def sharing_conflict(path, target):
        attempts.append(path)
        if len(attempts) < 3:
            raise PermissionError('Windows reader has not released progress file')
        return replace(path, target)
    monkeypatch.setattr(Path, 'replace', sharing_conflict)
    monkeypatch.setattr(module.time, 'sleep', lambda _: None)
    module.write(tmp_path / 'progress.json', {'phase': 'batch'})
    assert len(attempts) == 3
    assert json.loads((tmp_path / 'progress.json').read_text(encoding='utf-8')) == {'phase': 'batch'}


def test_trial_progress_read_sharing_retains_last_checkpoint(monkeypatch, tmp_path):
    import importlib.util
    from pathlib import Path
    source = Path(__file__).resolve().parents[3] / 'scripts/run_m2a_encoder_trial.py'
    spec = importlib.util.spec_from_file_location('encoder_trial', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    def denied(*args, **kwargs):
        raise PermissionError('Transient Windows rename sharing')
    monkeypatch.setattr(Path, 'read_text', denied)
    assert module.read_progress(tmp_path / 'progress.json') is None


def test_encoder_rpc_retry_is_bounded_and_uses_same_pins(monkeypatch):
    import io
    from types import SimpleNamespace

    from masterone.mdm.matching_agents.encoder_runtime import BUDGET, EncoderRuntime
    runtime = EncoderRuntime({'revision': 'pinned'}, BUDGET)
    runtime.child = SimpleNamespace(stdin=io.StringIO())
    replies, boots = [], []
    def receive(seconds):
        replies.append(seconds)
        if len(replies) == 1:
            raise ValueError('ENCODER_TIMEOUT')
        return {'request_id': 1, 'field': 'name', 'vectors': [[1., 0.]], 'input_tokens': 4}
    def start(self):
        boots.append(self.identity.copy())
        self.child = SimpleNamespace(stdin=io.StringIO())
        return self
    monkeypatch.setattr(runtime, '_receive', receive)
    monkeypatch.setattr(runtime, 'close', lambda: None)
    monkeypatch.setattr(EncoderRuntime, '__enter__', start)
    assert runtime.encode(['An'], 'name') == [[1., 0.]]
    assert boots == [{'revision': 'pinned'}]
    assert runtime.retry_count == 1
    assert runtime.batch_attempts == 2
    assert runtime.usage['name']['input_tokens'] == 4


def test_cache_runtime_identity_prevents_cross_backend_reuse():
    from masterone.mdm.matching_agents.embedding_agent import EmbeddingAgent
    cache, calls = {}, []
    def encode(texts, field):
        calls.append(field)
        return [[1., 0.] for _ in texts]
    rows = [{'id': rid, 'normalized_fields': {'name_unicode': 'An', 'address': None,
             '_field_quality': {'name': 'VALID', 'address': 'MISSING'}}} for rid in ('a', 'b')]
    key = PairKey('o', 'r', 'a', 'b')
    for runtime in ('cpu-fp32-v1', 'cpu-fp32-v1', 'onnx-fp32-v1'):
        agent = EmbeddingAgent(encode, encoder_digest='same-model', preprocessing_digest='p',
                               runtime_digest=runtime, dimension=2, cache=cache)
        agent.evaluate(rows, [key])
    assert calls == ['name', 'name']


def test_snapshot_encoder_error_outside_candidate_pairs_is_not_complete():
    from masterone.mdm.matching_agents.embedding_agent import EmbeddingAgent
    rows = [{'id': rid, 'normalized_fields': {'name_unicode': rid, 'address': None,
             '_field_quality': {'name': 'VALID', 'address': 'MISSING'}}} for rid in ('a', 'b', 'c')]
    key = PairKey('o', 'r', 'a', 'b')
    def encode(texts, field):
        return [[0., 0.] if text == 'c' else [1., 0.] for text in texts]
    agent = EmbeddingAgent(encode, encoder_digest='m', preprocessing_digest='p', dimension=2)
    embeddings = agent.evaluate(rows, [key])
    assert embeddings[0]['fields']['name'].status == 'OK'
    assert agent.last_coverage['name']['error_records'] == 1
    with pytest.raises(ValueError, match='EMBEDDING_COVERAGE_FAILED'):
        join_evidence([key], [{'key': key, 'status': 'OK'}], embeddings,
            expected_encoder_digest='m', expected_preprocessing_digest='p',
            snapshot_coverage=agent.last_coverage)


def test_worker_preserves_integrity_error_category():
    from masterone.mdm.matching_agents.encoder_worker import error_envelope
    envelope = error_envelope(ValueError('ENCODER_MODEL_FILE_MISMATCH'))
    assert envelope == {'error': 'ENCODER_MODEL_FILE_MISMATCH', 'integrity': True}
    assert error_envelope(ValueError('an arbitrary potentially sensitive exception')) == {'error': 'ENCODER_VALUEERROR', 'integrity': False}


def test_empty_candidates_still_cover_eligible_snapshot_fields_and_zero_denominator():
    from masterone.mdm.matching_agents.embedding_agent import EmbeddingAgent
    calls = []
    def encode(texts, field):
        calls.append(texts)
        return [[1., 0.] for _ in texts]
    agent = EmbeddingAgent(encode, encoder_digest='m', preprocessing_digest='p', dimension=2)
    rows = [{'id': 'a', 'normalized_fields': {'name_unicode': 'An', 'address': None,
             '_field_quality': {'name': 'VALID', 'address': 'MISSING'}}}]
    assert agent.evaluate(rows, [], owner_id='o', run_id='r') == []
    assert calls == [['An']]
    assert agent.last_coverage['name']['available_records'] == 1
    assert agent.last_coverage['address']['eligible_success_ratio'] is None
