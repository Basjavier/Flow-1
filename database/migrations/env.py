"""
Alembic migration environment.

Uses a synchronous psycopg2 connection (Alembic doesn't support asyncpg natively).
DATABASE_URL in alembic.ini should use postgresql:// (not postgresql+asyncpg://).
"""
from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Import all models so Alembic detects them in metadata
from database.models import Base  # noqa: F401

config = context.config

if config.config_file_name:
    fileConfig(config.config_file_name)

# Override URL from env if DATABASE_URL is set (psycopg2 form)
env_url = os.getenv("DATABASE_URL_SYNC")
if env_url:
    config.set_main_option("sqlalchemy.url", env_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
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
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
