import os

from alembic import context
from masterone.auth import models as auth_models  # noqa: F401
from masterone.db.base import Base
from masterone.mdm import (
    models,  # noqa: F401
    workflow_models,  # noqa: F401
)
from sqlalchemy import engine_from_config, pool

config = context.config
target_metadata = Base.metadata


def run_migrations_online():
    supplied = config.attributes.get('connection')
    if supplied is not None:
        context.configure(connection=supplied, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
        return
    # Explicit upgrade_database(url)/CLI target wins over ambient environment.
    url = config.get_main_option("sqlalchemy.url") or os.environ.get("MDM_DATABASE_URL")
    if not url:
        raise RuntimeError("MDM_DATABASE_URL must be set")
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args={"connect_timeout": 5} if url.startswith("postgresql") else {},
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
