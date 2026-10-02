"""Alembic environment (async): migrations run through asyncpg, the same driver as the app."""
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # make `app` importable

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.config import DATABASE_URL
from app.db import Base
import app.models  # noqa: F401  (registers the uploads table on Base.metadata)

logging.basicConfig(level=logging.WARNING)
logging.getLogger("alembic").setLevel(logging.INFO)

config = context.config
target_metadata = Base.metadata


def _async_url() -> str:
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set")
    for prefix in ("postgres://", "postgresql://"):
        if DATABASE_URL.startswith(prefix):
            return "postgresql+asyncpg://" + DATABASE_URL[len(prefix):]
    return DATABASE_URL


def run_migrations_offline() -> None:
    context.configure(url=_async_url(), target_metadata=target_metadata,
                      literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _async_url()
    connectable = async_engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
