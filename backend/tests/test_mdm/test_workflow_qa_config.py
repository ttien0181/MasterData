from masterone.config import get_settings


def test_workflow_env_selection_and_default_gate(monkeypatch, tmp_path):
    env = tmp_path / '.env.qa'
    env.write_text('MDM_DATABASE_PROFILE=local\nMDM_DATABASE_URL_LOCAL=sqlite:///qa.db\nSESSION_WORKFLOW_ENABLED=true\nMDM_ENGINE_V2_ENABLED=false\n', encoding='utf-8')
    monkeypatch.setenv('MASTERONE_ENV_FILE', str(env))
    for key in ('MDM_DATABASE_PROFILE', 'MDM_DATABASE_URL_LOCAL', 'SESSION_WORKFLOW_ENABLED', 'MDM_ENGINE_V2_ENABLED'):
        monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()
    try:
        api = get_settings()
        get_settings.cache_clear()
        worker = get_settings()
        same_qa_database = api.active_mdm_database_url == worker.active_mdm_database_url == 'sqlite:///qa.db'
        assert same_qa_database, 'ENV_SELECTION_MISMATCH (database values redacted)'
        assert api.session_workflow_enabled is True
        assert api.mdm_engine_v2_enabled is False
        monkeypatch.setenv('MASTERONE_ENV_FILE', str(tmp_path / 'missing'))
        get_settings.cache_clear()
        assert get_settings().session_workflow_enabled is False
    finally:
        get_settings.cache_clear()
