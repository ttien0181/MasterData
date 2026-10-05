"""Render the current SQLAlchemy schema for an empty Supabase PostgreSQL project."""

from pathlib import Path

from masterone.auth import models as auth_models  # noqa: F401
from masterone.db.base import Base
from masterone.mdm import models as mdm_models  # noqa: F401
from masterone.mdm.migrate import HEAD
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

DEFAULTS = {
    "mdm_resolution_runs": {"status": "UPLOADED", "phase": "upload", "policy_version": "person-v1",
                            "record_count": "0", "file_count": "0", "candidate_count": "0",
                            "insufficient_count": "0"},
    "mdm_resolution_explanations": {"status": "PENDING"},
    "mdm_resolution_proposals": {"status": "AUTO_READY", "revision": "1", "origin": "AUTO"},
    "mdm_resolution_review_cases": {"status": "OPEN", "revision": "1", "signature_hash": ""},
    "mdm_resolution_tasks": {"status": "QUEUED", "phase": "queued", "processed_count": "0",
                             "total_count": "0"},
    "mdm_resolution_pairs": {"user_decision": "UNDECIDED", "revision": "1",
                             "normalization_tags": "[]"},
    "mdm_resolution_previews": {"status": "OPEN", "acknowledge_unreviewed": "FALSE"},
}


def main() -> None:
    dialect = postgresql.dialect()
    tables = Base.metadata.sorted_tables
    lines = [
        "-- MasterOne empty schema for Supabase PostgreSQL. Run once in SQL Editor.",
        "-- Generated from SQLAlchemy models for a trusted backend PostgreSQL connection.",
        "-- Application users are separate from Supabase auth.users.",
        "-- No data, passwords, sessions or CSV files are imported.",
        "-- RLS is enabled without Data API policies; anon/authenticated/service_role grants are revoked.",
        "BEGIN;",
        "SET LOCAL search_path = public;",
    ]
    for table in tables:
        lines.append(str(CreateTable(table).compile(dialect=dialect)).strip() + ";")
    for table in tables:
        for index in sorted(table.indexes, key=lambda item: item.name):
            lines.append(str(CreateIndex(index).compile(dialect=dialect)) + ";")
    for table, defaults in DEFAULTS.items():
        for column, value in defaults.items():
            literal = value if value.isdecimal() or value in {"TRUE", "FALSE"} else "'" + value.replace("'", "''") + "'"
            lines.append(f"ALTER TABLE public.{table} ALTER COLUMN {column} SET DEFAULT {literal};")
    lines.extend([
        "CREATE TABLE public.alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY);",
        f"INSERT INTO public.alembic_version (version_num) VALUES ('{HEAD}');",
    ])
    for table in tables:
        lines.append(f"ALTER TABLE public.{table.name} ENABLE ROW LEVEL SECURITY;")
        lines.append(f"REVOKE ALL PRIVILEGES ON TABLE public.{table.name} FROM anon, authenticated, service_role;")
    lines.extend([
        "ALTER TABLE public.alembic_version ENABLE ROW LEVEL SECURITY;",
        "REVOKE ALL PRIVILEGES ON TABLE public.alembic_version FROM anon, authenticated, service_role;",
        "COMMIT;",
    ])
    target = Path(__file__).resolve().parents[1] / "supabase" / "masterone_schema.sql"
    target.parent.mkdir(parents=True, exist_ok=True)
    rendered = "\n\n".join(lines)
    target.write_text("\n".join(line.rstrip() for line in rendered.splitlines()) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
