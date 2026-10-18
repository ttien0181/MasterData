"""Expired synthetic demo runs have a separate retention policy from customer lineage."""
from sqlalchemy import event,text,inspect
from masterone.db.base import Base
from masterone.mdm.matching_foundation_guards_v1 import statements

TABLES=('mdm_matching_bindings','mdm_steward_feedback','mdm_evaluation_reports',
        'mdm_structure_constraints','mdm_resolution_audit_events')


def install(connection, enabled=True):
    dialect=connection.dialect.name
    if dialect not in ('sqlite','postgresql'): return
    clock="CAST(strftime('%s','now') AS INTEGER)" if dialect=='sqlite' else 'EXTRACT(EPOCH FROM clock_timestamp())'
    eligible=("EXISTS (SELECT 1 FROM demo_grants dg JOIN users du ON du.id=dg.user_id "
              "WHERE dg.user_id=OLD.owner_user_id AND dg.run_id=OLD.run_id AND NOT du.is_active "
              "AND dg.state IN ('ENDED','EXPIRED','PURGED') AND dg.expires_at_epoch < "+clock+")")
    for name in TABLES:
        table=Base.metadata.tables[name]
        if dialect=='sqlite':
            connection.execute(text(f'DROP TRIGGER IF EXISTS {name}_retain_delete'))
            sql=statements(table,dialect)[1]
            if enabled:
                existing='WHEN OLD.owner_user_id IS NOT NULL '
                sql=sql.replace(existing,existing+'AND NOT ('+eligible+') ') if existing in sql else sql.replace(' BEGIN SELECT',' WHEN NOT ('+eligible+') BEGIN SELECT')
            connection.execute(text(sql))
        else:
            sql=statements(table,dialect)[0]
            if enabled: sql=sql.replace("IF TG_OP = 'DELETE' THEN", "IF TG_OP = 'DELETE' THEN IF "+eligible+" THEN RETURN OLD; END IF;")
            connection.execute(text(sql))


@event.listens_for(Base.metadata,'after_create')
def _install_for_test_and_local_metadata(metadata,connection,**kwargs):
    if all(inspect(connection).has_table(name) for name in (*TABLES,'demo_grants')):
        install(connection)
