"""Safely initialize or upgrade the MDM database."""

import argparse
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from masterone.config import get_settings

BASELINE = {
    "mdm_imports": {"id", "created_at", "profile"},
    "mdm_source_records": {"id", "import_id", "source", "source_record_id", "raw_fields", "normalized_fields"},
    "mdm_candidates": {"id", "import_id", "left_record_id", "right_record_id", "engine_id", "evidence", "decision"},
    "mdm_golden_record_drafts": {"id", "candidate_id", "fields", "updated_at"},
    "mdm_audit_events": {"id", "candidate_id", "action", "details", "actor", "created_at"},
}
BASELINE_INDEXES = {
    "mdm_source_records": {"ix_mdm_source_records_import_id"},
    "mdm_candidates": {"ix_mdm_candidates_import_id"},
    "mdm_audit_events": {"ix_mdm_audit_events_candidate_id"},
}
BASELINE_FOREIGN_KEYS = {
    "mdm_source_records": {("import_id", "mdm_imports")},
    "mdm_candidates": {("import_id", "mdm_imports"), ("left_record_id", "mdm_source_records"),
                       ("right_record_id", "mdm_source_records")},
    "mdm_golden_record_drafts": {("candidate_id", "mdm_candidates")},
    "mdm_audit_events": {("candidate_id", "mdm_candidates")},
}
HEAD = "0005_storage_registry"


def alembic_config(url: str, config_path: str | Path | None = None) -> Config:
    ini = Path(config_path).resolve() if config_path else Path.cwd().resolve() / "backend" / "alembic.ini"
    if not ini.is_file():
        raise RuntimeError(f"Alembic config not found at {ini}; run from the repository root")
    cfg = Config(str(ini))
    cfg.set_main_option("script_location", str(ini.parent / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def upgrade_database(url: str) -> None:
    is_sqlite = url.startswith("sqlite:///")
    db_path = url.removeprefix("sqlite:///") if is_sqlite else None
    if db_path and db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url)
    inspector = inspect(engine)
    if is_sqlite:
        with engine.connect() as connection:
            if connection.execute(text("PRAGMA integrity_check")).scalar_one() != "ok":
                raise RuntimeError("SQLite integrity check failed; restore from backup")
        tables = set(inspector.get_table_names()) - {"alembic_version"}
        if tables and "alembic_version" not in inspector.get_table_names():
            if tables != BASELINE.keys():
                raise RuntimeError(f"Unversioned database has unexpected tables: {sorted(tables)}")
            for table, columns in BASELINE.items():
                actual = {item["name"] for item in inspector.get_columns(table)}
                if actual != columns:
                    raise RuntimeError(f"Unversioned database has unexpected columns in {table}: {sorted(actual)}")
                expected_indexes = BASELINE_INDEXES.get(table, set())
                actual_indexes = {item["name"] for item in inspector.get_indexes(table)}
                if not expected_indexes <= actual_indexes:
                    raise RuntimeError(f"Unversioned database is missing indexes in {table}")
                expected_fks = BASELINE_FOREIGN_KEYS.get(table, set())
                actual_fks = {(item["constrained_columns"][0], item["referred_table"])
                              for item in inspector.get_foreign_keys(table)}
                if not expected_fks <= actual_fks:
                    raise RuntimeError(f"Unversioned database is missing foreign keys in {table}")
            command.stamp(alembic_config(url), "0001")
    engine.dispose()
    command.upgrade(alembic_config(url), "head")


def require_current(engine) -> None:
    if not inspect(engine).has_table("alembic_version"):
        raise RuntimeError("MDM database has no migration version. Run: python -m masterone.mdm.migrate from the repository root")
    with engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
    if revision != HEAD:
        raise RuntimeError(f"MDM database is at migration {revision}; expected {HEAD}. Run: python -m masterone.mdm.migrate from the repository root")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=get_settings().mdm_database_url)
    args = parser.parse_args()
    upgrade_database(args.database_url)
