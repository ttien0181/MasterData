"""Migration keeps historical manual decisions and adds nullable provenance."""
from alembic import command
from masterone.mdm.migrate import HEAD, alembic_config
from sqlalchemy import create_engine, inspect, text


def test_automation_provenance_is_added_on_real_migrated_schema(tmp_path):
    url='sqlite:///'+(tmp_path/'automation.db').as_posix()
    cfg=alembic_config(url)
    command.upgrade(cfg,'0030_review_reasons')
    engine=create_engine(url)
    with engine.begin() as db:
        db.execute(text("INSERT INTO mdm_workflow_pairs (id,run_id,baseline,decision,review_reason,revision) VALUES ('p','r','{}','ACCEPTED','source verified',4)"))
    command.upgrade(cfg,'head')
    assert HEAD=='0032_generation_coverage'
    assert 'automation' in {c['name'] for c in inspect(engine).get_columns('mdm_workflow_pairs')}
    with engine.connect() as db:
        row=db.execute(text('SELECT decision,review_reason,revision,automation FROM mdm_workflow_pairs')).one()
        assert tuple(row)==('ACCEPTED','source verified',4,None)
    command.upgrade(cfg,'head')
    engine.dispose()
