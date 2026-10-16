"""M2-A uses the actual API, leased worker and pinned local CPU encoder."""
from pathlib import Path

import pytest
from masterone.config import get_settings
from masterone.mdm.workflow_models import WorkflowOperation, WorkflowPair

from tests.test_mdm.test_matching_foundation import REGISTRY, flags, new_run
from tests.test_mdm.test_workflow_contract import request
from tests.test_mdm.test_workflow_stages import read, ready_decisions, run_one

TRIAL = Path(__file__).resolve().parents[3] / 'artifacts/advanced-m2a-20261014/e5-trial-01'


@pytest.fixture(autouse=True)
def historical_e5_baseline(monkeypatch):
    monkeypatch.setattr(get_settings(), 'embedding_provider', 'e5_local')
    monkeypatch.setenv('EMBEDDING_PROVIDER', 'e5_local')


def advanced(client, monkeypatch, fallback='fail_operation'):
    flags(monkeypatch)
    monkeypatch.setattr(get_settings(), 'advanced_matching_agents_enabled', True)
    monkeypatch.setattr(get_settings(), 'embedding_runtime_root', str(TRIAL))
    response = client.post(REGISTRY + '/profiles', json={'version': 1, 'advanced_agents': True,
                                                       'fallback_mode': fallback})
    assert response.status_code == 201, response.text
    row = response.json()
    response = client.post(REGISTRY + f"/profiles/{row['id']}/validate", json={'expected_revision': 1})
    assert response.status_code == 200, response.text
    response = client.put(REGISTRY + '/defaults/owner-private', json={'profile_id': row['id'], 'expected_revision': 0})
    assert response.status_code == 200, response.text
    return row


def test_advanced_flags_default_false_and_no_implicit_profile(client):
    assert not get_settings().advanced_matching_agents_enabled
    response = client.post(REGISTRY + '/profiles', json={'version': 1, 'advanced_agents': True})
    assert response.status_code == 404


def test_real_encoder_worker_sidecar_keeps_baseline(client, monkeypatch):
    row = advanced(client, monkeypatch)
    run = new_run(client)
    state = ready_decisions(client, run)
    response = client.get(REGISTRY + f'/runs/{run}/agents')
    assert response.status_code == 200, response.text
    report = response.json()
    assert report['profile_digest'] == row['digest']
    assert report['status'] == 'OK'
    assert report['coverage']['expected_pairs'] == report['coverage']['embedding_pairs']
    assert report['pairs']
    pair = report['pairs'][0]
    assert pair['rule']['score_unit'] == 'p119_rule_v1'
    assert pair['embedding']['encoder_digest'] == row['config']['encoder_identity']['bundle_digest']
    assert pair['embedding']['fields']['name']['status'] == 'OK'
    assert state['matching_agents']['status'] == 'OK'
    assert 'automation-apply' not in state['allowed_actions']


def test_degraded_is_terminal_explicit_and_blocks_automation(client, monkeypatch):
    advanced(client, monkeypatch, 'rule_only')
    run = new_run(client)
    run_one(client)
    state = read(client, run)
    response = client.post(f'/api/v1/mdm/runs/{run}/workflow/start-decide', json=request(state['workflow_revision']))
    assert response.status_code == 202
    from masterone.mdm.matching_agents.encoder_runtime import EncoderRuntime
    def crash(*args, **kwargs):
        raise ValueError('ENCODER_TEST_CRASH')
    monkeypatch.setattr(EncoderRuntime, '__enter__', crash)
    op_id = run_one(client)
    with client.session_factory() as db:
        assert db.get(WorkflowOperation, op_id).status == 'DEGRADED'
    state = read(client, run)
    assert state['matching_agents']['status'] == 'DEGRADED'
    assert state['matching_agents']['warning']
    assert 'automation-apply' not in state['allowed_actions']
    assert client.get(REGISTRY + f'/runs/{run}/agents').json()['status'] == 'DEGRADED'


def test_fail_operation_never_publishes_incomplete_sidecar(client, monkeypatch):
    advanced(client, monkeypatch)
    run = new_run(client)
    run_one(client)
    state = read(client, run)
    response = client.post(f'/api/v1/mdm/runs/{run}/workflow/start-decide', json=request(state['workflow_revision']))
    assert response.status_code == 202
    from masterone.mdm.matching_agents.encoder_runtime import EncoderRuntime
    def crash(*args, **kwargs):
        raise ValueError('ENCODER_TEST_CRASH')
    monkeypatch.setattr(EncoderRuntime, '__enter__', crash)
    with pytest.raises(ValueError, match='ENCODER'):
        run_one(client)
    assert client.get(REGISTRY + f'/runs/{run}/agents').status_code == 404


def test_advanced_full_rule_output_equivalence_and_old_run_no_backfill(client, monkeypatch):
    from copy import deepcopy

    from masterone.mdm.models import ResolutionRecord
    flags(monkeypatch, profile=False, feedback=False)
    first = new_run(client)
    ready_decisions(client, first)
    def snapshot(run):
        with client.session_factory() as db:
            ids = {r.id: (r.source_system, r.external_id) for r in db.query(ResolutionRecord).filter_by(run_id=run)}
            return {tuple(sorted((ids[p.baseline['left_id']], ids[p.baseline['right_id']]))):
                {k: v for k, v in deepcopy(p.baseline).items() if k not in {'id', 'left_id', 'right_id'}}
                for p in db.query(WorkflowPair).filter_by(run_id=run)}
    before = snapshot(first)
    advanced(client, monkeypatch)
    second = new_run(client)
    ready_decisions(client, second)
    assert snapshot(second) == before
    assert snapshot(first) == before
    assert client.get(REGISTRY + f'/runs/{first}/agents').status_code == 404


def test_reassessment_preserves_human_decision_and_original_baseline(client, monkeypatch):
    from copy import deepcopy

    from tests.test_mdm.test_matching_foundation import review
    advanced(client, monkeypatch)
    run = new_run(client)
    ready_decisions(client, run)
    response, _, _, pair = review(client, run, 'merge')
    assert response.status_code == 200, response.text
    with client.session_factory() as db:
        original = deepcopy(db.get(WorkflowPair, pair['pair_id']).baseline)
    state = read(client, run)
    response = client.post(f'/api/v1/mdm/runs/{run}/workflow/reassess', json=request(state['workflow_revision']))
    assert response.status_code == 202, response.text
    run_one(client)
    with client.session_factory() as db:
        row = db.get(WorkflowPair, pair['pair_id'])
        assert row.decision == 'ACCEPTED'
        assert row.baseline == original
    assert read(client, run)['matching_agents']['status'] == 'OK'


def test_advanced_mutations_and_evidence_are_owner_scoped(client, monkeypatch):
    profile = advanced(client, monkeypatch)
    run = new_run(client)
    ready_decisions(client, run)
    body = {'version': 2, 'advanced_agents': True}
    assert client.post(REGISTRY + '/profiles', json=body, headers={'X-CSRF-Token': 'bad'}).status_code == 403
    assert client.post(REGISTRY + '/profiles', json=body, headers={'Origin': 'https://evil.example'}).status_code == 403
    credentials = {'email': 'other-m2@example.com', 'password': 'another-long-test-password'}
    assert client.post('/api/v1/auth/register', json=credentials).status_code == 201
    logged = client.post('/api/v1/auth/login', json=credentials)
    client.headers['X-CSRF-Token'] = logged.json()['csrf_token']
    assert client.get(REGISTRY + f'/profiles/{profile["id"]}').status_code == 404
    assert client.get(REGISTRY + f'/runs/{run}/agents').status_code == 404
    assert client.post(REGISTRY + '/profiles', json=body).status_code == 403


def test_real_supervisor_crash_recovery_retries_same_profile(client, monkeypatch, tmp_path):
    import sqlite3

    from masterone.db.session import get_session
    from masterone.main import app
    from masterone.mdm.models import ResolutionTask
    from masterone.mdm.resolution_worker import ResolutionWorker
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    profile = advanced(client, monkeypatch)
    run = new_run(client)
    run_one(client)
    state = read(client, run)
    response = client.post(f'/api/v1/mdm/runs/{run}/workflow/start-decide', json=request(state['workflow_revision']))
    assert response.status_code == 202
    # The normal API fixture is in-memory SQLite, inaccessible from spawn.
    # Preserve it via backup into an owned file and point API/worker at that file.
    database_path = tmp_path / 'm2-worker-recovery.db'
    with client.session_factory.kw['bind'].connect() as connection:
        with sqlite3.connect(database_path) as target:
            connection.connection.driver_connection.backup(target)
    engine = create_engine('sqlite:///' + database_path.as_posix(), connect_args={'check_same_thread': False})
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    client.session_factory = factory
    def session():
        with factory() as db:
            yield db
    app.dependency_overrides[get_session] = session
    monkeypatch.setattr(app.state, 'session_factory', factory)
    # Spawned worker reads env rather than pytest's in-memory Settings monkeypatch.
    monkeypatch.setenv('SESSION_WORKFLOW_ENABLED', 'true')
    monkeypatch.setenv('ADVANCED_MATCHING_AGENTS_ENABLED', 'true')
    monkeypatch.setenv('EMBEDDING_RUNTIME_ROOT', str(TRIAL))
    interrupted = ResolutionWorker(client.session_factory, lease_seconds=6, timeout_seconds=.1)
    assert interrupted.run_once()
    with client.session_factory() as db:
        assert db.query(ResolutionTask).filter_by(run_id=run).one().status == 'QUEUED'
        assert db.query(WorkflowPair).filter_by(run_id=run).count() == 0
    resumed = ResolutionWorker(client.session_factory, lease_seconds=30, timeout_seconds=300)
    assert resumed.run_once()
    state = read(client, run)
    assert state['matching_agents']['status'] == 'OK'
    assert state['matching_agents']['profile_digest'] == profile['digest']
    with client.session_factory() as db:
        assert db.query(ResolutionTask).filter_by(run_id=run).one().attempt_count == 2
    engine.dispose()


def test_assessment_configuration_is_pinned_for_advanced_run(client, monkeypatch):
    monkeypatch.setattr(get_settings(), 'pair_assessment_auto_enabled', False)
    advanced(client, monkeypatch)
    run = new_run(client)
    run_one(client)
    monkeypatch.setattr(get_settings(), 'pair_assessment_auto_enabled', True)
    state = read(client, run)
    response = client.post(f'/api/v1/mdm/runs/{run}/workflow/start-decide', json=request(state['workflow_revision']))
    assert response.status_code == 202
    run_one(client)
    assert read(client, run)['assessment_id'] is None


def test_degraded_run_nonmatching_operations_have_valid_completion_receipts(client, monkeypatch):
    from masterone.mdm.models import ResolutionRun
    advanced(client, monkeypatch, 'rule_only')
    run = new_run(client)
    run_one(client)
    state = read(client, run)
    assert client.post(f'/api/v1/mdm/runs/{run}/workflow/start-decide', json=request(state['workflow_revision'])).status_code == 202
    from masterone.mdm.matching_agents.encoder_runtime import EncoderRuntime
    def crash(*args, **kwargs):
        raise ValueError('ENCODER_TEST_CRASH')
    monkeypatch.setattr(EncoderRuntime, '__enter__', crash)
    run_one(client)
    state = read(client, run)
    response = client.post(f'/api/v1/mdm/runs/{run}/workflow/cluster-preview', json=request(
        state['workflow_revision'], expected_decision_revision=state['decision_revision'], allow_partial=True))
    assert response.status_code == 202, response.text
    op_id = run_one(client)
    with client.session_factory() as db:
        assert db.get(WorkflowOperation, op_id).status == 'COMPLETED'
        assert db.get(ResolutionRun, run).status == 'DEGRADED'
    assert read(client, run)['matching_agents']['status'] == 'DEGRADED'


def test_integrity_failure_cannot_use_rule_only_fallback(client, monkeypatch):
    from masterone.mdm.matching_agents.contracts import EncoderIntegrityError
    from masterone.mdm.matching_agents.encoder_runtime import EncoderRuntime
    advanced(client, monkeypatch, 'rule_only')
    run = new_run(client)
    run_one(client)
    state = read(client, run)
    assert client.post(f'/api/v1/mdm/runs/{run}/workflow/start-decide', json=request(state['workflow_revision'])).status_code == 202
    def integrity_error(*args, **kwargs):
        raise EncoderIntegrityError('ENCODER_MODEL_FILE_MISMATCH')
    monkeypatch.setattr(EncoderRuntime, '__enter__', integrity_error)
    with pytest.raises(EncoderIntegrityError, match='MODEL_FILE_MISMATCH'):
        run_one(client)
    with client.session_factory() as db:
        assert db.query(WorkflowPair).filter_by(run_id=run).count() == 0
    assert client.get(REGISTRY + f'/runs/{run}/agents').status_code == 404
