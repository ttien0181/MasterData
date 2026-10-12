import json

from masterone.config import Settings
from masterone.llm.gateway import _gemini_request


def test_gemini_three_uses_thinking_level_instead_of_unsupported_budget():
    settings = Settings(_env_file=None, gemini_model='gemini-3.5-flash-lite')
    body = json.loads(_gemini_request(settings, settings.gemini_model, 'counts only', {}).data)
    assert body['generationConfig']['thinkingConfig'] == {'thinkingLevel': 'MINIMAL'}


def test_gemini_two_keeps_existing_thinking_budget():
    settings = Settings(_env_file=None, gemini_model='gemini-2.5-flash')
    body = json.loads(_gemini_request(settings, settings.gemini_model, 'counts only', {}).data)
    assert body['generationConfig']['thinkingConfig'] == {'thinkingBudget': 0}
