from pathlib import Path
from tempfile import TemporaryDirectory

from alembic import command
from masterone.auth import models as auth_models  # noqa: F401
from masterone.db.base import Base
from masterone.mdm import models as mdm_models  # noqa: F401
from masterone.mdm.migrate import alembic_config, require_current, upgrade_database
from sqlalchemy import create_engine, inspect, text


def test_fresh_database_upgrades_to_head():
    with TemporaryDirectory(dir=Path(__file__).resolve().parents[2]) as folder:
        url = f"sqlite:///{(Path(folder) / 'fresh.db').as_posix()}"
        upgrade_database(url)
        engine = create_engine(url)
        require_current(engine)
        # Tables created by migrations but without ORM models in Base.metadata
        migration_only_tables = {
            "llm_requests", "llm_semantic_evidence", "llm_explanations",  # 0013_llm_audit_tables
            "jobs", "job_stages",  # job queue tables
        }
        assert set(inspect(engine).get_table_names()) == set(Base.metadata.tables) | {"alembic_version"} | migration_only_tables
        engine.dispose()


def test_partial_database_upgrades_to_head():
    with TemporaryDirectory(dir=Path(__file__).resolve().parents[2]) as folder:
        url = f"sqlite:///{(Path(folder) / 'partial.db').as_posix()}"
        # Simulate a DB that was upgraded to 0001 (pre-streamline era)
        command.upgrade(alembic_config(url), "0001")
        engine = create_engine(url)
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO mdm_imports (id, created_at, profile) VALUES ('legacy', '2026-01-01', '{}')"))
        engine.dispose()
        # Upgrade from 0001 → head; 0011 should drop mdm_imports
        upgrade_database(url)
        engine = create_engine(url)
        with engine.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0015"
            assert "mdm_imports" not in inspect(engine).get_table_names()
        require_current(engine)
        engine.dispose()
