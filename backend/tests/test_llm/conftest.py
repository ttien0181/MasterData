"""Durable LLM policy fixtures: a real database with one run, its task, its publication slot and the switch."""

import pytest
from masterone.auth.models import User
from masterone.config import Settings
from masterone.db.base import Base
from masterone.mdm import models  # noqa: F401 - registers LLM and resolution tables
from masterone.mdm.models import LLMPolicyControl, ResolutionRun, ResolutionTask
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def llm_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'llm.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as db:
        owner = User(id="owner-1", email="owner@example.com", email_normalized="owner@example.com",
                     password_hash="x", role="STEWARD")
        db.add(owner)
        db.add(ResolutionRun(id="run-1", owner_user_id="owner-1", status="RUNNING", phase="loading",
                             record_count=0, file_count=0))
        db.add(ResolutionTask(id="task-1", run_id="run-1", status="RUNNING"))
        db.add(LLMPolicyControl(id="global", enabled=True, revision=1))
    yield factory
    engine.dispose()


@pytest.fixture
def llm_settings():
    """Both providers approved with retention and a price, so each test changes only what it asserts."""
    return Settings(
        _env_file=None,
        llm_enabled=True,
        semantic_address_enabled=True,
        llm_explanation_enabled=True,
        gemini_api_key="gemini-test-key",
        openrouter_api_key="openrouter-test-key",
        openrouter_base_url="https://openrouter.test/api/v1",
        llm_approved_providers="gemini,openrouter",
        llm_approved_models="gemini-test,google/gemini-test",
        gemini_model="gemini-test",
        openrouter_model="google/gemini-test",
        gemini_retention_verified=True,
        gemini_retention_contract="gemini-zdr-contract",
        gemini_token_price_microusd=1,
        openrouter_retention_verified=True,
        openrouter_retention_contract="openrouter-zdr-contract",
        openrouter_token_price_microusd=1,
        gemini_max_retries=0,
        gemini_max_output_tokens=512,
        llm_request_timeout_seconds=5,
        gemini_quota_cooldown_seconds=900,
    )


def run_context(factory, run_id="run-1"):
    """Full snapshot, as a stage would take it once at its start."""
    from masterone.llm.policy import snapshot
    with factory() as db:
        return snapshot(db, run_id)
