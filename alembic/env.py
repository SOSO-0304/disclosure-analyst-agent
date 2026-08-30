"""Alembic environment bound to the application's PostgreSQL settings."""

from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool

from disclosure_agent.config import get_settings
from disclosure_agent.storage.fundraising_models import FundraisingEventRow
from disclosure_agent.storage.source_event_models import SourceEventRow

config = context.config
config.set_main_option("sqlalchemy.url", get_settings().database_url)
target_metadata = SourceEventRow.__table__.metadata


def run_migrations_offline() -> None:
    """Run migrations without creating a DB connection."""

    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations using the configured application database."""

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
