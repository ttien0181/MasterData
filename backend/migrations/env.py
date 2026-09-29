from alembic import context
from masterone.auth import models as auth_models  # noqa: F401
from masterone.db.base import Base
from masterone.mdm import models  # noqa: F401
from sqlalchemy import engine_from_config, pool

config = context.config
target_metadata = Base.metadata


def run_migrations_online():
    connectable = engine_from_config(config.get_section(config.config_ini_section), prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
