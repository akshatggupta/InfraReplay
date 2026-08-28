"""Async engine + session factory, rebuildable so tests can point elsewhere."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from infrareplay import config
from infrareplay.db.models import Base

_engine = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _ensure() -> async_sessionmaker[AsyncSession]:
    global _engine, _sessionmaker

    if _sessionmaker is None:
        _engine = create_async_engine(config.DATABASE_URL, future=True)
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)

    return _sessionmaker


def reset_engine() -> None:
    """Drop cached engine/sessionmaker. Used by tests after changing config."""

    global _engine, _sessionmaker

    _engine = None
    _sessionmaker = None


async def init_db() -> None:
    """Create tables if absent.

    Alembic (`migrations/`) owns schema *changes*; this bootstraps a fresh
    database so `docker compose up` and the test suite need no extra step.
    """

    _ensure()
    assert _engine is not None

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    factory = _ensure()

    async with factory() as session:
        yield session
