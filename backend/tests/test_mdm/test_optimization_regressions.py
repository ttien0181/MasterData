"""Performance contracts: bounded work, identical evidence and opt-in retrieval."""
from copy import deepcopy

import pytest
from masterone.mdm.matching_agents.contracts import PairKey, cosine
from masterone.mdm.matching_agents.embedding_agent import EmbeddingAgent


def test_vector_validation_is_per_text_not_per_candidate(monkeypatch):
    import masterone.mdm.matching_agents.embedding_agent as module
    calls = []
    original = module.cosine
    def measured(*args):
        calls.append(1)
        return original(*args)
    monkeypatch.setattr(module, 'cosine', measured)
    rows = [{'id': str(i), 'normalized_fields': {'name_unicode': 'shared',
             '_field_quality': {'name': 'VALID'}}} for i in range(30)]
    keys = [PairKey.canonical('owner', 'run', str(a), str(b)) for a in range(30) for b in range(a)]
    agent = EmbeddingAgent(lambda texts, _: [[1., 2., 3.] for _ in texts],
                           encoder_digest='model', preprocessing_digest='v1', dimension=3)
    output = agent.evaluate(rows, keys)
    assert all(row['fields']['name'].cosine == pytest.approx(1.) for row in output)
    assert len(calls) <= 2, 'vector validation must not grow with candidate count'


@pytest.mark.parametrize('left,right', [([1e308, 1e308, 0.], [-1e308, 1e308, 0.]),
    ([1e-300, 2e-300, 0.], [3., -4., 5.]), ([1., 2., 3.], [-1., -2., -3.])])
def test_prepared_evidence_matches_reference_cosine(left, right):
    rows = [{'id': str(i), 'normalized_fields': {'name_unicode': str(i),
             '_field_quality': {'name': 'VALID'}}} for i in range(2)]
    agent = EmbeddingAgent(lambda texts, _: [left if text == '0' else right for text in texts],
                           encoder_digest='model', preprocessing_digest='v1', dimension=3)
    result = agent.evaluate(rows, [PairKey('owner', 'run', '0', '1')])
    assert result[0]['fields']['name'].cosine == pytest.approx(cosine(left, right, 3).cosine, abs=1e-14)


def test_global_lexical_union_crosses_dob_province_without_crossing_entity_type():
    from masterone.mdm.matching_agents.candidate_union import build_union

    from tests.test_mdm.test_matching_candidates import config
    rows = [{'id': str(i), 'entity_type': 'PERSON' if i < 2 else 'ORGANIZATION',
             'normalized_fields': {'name': name, 'name_unicode': name,
                'dob': f'{1980+i}-01-01', 'address_parts': {'province': str(i)},
                '_field_quality': {'name': 'VALID'}}}
            for i, name in enumerate(['alexander smith', 'smith alexandre', 'smith alexandre'])]
    cfg = config()
    cfg['experiment'] = 'E1'
    cfg['retrieval']['tfidf'] = {'top_k': 10, 'min_similarity': .30}
    baseline = build_union(rows, cfg, None, [], lambda *_: None)
    enabled = deepcopy(cfg)
    enabled['retrieval']['global_lexical'] = True
    result = build_union(rows, enabled, None, [], lambda *_: None)
    assert set(baseline.pairs) <= set(result.pairs)
    assert 'tfidf_global_name' in result.pairs['0', '1']
    assert all(o['left_id'] in {'0', '1'} and o['right_id'] in {'0', '1'}
               for o in result.metrics['provenance'] if o['pass'].startswith('tfidf_global'))
    assert sum(p['new_unique'] for p in result.metrics['passes'].values()) == len(result.pairs)


def test_operation_deadline_does_not_spend_embedding_budget_on_other_stages():
    from masterone.mdm.resolution_worker import operation_timeout
    assert operation_timeout(3600, {'resource_budget': {'run_seconds': 1800}}) == 3600
    assert operation_timeout(3600, {'resource_budget': {'run_seconds': 1800},
        'execution_budget': {'run_seconds': 900}}) == 900
    assert operation_timeout(60, None) == 60


def test_snapshot_overlay_streams_do_not_read_pair_evidence(client, workflow_run):
    from masterone.mdm.models import ResolutionRun
    from masterone.mdm.workflow_snapshot_producers import snapshot_streams
    from sqlalchemy import event

    from tests.test_mdm.test_workflow_stages import ready_decisions
    ready_decisions(client, workflow_run)
    statements = []
    engine = client.session_factory.kw['bind']
    def collect(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    with client.session_factory() as db:
        _, streams = snapshot_streams(db, db.get(ResolutionRun, workflow_run))
        event.listen(engine, 'before_cursor_execute', collect)
        try:
            overlays = list(streams['overlays'])
            reasons = list(streams['review_reasons'])
        finally:
            event.remove(engine, 'before_cursor_execute', collect)
    assert overlays and reasons == []
    assert len(statements) == 2
    assert all('baseline' not in sql and 'assessment' not in sql and 'advice' not in sql for sql in statements)


def test_global_lexical_profile_is_explicit_immutable_and_validated(client, monkeypatch):
    from masterone.config import get_settings

    from tests.test_mdm.test_matching_foundation import REGISTRY, flags
    flags(monkeypatch)
    for flag in ('advanced_matching_integration_enabled', 'advanced_matching_agents_enabled'):
        monkeypatch.setattr(get_settings(), flag, True)
    created = client.post(REGISTRY + '/profiles', json={'version': 1, 'integration': True, 'global_lexical': True})
    assert created.status_code == 201, created.text
    result = client.post(REGISTRY + f"/profiles/{created.json()['id']}/validate", json={'expected_revision': 1})
    assert result.status_code == 200, result.text
    from masterone.mdm.matching_foundation_models import MatchingProfile
    with client.session_factory() as db:
        profile = db.get(MatchingProfile, created.json()['id'])
        assert profile.config_json['retrieval']['global_lexical'] is True
    invalid = client.post(REGISTRY + '/profiles', json={'version': 2, 'global_lexical': True})
    assert invalid.status_code == 422


def test_last_successful_embedding_batch_is_checkpointed_before_next_work():
    saved, calls = {}, []
    cache = {}
    def checkpoint():
        saved.update(cache)
    def encode(texts, field):
        calls.append(list(texts))
        if texts == ['2']:
            raise ValueError('EMBEDDING_TRANSPORT_ERROR')
        return [[1., 2., 3.] for _ in texts]
    rows = [{'id': str(i), 'normalized_fields': {'name_unicode': str(i),
             '_field_quality': {'name': 'VALID'}}} for i in range(3)]
    agent = EmbeddingAgent(encode, encoder_digest='model', preprocessing_digest='v1', dimension=3,
        batch_size=1, cache=cache, cache_version='text_vectors_v2', on_batch_stored=checkpoint)
    with pytest.raises(ValueError, match='EMBEDDING_TRANSPORT_ERROR'):
        agent.evaluate(rows, [], owner_id='owner', run_id='run')
    assert len(saved) == 2
    assert calls == [['0'], ['1'], ['2']]
    calls.clear()
    resumed = EmbeddingAgent(lambda texts, _: calls.append(texts) or [[1., 2., 3.] for _ in texts],
        encoder_digest='model', preprocessing_digest='v1', dimension=3, batch_size=1,
        cache=saved, cache_version='text_vectors_v2')
    resumed.evaluate(rows, [], owner_id='owner', run_id='run')
    assert calls == [['2']]


def test_bulk_feedback_queries_are_bounded_and_audit_is_preserved(client, workflow_run, monkeypatch):
    from masterone.config import get_settings
    from masterone.mdm.matching_foundation_models import StewardFeedback
    from masterone.mdm.models import ResolutionAuditEvent, ResolutionRun
    from masterone.mdm.steward_feedback import capture_review_snapshot, persist_feedback, prepare_feedback_batch
    from masterone.mdm.workflow_models import WorkflowOperation, WorkflowPair
    from masterone.mdm.workflow_review import apply_decision
    from sqlalchemy import event

    from tests.test_mdm.test_workflow_stages import ready_decisions
    ready_decisions(client, workflow_run)
    monkeypatch.setattr(get_settings(), 'steward_feedback_capture_enabled', True)
    engine = client.session_factory.kw['bind']
    statements = []
    def collect(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    with client.session_factory.begin() as db:
        run = db.get(ResolutionRun, workflow_run)
        seed = db.query(WorkflowPair).filter_by(run_id=run.id).first()
        pairs = [WorkflowPair(id=f'feedback-{i:03}', run_id=run.id, baseline=deepcopy(seed.baseline),
                 decision='UNDECIDED', revision=0) for i in range(30)]
        db.add_all(pairs)
        op = WorkflowOperation(run_id=run.id, request_id='feedback-batch', operation_type='BULK_DECISIONS',
             actor_user_id=run.owner_user_id, payload_hash='0' * 64, payload={'decision': 'no_merge'})
        db.add(op)
        db.flush()
        event.listen(engine, 'before_cursor_execute', collect)
        try:
            batch = prepare_feedback_batch(db, run, pairs, op, run.owner_user_id)
            with db.no_autoflush:
                for pair in pairs:
                    snapshot = capture_review_snapshot(db, run, pair, batch=batch)
                    apply_decision(db, run, pair, 'no_merge', run.owner_user_id, links=set(), resolve_conflicts=False)
                    persist_feedback(db, run, pair, op, run.owner_user_id, snapshot, 'HUMAN_BULK_DIRECT', batch=batch)
            db.flush()
        finally:
            event.remove(engine, 'before_cursor_execute', collect)
        feedback = db.query(StewardFeedback).filter_by(operation_id=op.id).all()
        assert len(feedback) == len(pairs)
        assert all(row.human_decision == 'REJECTED' and row.human_direct_label is None for row in feedback)
        assert db.query(ResolutionAuditEvent).filter_by(run_id=run.id, action='FEEDBACK_CAPTURED').count() == 30
        assert db.query(ResolutionAuditEvent).filter_by(run_id=run.id, action='WORKFLOW_PAIR_REJECTED').count() == 30
    selects = [sql for sql in statements if sql.lstrip().upper().startswith('SELECT')]
    assert len(selects) <= 6, f'feedback SELECT count must be batch-bounded: {len(selects)}'


def test_checkpoint_failure_cannot_become_rule_only_fallback(client, monkeypatch):
    from masterone.config import get_settings
    from masterone.mdm.matching_agents import workflow_agents
    from masterone.mdm.matching_agents.openrouter_embedding import OpenRouterEmbeddingRuntime

    from tests.test_mdm.test_matching_foundation import REGISTRY, flags, new_run
    from tests.test_mdm.test_workflow_stages import ready_decisions
    flags(monkeypatch)
    settings = get_settings()
    for name, value in {'advanced_matching_agents_enabled': True, 'embedding_provider': 'openrouter',
                        'embedding_openrouter_api_key': 'test-only-unused', 'embedding_dimensions': 3}.items():
        monkeypatch.setattr(settings, name, value)
    monkeypatch.setattr(OpenRouterEmbeddingRuntime, 'encode', lambda self, texts, field: [[1., 0., 0.] for _ in texts])
    real_seal = workflow_agents.seal_agent
    def seal(factory, run_id, op_id, phase, payload):
        if phase == 'AGENT_VECTOR_CACHE':
            raise OSError('simulated checkpoint disk failure')
        return real_seal(factory, run_id, op_id, phase, payload)
    monkeypatch.setattr(workflow_agents, 'seal_agent', seal)
    created = client.post(REGISTRY + '/profiles', json={'version': 1, 'advanced_agents': True, 'fallback_mode': 'rule_only'})
    assert created.status_code == 201
    pid = created.json()['id']
    assert client.post(REGISTRY + f'/profiles/{pid}/validate', json={'expected_revision': 1}).status_code == 200
    assert client.put(REGISTRY + '/defaults/owner-private', json={'profile_id': pid, 'expected_revision': 0}).status_code == 200
    run = new_run(client)
    with pytest.raises(ValueError, match='ENCODER_CHECKPOINT_FAILED'):
        ready_decisions(client, run)
    assert client.get(REGISTRY + f'/runs/{run}/agents').status_code == 404


def test_feedback_batch_cannot_reuse_authorization_after_commit(client, workflow_run, monkeypatch):
    from types import SimpleNamespace

    from fastapi import HTTPException
    from masterone.auth.models import User
    from masterone.config import get_settings
    from masterone.mdm.models import ResolutionRun
    from masterone.mdm.steward_feedback import capture_review_snapshot, prepare_feedback_batch
    from masterone.mdm.workflow_models import WorkflowPair

    from tests.test_mdm.test_workflow_stages import ready_decisions
    ready_decisions(client, workflow_run)
    monkeypatch.setattr(get_settings(), 'steward_feedback_capture_enabled', True)
    with client.session_factory() as db:
        run = db.get(ResolutionRun, workflow_run)
        pair = db.query(WorkflowPair).filter_by(run_id=run.id).first()
        op = SimpleNamespace(id='batch', run_id=run.id, actor_user_id=run.owner_user_id,
                             operation_type='BULK_DECISIONS', payload={'decision': 'deny'})
        batch = prepare_feedback_batch(db, run, [pair], op, run.owner_user_id)
        assert capture_review_snapshot(db, run, pair, batch=batch)
        db.commit()
        with pytest.raises(ValueError, match='BATCH_IDENTITY_CHANGED'):
            capture_review_snapshot(db, run, pair, batch=batch)
        actor = db.get(User, run.owner_user_id)
        actor.role = 'VIEWER'
        db.commit()
        with pytest.raises(HTTPException) as denied:
            prepare_feedback_batch(db, run, [pair], op, run.owner_user_id)
        assert denied.value.status_code == 403
