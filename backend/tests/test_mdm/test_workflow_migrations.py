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
    columns = {column['name']: column for column in inspect(engine).get_columns('mdm_workflow_pairs')}
    assert columns['review_reason']['nullable'] is True
    assert columns['review_reason']['type'].length == 500
    engine.dispose()


def test_generation_receipts_upgrade_real_0031_layout(tmp_path):
    url = f"sqlite:///{(tmp_path / 'generation.db').as_posix()}"
    cfg = alembic_config(url)
    command.upgrade(cfg, '0031_pair_automation')
    engine = create_engine(url)
    # Historical table creation imports current ORM metadata. Remove the new
    # fields here so this test exercises a genuine pre-0032 column layout.
    with engine.begin() as db:
        for column in ('coverage', 'singleton_confirmation', 'storage_version'):
            db.execute(text(f'ALTER TABLE mdm_workflow_generations DROP COLUMN {column}'))
        db.execute(text("INSERT INTO mdm_workflow_generations (id, run_id, revision, artifact_id) VALUES ('old-gen', 'run', 1, 'old-artifact')"))
    command.upgrade(cfg, 'head')
    with engine.connect() as db:
        assert db.execute(text("SELECT coverage, singleton_confirmation, storage_version FROM mdm_workflow_generations WHERE id='old-gen'")).one() == ('{}', '{}', 'workflow_json_v1')
    assert len(ScriptDirectory.from_config(cfg).get_heads()) == 1
    assert workflow_cluster_id_length() >= 75
    engine.dispose()


def workflow_cluster_id_length():
    from masterone.mdm.workflow_models import WorkflowCluster
    return WorkflowCluster.__table__.c.id.type.length


def test_optional_embedding_migration_preserves_existing_run_mode(tmp_path):
    url = f"sqlite:///{(tmp_path / 'embedding.db').as_posix()}"
    cfg = alembic_config(url)
    command.upgrade(cfg, '0038_demo_feedback_retention')
    engine = create_engine(url)
    # Old migrations can import current ORM tables; reproduce the actual old layout.
    with engine.begin() as db:
        for table in ('mdm_resolution_runs', 'mdm_resolution_upload_sessions'):
            if 'use_embedding' in {c['name'] for c in inspect(db).get_columns(table)}:
                db.execute(text(f'ALTER TABLE {table} DROP COLUMN use_embedding'))
        db.execute(text("INSERT INTO mdm_resolution_runs (id, owner_user_id, status, phase, policy_version, record_count, file_count, candidate_count, insufficient_count, created_at) VALUES ('legacy', 'owner', 'COMPLETED', 'completed', 'person-v3', 0, 0, 0, 0, CURRENT_TIMESTAMP)"))
    upgrade_database(url)
    with engine.connect() as db:
        assert db.execute(text("SELECT use_embedding, status FROM mdm_resolution_runs WHERE id='legacy'")).one() == (1, 'COMPLETED')
    for table in ('mdm_resolution_runs', 'mdm_resolution_upload_sessions'):
        assert 'use_embedding' in {c['name'] for c in inspect(engine).get_columns(table)}
    engine.dispose()
