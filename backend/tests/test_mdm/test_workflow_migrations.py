from alembic import command
from alembic.script import ScriptDirectory
from masterone.mdm.migrate import HEAD, alembic_config, upgrade_database
from sqlalchemy import create_engine, inspect, text


def test_workflow_migration_additive(tmp_path):
    url = f"sqlite:///{(tmp_path / 'workflow.db').as_posix()}"
    cfg = alembic_config(url)
    assert ScriptDirectory.from_config(cfg).get_heads() == [HEAD]
    command.upgrade(cfg, '0027_llm_policy')
    engine = create_engine(url)
    with engine.begin() as db:
        db.execute(text("INSERT INTO mdm_resolution_runs (id, owner_user_id, status, phase, policy_version, record_count, file_count, candidate_count, insufficient_count, created_at) VALUES ('legacy', 'owner', 'COMPLETED', 'completed', 'person-v3', 0, 0, 0, 0, CURRENT_TIMESTAMP)"))
    upgrade_database(url)
    with engine.connect() as db:
        assert db.execute(text("SELECT workflow_version, workflow_revision, status FROM mdm_resolution_runs WHERE id='legacy'")).one() == (1, 0, 'COMPLETED')
        assert db.execute(text('SELECT version_num FROM alembic_version')).scalar() == HEAD
    assert 'mdm_workflow_operations' in inspect(engine).get_table_names()
    engine.dispose()
