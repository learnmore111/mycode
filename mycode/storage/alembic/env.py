"""Alembic environment for mycode.

Wires the SQLAlchemy URL to the same ``get_db_path()`` helper used by
runtime so one database path flows through both.

Usage::

    export MYCODE_ALEMBIC=1
    alembic -c alembic.ini revision --autogenerate -m "add foo"
    alembic -c alembic.ini upgrade head

The existing in-code ``_migrate()`` hook in ``mycode/storage/database.py``
is intentionally left in place: deployments that do not enable Alembic
still get additive migrations on startup.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from mycode.storage.database import get_db_path
from mycode.storage.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Resolve the SQLAlchemy URL. Callers that explicitly set
# ``sqlalchemy.url`` via ``Config.set_main_option`` (tests, one-off
# invocations against a specific DB file) win; otherwise fall back to
# the project's canonical ``get_db_path()`` helper.
resolved_url = config.get_main_option("sqlalchemy.url")
_placeholder = "driver://user:pass@localhost/dbname"
if not resolved_url or resolved_url == _placeholder:
    db_path = get_db_path()
    resolved_url = f"sqlite:///{db_path}"
    config.set_main_option("sqlalchemy.url", resolved_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,  # needed for SQLite ALTER
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
