"""Read-only deployment gate. No provider calls and no credential values printed.

docker compose --env-file .env.production -f docker-compose.production.yml \
    run --rm --no-deps -T backend python - < deploy/check_runtime.py
"""
import sys
from pathlib import Path

from masterone.config import get_settings
from masterone.db.session import create_database
from masterone.llm.policy import PolicyDenied, require_some_provider
from masterone.mdm.migrate import validate_current
from sqlalchemy import text


def main():
    failures = []

    def check(name, valid):
        print(f"{'PASS' if valid else 'FAIL'} {name}")
        if not valid:
            failures.append(name)

    try:
        settings = get_settings()
    except Exception:
        print('FAIL SETTINGS_INVALID (check required environment variables; values suppressed)')
        return 1
    check('production + CSRF secret', settings.app_env == 'production' and
          len(settings.auth_csrf_secret) >= 32 and 'REPLACE' not in settings.auth_csrf_secret)
    for name in ('demo_enabled', 'workflow_auto_process', 'session_workflow_enabled',
                 'entity_structure_enabled', 'matching_profile_registry_enabled',
                 'advanced_matching_agents_enabled', 'advanced_matching_integration_enabled'):
        check(name, getattr(settings, name))
    check('full demo processing', not settings.demo_fast_mode)
    check('real embedding credential', settings.embedding_provider == 'openrouter' and
          bool(settings.embedding_openrouter_api_key.strip()))
    check('HTTPS origin', settings.cors_origins.startswith('https://') and
          '*' not in settings.cors_origins and 'example.com' not in settings.cors_origins)
    check('LLM explanation enabled', settings.llm_enabled and settings.llm_explanation_enabled)
    try:
        require_some_provider(settings, 'EXPLANATION')
        check('LLM provider/model/retention/price approved', True)
    except PolicyDenied as exc:
        check(f'LLM policy ({exc.code})', False)
    root = Path(settings.workflow_artifact_dir)
    from tempfile import TemporaryFile
    try:
        root.mkdir(parents=True, exist_ok=True)
        with TemporaryFile(dir=root) as probe:
            probe.write(b'deployment-check')
        check('shared artifacts writable', True)
    except OSError:
        check('shared artifacts writable', False)
    engine = None
    try:
        engine, _ = create_database(settings.active_mdm_database_url)
        validate_current(engine)
        check('database migration at current head', True)
        with engine.connect() as conn:
            enabled = conn.execute(text("SELECT enabled FROM llm_policy_control WHERE id='global'")).scalar()
        check('database LLM global switch enabled', bool(enabled))
    except Exception:
        check('database/schema available (details suppressed)', False)
    finally:
        if engine is not None:
            engine.dispose()
    print(f'{len(failures)} failed checks. No AI requests were sent.')
    return int(bool(failures))


if __name__ == '__main__':
    sys.exit(main())
