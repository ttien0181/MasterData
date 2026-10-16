"""Frozen M1 database guard contract, shared by create_all and migration 0033."""
from sqlalchemy import DDL, event, text

MUTABLE = {'mdm_matching_profiles': {'lifecycle', 'lifecycle_revision'},
           'mdm_matching_profile_defaults': {'profile_id', 'revision', 'changed_by', 'changed_at'}}


def statements(table, dialect):
    name = table.name
    mutable = MUTABLE.get(name, set())
    immutable = [c.name for c in table.columns if c.name not in mutable]
    audit = name == 'mdm_resolution_audit_events'
    prefix = 'OLD.owner_user_id IS NOT NULL AND ' if audit else ''
    hashes = [c.name for c in table.columns if c.name.endswith(('_hash', '_digest')) or c.name == 'feature_schema_identity']
    transition_invalid = "(NEW.lifecycle != OLD.lifecycle AND (NEW.lifecycle_revision != OLD.lifecycle_revision + 1 OR NOT ((OLD.lifecycle = 'CANDIDATE' AND NEW.lifecycle IN ('VALIDATED','REJECTED','RETIRED')) OR (OLD.lifecycle = 'VALIDATED' AND NEW.lifecycle IN ('ACTIVE','RETIRED')) OR (OLD.lifecycle IN ('ACTIVE','REJECTED') AND NEW.lifecycle = 'RETIRED')))) OR (NEW.lifecycle = OLD.lifecycle AND NEW.lifecycle_revision != OLD.lifecycle_revision)"
    if dialect == 'sqlite':
        changed = ' OR '.join(f'NEW.{c} IS NOT OLD.{c}' for c in immutable)
        sql = [f"CREATE TRIGGER IF NOT EXISTS {name}_immutable_update BEFORE UPDATE ON {name} WHEN {prefix}({changed}) BEGIN SELECT RAISE(ABORT, 'M1_IDENTITY_IMMUTABLE'); END",
               f"CREATE TRIGGER IF NOT EXISTS {name}_retain_delete BEFORE DELETE ON {name} " +
               ("WHEN OLD.owner_user_id IS NOT NULL " if audit else '') +
               "BEGIN SELECT RAISE(ABORT, 'M1_LINEAGE_RETAINED'); END"]
        if hashes:
            invalid = ' OR '.join(f"(NEW.{c} IS NOT NULL AND (length(NEW.{c}) != 64 OR NEW.{c} GLOB '*[^0-9a-f]*'))" for c in hashes)
            sql.append(f"CREATE TRIGGER IF NOT EXISTS {name}_hash_insert BEFORE INSERT ON {name} WHEN {invalid} BEGIN SELECT RAISE(ABORT, 'M1_HASH_INVALID'); END")
        if name == 'mdm_matching_profiles':
            sql.append(f"CREATE TRIGGER IF NOT EXISTS {name}_lifecycle_update BEFORE UPDATE ON {name} WHEN {transition_invalid} BEGIN SELECT RAISE(ABORT, 'M1_LIFECYCLE_INVALID'); END")
        if name == 'mdm_matching_profile_defaults':
            sql.append(f"CREATE TRIGGER IF NOT EXISTS {name}_revision_update BEFORE UPDATE ON {name} WHEN NEW.revision != OLD.revision + 1 BEGIN SELECT RAISE(ABORT, 'M1_DEFAULT_REVISION_INVALID'); END")
        return sql
    excluded = ','.join("'" + c + "'" for c in sorted(mutable))
    old, new = 'to_jsonb(OLD)', 'to_jsonb(NEW)'
    if excluded:
        old += f' - ARRAY[{excluded}]'
        new += f' - ARRAY[{excluded}]'
    condition = 'OLD.owner_user_id IS NOT NULL' if audit else 'TRUE'
    invalid = ' OR '.join(f"(NEW.{c} IS NOT NULL AND NEW.{c} !~ '^[0-9a-f]{{64}}$')" for c in hashes) or 'FALSE'
    extra = ''
    if name == 'mdm_matching_profiles':
        extra = f"IF TG_OP = 'UPDATE' AND ({transition_invalid}) THEN RAISE EXCEPTION 'M1_LIFECYCLE_INVALID'; END IF;"
    elif name == 'mdm_matching_profile_defaults':
        extra = "IF TG_OP = 'UPDATE' AND NEW.revision != OLD.revision + 1 THEN RAISE EXCEPTION 'M1_DEFAULT_REVISION_INVALID'; END IF;"
    return [f"""CREATE OR REPLACE FUNCTION {name}_guard_v1() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP = 'DELETE' THEN
        IF {condition} THEN RAISE EXCEPTION 'M1_LINEAGE_RETAINED'; END IF;
        RETURN OLD;
      END IF;
      IF TG_OP = 'UPDATE' AND {condition} AND {old} IS DISTINCT FROM {new} THEN
        RAISE EXCEPTION 'M1_IDENTITY_IMMUTABLE';
      END IF;
      IF TG_OP = 'INSERT' AND ({invalid}) THEN RAISE EXCEPTION 'M1_HASH_INVALID'; END IF;
      {extra}
      RETURN NEW;
    END; $$""",
        f"CREATE TRIGGER {name}_guard_v1 BEFORE INSERT OR UPDATE OR DELETE ON {name} FOR EACH ROW EXECUTE FUNCTION {name}_guard_v1()"]


def install_guards(connection, tables):
    dialect = connection.dialect.name
    if dialect not in {'sqlite', 'postgresql'}:
        raise RuntimeError('M1_DATABASE_DIALECT_UNSUPPORTED')
    for table in tables:
        for statement in statements(table, dialect):
            connection.execute(text(statement))


def register_guards(tables):
    for table in tables:
        for dialect in ('sqlite', 'postgresql'):
            for statement in statements(table, dialect):
                event.listen(table, 'after_create', DDL(statement).execute_if(dialect=dialect))
