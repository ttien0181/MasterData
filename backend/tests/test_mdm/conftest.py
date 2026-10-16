import pytest
from fastapi.testclient import TestClient
from masterone.auth.models import User
from masterone.config import get_settings
from masterone.db.base import Base
from masterone.db.session import get_session
from masterone.main import app
from masterone.mdm import models  # noqa: F401
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture
def client(monkeypatch):
    # API tests use local snapshots, never the developer's live R2/LLM settings.
    for key in ('ENDPOINT_URL', 'ACCESS_KEY_ID', 'SECRET_ACCESS_KEY', 'BUCKET'):
        monkeypatch.delenv('PAIR_CACHE_R2_' + key, raising=False)
    for field in ("r2_endpoint_url", "r2_access_key_id", "r2_secret_access_key", "r2_bucket", "gemini_api_key"):
        monkeypatch.setattr(get_settings(), field, "")
    monkeypatch.setattr(get_settings(), "semantic_address_enabled", False)
    # Validate and commit run inline here; process-based jobs are covered by test_import_jobs.py.
    monkeypatch.setattr(get_settings(), "import_jobs_inline", True)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def override_session():
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    test_client = TestClient(app)
    test_client.session_factory = factory
    # /start uses the same factory as the worker. Keep it isolated from the live DB.
    previous_factory = getattr(app.state, "session_factory", None)
    app.state.session_factory = factory
    test_client.headers.update({"Origin": "http://localhost:3000"})
    credentials = {"email": "steward@example.com", "password": "a-long-test-password"}
    assert test_client.post("/api/v1/auth/register", json=credentials).status_code == 201
    with factory.begin() as db:
        db.query(User).filter_by(email_normalized=credentials["email"]).one().role = "STEWARD"
    logged_in = test_client.post("/api/v1/auth/login", json=credentials)
    assert logged_in.status_code == 200
    test_client.headers.update({"X-CSRF-Token": logged_in.json()["csrf_token"]})
    yield test_client
    app.dependency_overrides.clear()
    if previous_factory is None:
        del app.state.session_factory
    else:
        app.state.session_factory = previous_factory
    engine.dispose()


@pytest.fixture
def senior_client(client):
    with client.session_factory.begin() as db:
        db.query(User).filter_by(email_normalized="steward@example.com").one().role = "SENIOR_STEWARD"
    return client


@pytest.fixture
def workflow_run(client, monkeypatch):
    monkeypatch.setattr(get_settings(), 'session_workflow_enabled', True)
    csv = b'source_record_id,source_system,name,phone,email,address\n1,CRM,Nguyen Van An,0901234567,a@example.test,123 Hanoi\n2,BILL,Nguyen Van An,0901234567,a@example.test,123 Hanoi\n3,CRM,Tran Thi Binh,0987654321,b@example.test,456 HCM\n'
    response = client.post('/api/v1/mdm/runs', data={'workflow_version': '2'}, files={'files': ('workflow.csv', csv, 'text/csv')})
    assert response.status_code == 201, response.text
    return response.json()['run_id']
