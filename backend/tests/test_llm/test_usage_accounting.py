"""Provider usage includes billable thinking and survives rejected output."""
import pytest
from masterone.config import Settings
from masterone.llm import gateway
from masterone.llm.policy import PolicyDenied


def test_gemini_usage_includes_thinking_tokens():
    body = {'usageMetadata': {'promptTokenCount': 10, 'candidatesTokenCount': 3, 'thoughtsTokenCount': 7}}
    assert gateway.provider_usage('gemini', body) == {'input': 10, 'output': 10}


def test_missing_usage_is_unknown_not_zero():
    assert gateway.provider_usage('gemini', {}) == {'input': None, 'output': None}


def test_settings_diagnostics_do_not_print_secrets():
    settings = Settings(_env_file=None, gemini_api_key='private-gemini',
                        r2_secret_access_key='private-storage',
                        mdm_database_url='postgresql://user:private-password@host/db')
    assert 'private-' not in repr(settings)


def test_paid_invalid_output_keeps_provider_usage(monkeypatch):
    class Gate:
        def attempt(self, *args):
            return 'attempt'

        def finish(self, *args, **kwargs):
            self.status, self.usage = args[2], kwargs.get('usage')

    gate = Gate()
    monkeypatch.setattr(gateway, '_transport', lambda *args: {
        'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': 'not JSON'}]}}],
        'usageMetadata': {'promptTokenCount': 10, 'candidatesTokenCount': 3, 'thoughtsTokenCount': 7},
    })
    with pytest.raises(PolicyDenied, match='SCHEMA_INVALID'):
        gateway._call(gate, 'gemini', Settings(_env_file=None), 'EXPLANATION', {}, 'prompt', 'request', {})
    assert gate.status == 'SCHEMA_INVALID'
    assert gate.usage == {'input': 10, 'output': 10}
