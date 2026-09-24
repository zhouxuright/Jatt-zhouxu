"""Alembic environment configuration — async engine with asyncpg."""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

# ---------------------------------------------------------------------------
# Alembic Config object — provides access to alembic.ini values
# ---------------------------------------------------------------------------
config = context.config

# Interpret the config file for Python logging if present
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ---------------------------------------------------------------------------
# Import all models so Base.metadata is fully populated
# ---------------------------------------------------------------------------
from app.core.database import Base  # noqa: E402
from app.models import (  # noqa: E402, F401
    User, UserRole,
    Conversation,
    Message, MessageRole,
    Feedback,
    Document, DocumentStatus, ContractReview,
    AuditLog,
    UserMemory,
    Tenant,
    Law, LegalArticle, CourtCase, JudicialInterpretation, LegalConcept,
)

# Metadata for 'autogenerate' support
target_metadata = Base.metadata

# ---------------------------------------------------------------------------
# Database URL — override from environment if available, fall back to ini
# ---------------------------------------------------------------------------
# The default URL in alembic.ini matches the .env default.  If the real
# environment supplies a different DATABASE_URL the caller can pass it via
# -x db_url=... or set the env var before invoking alembic.
import os  # noqa: E402
db_url = os.environ.get("DATABASE_URL", config.get_main_option("sqlalchemy.url"))


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    Configures the context with just a URL and not an Engine.  Calls to
    context.execute() here emit the given string to the script output.
    """
    url = db_url
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    """Shared helper used by the online runner."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Create an async Engine and associate it with the context."""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = db_url

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode using an async engine."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
