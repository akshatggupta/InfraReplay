"""Alembic environment — async engine, metadata from the ORM models."""

import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from infrareplay import config as app_config
from infrareplay.db.models import Base

target_metadata = Base.metadata


def _run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


def run_offline() -> None:
    context.configure(
        url=app_config.DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_online() -> None:
    engine = create_async_engine(app_config.DATABASE_URL)

    async with engine.connect() as connection:
        await connection.run_sync(_run_migrations)

    await engine.dispose()


if context.is_offline_mode():
    run_offline()
else:
    asyncio.run(run_online())
