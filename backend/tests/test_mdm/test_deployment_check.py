"""The handoff gate must reject incomplete deployments without leaking secrets."""
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from masterone.config import Settings


@pytest.fixture
def gate(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[3] / 'deploy' / 'check_runtime.py'
    spec = importlib.util.spec_from_file_location('deployment_check', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    settings = Settings(_env_file=None, app_env='production',
        auth_csrf_secret='secret-value-must-not-appear-in-output-0123456789',
        cors_origins='https://masterone.test', demo_enabled=True,
        workflow_auto_process=True, session_workflow_enabled=True,
        entity_structure_enabled=True, matching_profile_registry_enabled=True,
        advanced_matching_agents_enabled=True, advanced_matching_integration_enabled=True,
        embedding_provider='openrouter', embedding_openrouter_api_key='private-embedding-key',
        workflow_artifact_dir=str(tmp_path), llm_enabled=True, llm_explanation_enabled=True,
        openrouter_api_key='private-chat-key', llm_approved_providers='openrouter',
        llm_approved_models='google/gemini-2.5-flash',
        openrouter_retention_verified=True, openrouter_retention_contract='test-approved-policy',
        openrouter_token_price_microusd=1)
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value.execute.return_value.scalar.return_value = True
    monkeypatch.setattr(module, 'get_settings', lambda: settings)
    monkeypatch.setattr(module, 'create_database', lambda _: (engine, None))
    monkeypatch.setattr(module, 'validate_current', lambda _: None)
    return module, settings, engine


def test_gate_accepts_complete_setup_and_never_prints_secrets(gate, capsys):
    module, _, engine = gate
    assert module.main() == 0
    output = capsys.readouterr().out
    assert '0 failed checks' in output
    assert 'private-' not in output and 'secret-value' not in output
    engine.dispose.assert_called_once()


def test_gate_rejects_disabled_global_llm(gate, capsys):
    module, _, engine = gate
    engine.connect.return_value.__enter__.return_value.execute.return_value.scalar.return_value = False
    assert module.main() == 1
    assert 'FAIL database LLM global switch enabled' in capsys.readouterr().out


def test_gate_rejects_unapproved_provider_and_disabled_auto_processing(gate, capsys):
    module, settings, _ = gate
    settings.workflow_auto_process = False
    settings.llm_approved_providers = ''
    assert module.main() == 1
    output = capsys.readouterr().out
    assert 'FAIL workflow_auto_process' in output
    assert 'PROVIDER_DENIED' in output
    assert 'private-' not in output
