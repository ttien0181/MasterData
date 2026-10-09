"""Initialize or upgrade the MDM database (PostgreSQL)."""

import argparse
from pathlib import Path

from alembic import command
from alembic.config import Config

from masterone.config import get_settings

HEAD = "0020_phase5_entities_and_edges"


def alembic_config(url: str, config_path: str | Path | None = None) -> Config:
    if config_path:
        ini = Path(config_path).resolve()
    else:
        # Search: repo-root/backend/alembic.ini, backend/alembic.ini, or alongside this file
        candidates = [
            Path.cwd().resolve() / "backend" / "alembic.ini",
            Path.cwd().resolve() / "alembic.ini",
            Path(__file__).resolve().parents[4] / "backend" / "alembic.ini",
        ]
        ini = next((p for p in candidates if p.is_file()), None)
        if ini is None:
            raise RuntimeError(
                f"alembic.ini not found; searched {[str(p) for p in candidates]}"
            )
    cfg = Config(str(ini))
    cfg.set_main_option("script_location", str(ini.parent / "migrations"))
    # ConfigParser interpolates %, including URL-encoded passwords and paths.
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def upgrade_database(url: str) -> None:
    command.upgrade(alembic_config(url), "head")


def require_current(engine) -> None:
    """Auto-upgrade to head on startup."""
    from sqlalchemy import inspect, text

    url = engine.url.render_as_string(hide_password=False)
    inspector = inspect(engine)
    if not inspector.has_table("alembic_version"):
        upgrade_database(url)
        return
    with engine.connect() as connection:
        revision = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one_or_none()
    if revision != HEAD:
        upgrade_database(url)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=get_settings().active_mdm_database_url)
    args = parser.parse_args()
    upgrade_database(args.database_url)
