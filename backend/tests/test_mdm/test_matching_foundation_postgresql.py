"""Actual PostgreSQL QA migrations/concurrency; explicit isolated URL only."""
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from alembic import command
from fastapi import HTTPException
from masterone.auth.models import User
from masterone.db.session import get_session
from masterone.main import app
from masterone.mdm.matching_foundation_contracts import DefaultApply, ProfileCreate
from masterone.mdm.matching_foundation_models import MatchingProfile, StewardFeedback
from masterone.mdm.matching_foundation_service import apply_default, create_profile, transition_profile
from masterone.mdm.migrate import alembic_config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from tests.test_mdm.test_matching_foundation import REGISTRY, flags, new_run, profile, review
from tests.test_mdm.test_workflow_contract import request
from tests.test_mdm.test_workflow_stages import read, ready_decisions


@pytest.fixture
def pg_engine():
    value = os.environ.get('M1_QA_POSTGRESQL_URL')
    if not value:
        pytest.skip('Explicit isolated PostgreSQL QA URL not supplied')
    url = make_url(value)
    assert url.host in {'127.0.0.1', 'localhost'} and url.port in {5432, 55434}
    assert url.database.startswith('masterone_m1_qa') and url.username.startswith('m1_qa')
    assert os.environ.get('M1_QA_VERIFIED_CONTAINER_ID'), 'Verified QA container identity required'
    schema = 'm1_test_' + uuid4().hex
    admin = create_engine(url, connect_args={'connect_timeout': 5})
    with admin.begin() as connection:
        connection.execute(text('CREATE SCHEMA ' + schema))
    admin.dispose()
    engine = create_engine(url, connect_args={'options': '-csearch_path=' + schema, 'connect_timeout': 5})
    with engine.begin() as connection:
        config = alembic_config(value)
        config.attributes['connection'] = connection
        command.upgrade(config, 'head')
    yield engine
    engine.dispose()  # Preserve QA schemas/data; no DROP/cleanup commands.


def migrate(engine, revision):
    with engine.begin() as connection:
        config = alembic_config(engine.url.render_as_string(hide_password=False))
        config.attributes['connection'] = connection
        command.downgrade(config, revision) if revision == '0032_generation_coverage' else command.upgrade(config, revision)


@pytest.fixture
def pg_client(client, pg_engine):
    factory = sessionmaker(pg_engine, expire_on_commit=False)
    def session_override():
        with factory() as db:
            yield db
    app.dependency_overrides[get_session] = session_override
    app.state.session_factory = factory
    client.session_factory = factory
    credentials = {'email': 'm1-pg@example.com', 'password': 'm1-pg-isolated-long-password'}
    assert client.post('/api/v1/auth/register', json=credentials).status_code == 201
    with factory.begin() as db:
        db.query(User).filter_by(email_normalized=credentials['email']).one().role = 'STEWARD'
    logged = client.post('/api/v1/auth/login', json=credentials)
    assert logged.status_code == 200
    client.headers['X-CSRF-Token'] = logged.json()['csrf_token']
    return client


def test_pg_upgrade_empty_downgrade_and_reupgrade(pg_engine):
    assert 'mdm_matching_profiles' in inspect(pg_engine).get_table_names()
    migrate(pg_engine, '0032_generation_coverage')
    assert 'mdm_matching_profiles' not in inspect(pg_engine).get_table_names()
    migrate(pg_engine, 'head')
    with pg_engine.connect() as db:
        assert db.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == '0033_matching_foundation'
    assert any(fk['name'] == 'fk_matching_profile_report' for fk in inspect(pg_engine).get_foreign_keys('mdm_matching_profiles'))


def test_pg_immutable_raw_sql_and_downgrade_refuses_lineage(pg_client, pg_engine, monkeypatch):
    flags(monkeypatch)
    row = profile(pg_client)
    for statement in ('UPDATE mdm_matching_profiles SET config_digest=:value WHERE id=:id',
                      'DELETE FROM mdm_matching_profiles WHERE id=:id'):
        with pytest.raises(DBAPIError), pg_engine.begin() as connection:
            connection.execute(text(statement), {'value': 'b' * 64, 'id': row['id']})
    with pytest.raises(RuntimeError, match='M1_DOWNGRADE_REFUSED_LINEAGE_PRESENT'):
        migrate(pg_engine, '0032_generation_coverage')
    assert pg_client.get(REGISTRY + '/profiles/' + row['id']).json()['digest'] == row['digest']
    with pg_engine.connect() as db:
        assert db.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == '0033_matching_foundation'


def test_pg_concurrent_default_cas_allows_one_writer(pg_engine, monkeypatch):
    flags(monkeypatch)
    factory = sessionmaker(pg_engine, expire_on_commit=False)
    with factory.begin() as db:
        actor = User(email='cas@example.com', email_normalized='cas@example.com', password_hash='qa-not-a-login', role='STEWARD')
        db.add(actor)
        db.flush()
        actor_id = actor.id
        row = create_profile(db, actor_id, ProfileCreate(version=1))
        identifier = row.id
        transition_profile(db, actor_id, identifier, 1, 'VALIDATED')
    def write():
        try:
            with factory.begin() as db:
                pointer = apply_default(db, actor_id, 'owner-private', DefaultApply(profile_id=identifier, expected_revision=0))
                return pointer.revision
        except HTTPException as exc:
            return exc.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: write(), range(2)))
    assert sorted(results) == [1, 409]


def test_pg_actual_worker_binding_review_amend_and_retry(pg_client, monkeypatch):
    flags(monkeypatch)
    p = profile(pg_client)
    run = new_run(pg_client)
    ready_decisions(pg_client, run)
    binding = pg_client.get(REGISTRY + f'/runs/{run}/binding').json()
    assert binding['revision'] == 3 and binding['profile_digest'] == p['digest']
    result, endpoint, body, pair = review(pg_client, run, 'merge')
    assert result.status_code == 200, result.text
    assert pg_client.post(endpoint, json=body).json() == result.json()
    amended, _, _, _ = review(pg_client, run, 'no_merge', pair_id=pair['pair_id'], amend=True)
    assert amended.status_code == 200, amended.text
    with pg_client.session_factory() as db:
        rows = db.query(StewardFeedback).order_by(StewardFeedback.pair_revision).all()
        assert len(rows) == 2 and rows[0].human_direct_label == 'MATCH' and rows[1].human_direct_label is None
        assert rows[1].supersedes_feedback_id == rows[0].id
        assert rows[0].assessment_id == binding['assessment_id']
        assert rows[0].profile_digest == p['digest']
        assert db.query(MatchingProfile).count() == 1


@pytest.mark.parametrize('same_request', [True, False])
def test_pg_concurrent_direct_review_preserves_one_decision_and_feedback(pg_client, monkeypatch, same_request):
    flags(monkeypatch)
    profile(pg_client)
    run = new_run(pg_client)
    ready_decisions(pg_client, run)
    root = f'/api/v1/mdm/runs/{run}/workflow'
    pair = next(p for p in pg_client.get(root + '/pairs').json()['items'] if p['eligibility']['manual_allowed'])
    state = read(pg_client, run)
    first = request(state['workflow_revision'], request_id='m1-concurrent',
                    expected_pair_revision=pair['revision'], decision='merge')
    second = {**first, 'request_id': 'm1-concurrent' if same_request else 'm1-competing'}
    endpoint = root + f"/pairs/{pair['pair_id']}/decision"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda body: pg_client.post(endpoint, json=body), [first, second]))
    assert sorted(r.status_code for r in results) == ([200, 200] if same_request else [200, 409])
    with pg_client.session_factory() as db:
        rows = db.query(StewardFeedback).filter_by(run_id=run).all()
        assert len(rows) == 1 and rows[0].pair_revision == 1 and rows[0].human_direct_label == 'MATCH'


def test_pg_composite_scope_fk_rejects_cross_owner_default(pg_client, pg_engine, monkeypatch):
    from masterone.mdm.matching_foundation_models import MatchingProfileDefault
    flags(monkeypatch)
    p = profile(pg_client)
    with pg_client.session_factory.begin() as db:
        other = User(email='other-scope@example.com', email_normalized='other-scope@example.com',
                     password_hash='isolated-qa-not-a-login', role='STEWARD')
        db.add(other)
        db.flush()
        other_id = other.id
    with pytest.raises(DBAPIError), pg_client.session_factory.begin() as db:
        db.add(MatchingProfileDefault(owner_user_id=other_id, data_scope='owner-private',
               profile_id=p['id'], revision=1, changed_by=other_id))
        db.flush()
