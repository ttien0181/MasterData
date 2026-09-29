import pytest
from fastapi.testclient import TestClient
from masterone.db.base import Base
from masterone.db.session import get_session
from masterone.main import app
from masterone.mdm import models  # noqa: F401
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def override_session():
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    test_client = TestClient(app)
    test_client.session_factory = factory
    test_client.headers.update({"Origin": "http://localhost:3000"})
    credentials = {"email": "steward@example.com", "password": "a-long-test-password"}
    assert test_client.post("/api/v1/auth/register", json=credentials).status_code == 201
    logged_in = test_client.post("/api/v1/auth/login", json=credentials)
    assert logged_in.status_code == 200
    test_client.headers.update({"X-CSRF-Token": logged_in.json()["csrf_token"]})
    yield test_client
    app.dependency_overrides.clear()
    engine.dispose()
