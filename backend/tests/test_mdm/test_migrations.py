from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from alembic import command
from alembic.migration import MigrationContext
from alembic.operations import Operations
from masterone.auth import models as auth_models  # noqa: F401
from masterone.db.base import Base
from masterone.mdm import models as mdm_models  # noqa: F401
from masterone.mdm.migrate import HEAD, alembic_config, require_current, upgrade_database
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session


@pytest.fixture(autouse=True)
def isolated_migration_environment(monkeypatch):
    # Migration tests must never use an environment override for the live DB.
    monkeypatch.delenv("MDM_DATABASE_URL", raising=False)


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
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == HEAD
            assert "mdm_imports" not in inspect(engine).get_table_names()
        require_current(engine)
        engine.dispose()


@pytest.mark.parametrize("early_schema", [True, False])
def test_upload_links_upgrade_preserves_existing_sessions_and_runs(tmp_path, early_schema):
    from masterone.api.mdm_runs import list_runs
    from masterone.auth.models import User
    from masterone.auth.security import CurrentSession
    from masterone.mdm.models import ResolutionRun, ResolutionUploadFile, ResolutionUploadSession

    url = f"sqlite:///{(tmp_path / 'upload-links.db').as_posix()}"
    command.upgrade(alembic_config(url), "0018_upload_sessions")
    engine = create_engine(url)
    try:
        with Session(engine) as db:
            user = User(id="owner", email="owner@example.test", email_normalized="owner@example.test",
                        password_hash="unused", role="STEWARD")
            db.add(user)
            db.flush()
            db.add(ResolutionUploadSession(id="draft", owner_user_id=user.id, total_bytes=10))
            db.flush()
            db.add(ResolutionRun(id="existing-run", owner_user_id=user.id, name="Existing session",
                                 upload_session_id=None if early_schema else "draft", record_count=3))
            db.add(ResolutionUploadFile(id="original", session_id="draft", position=0,
                                        filename="original.csv", expected_size=10))
            db.flush()
            db.add(ResolutionUploadFile(id="replacement", session_id="draft", position=1,
                                        filename="replacement.csv", expected_size=10,
                                        replaces_file_id=None if early_schema else "original"))
            db.commit()

        if early_schema:
            # Reproduce the deployed schema: 0018 stamped, but its first version
            # did not contain either link. Startup must upgrade this database.
            with engine.begin() as connection:
                operations = Operations(MigrationContext.configure(connection))
                for table, column in (("mdm_resolution_runs", "upload_session_id"),
                                      ("mdm_resolution_upload_files", "replaces_file_id")):
                    inspector = inspect(connection)
                    naming = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}
                    with operations.batch_alter_table(table, naming_convention=naming) as batch:
                        for fk in inspector.get_foreign_keys(table):
                            if fk["constrained_columns"] == [column]:
                                name = fk["name"] or f"fk_{table}_{column}_{fk['referred_table']}"
                                batch.drop_constraint(name, type_="foreignkey")
                        for unique in inspector.get_unique_constraints(table):
                            if unique["column_names"] == [column]:
                                batch.drop_constraint(unique["name"], type_="unique")
                        batch.drop_column(column)

        require_current(engine)
        require_current(engine)
        with Session(engine) as db:
            user = db.get(User, "owner")
            result = list_runs(limit=25, offset=0, archived="active", db=db,
                               auth=CurrentSession(user=user, session=None, token=""))
            assert result["total"] == 1
            assert result["items"][0]["run_id"] == "existing-run"
            assert result["items"][0]["record_count"] == 3
            assert db.get(ResolutionRun, "existing-run").upload_session_id == (None if early_schema else "draft")
            assert db.get(ResolutionUploadFile, "replacement").replaces_file_id == (
                None if early_schema else "original")
            assert db.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == HEAD
        inspector = inspect(engine)
        for table, column in (("mdm_resolution_runs", "upload_session_id"),
                              ("mdm_resolution_upload_files", "replaces_file_id")):
            assert any(fk["constrained_columns"] == [column] for fk in inspector.get_foreign_keys(table))
            assert any(unique["column_names"] == [column] for unique in inspector.get_unique_constraints(table))
    finally:
        engine.dispose()
