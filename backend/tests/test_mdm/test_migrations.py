from pathlib import Path
from tempfile import TemporaryDirectory

from alembic import command
from masterone.mdm.migrate import alembic_config, require_current, upgrade_database
from sqlalchemy import create_engine, inspect, text


def test_fresh_database_upgrades_to_head():
    with TemporaryDirectory(dir=Path(__file__).resolve().parents[2]) as folder:
        url = f"sqlite:///{(Path(folder) / 'fresh.db').as_posix()}"
        upgrade_database(url)
        engine = create_engine(url)
        require_current(engine)
        assert {"users", "auth_sessions", "auth_login_attempts", "mdm_imports"} <= set(inspect(engine).get_table_names())
        engine.dispose()


def test_unversioned_legacy_database_keeps_imports():
    with TemporaryDirectory(dir=Path(__file__).resolve().parents[2]) as folder:
        url = f"sqlite:///{(Path(folder) / 'legacy.db').as_posix()}"
        command.upgrade(alembic_config(url), "0001")
        engine = create_engine(url)
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO mdm_imports (id, created_at, profile) VALUES ('legacy', '2026-01-01', '{}')"))
            connection.execute(text("DROP TABLE alembic_version"))
        engine.dispose()
        upgrade_database(url)
        engine = create_engine(url)
        with engine.connect() as connection:
            assert connection.execute(text("SELECT id, owner_user_id FROM mdm_imports")).all() == [("legacy", None)]
        require_current(engine)
        engine.dispose()
