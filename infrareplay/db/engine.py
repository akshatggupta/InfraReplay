"""Async engine + session factory, rebuildable so tests can point elsewhere."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from infrareplay import config
from infrareplay.db.models import Base

_engine = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None

# Tables are checked once per engine; services call init_db() defensively.
_initialised = False


def _ensure() -> async_sessionmaker[AsyncSession]:
    global _engine, _sessionmaker

    if _sessionmaker is None:
        _engine = _create(config.DATABASE_URL)
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)

    return _sessionmaker


# SQLite's default lock wait is 5 s; under the v10 heavy benchmark writes hit
# it and failed with "database is locked".
_SQLITE_BUSY_TIMEOUT_S = 30


def _create(url: str):
    if not url.startswith("sqlite"):
        return create_async_engine(url, future=True)

    engine = create_async_engine(url, future=True, connect_args={"timeout": _SQLITE_BUSY_TIMEOUT_S})

    # WAL: readers stop blocking the writer and the writer stops blocking
    # readers — capture writes and dashboard reads arrive at the same time.
    @event.listens_for(engine.sync_engine, "connect")
    def _wal(dbapi_connection, _record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    return engine


def reset_engine() -> None:
    """Drop cached engine/sessionmaker. Used by tests after changing config."""

    global _engine, _sessionmaker, _initialised

    _engine = None
    _sessionmaker = None
    _initialised = False


async def init_db() -> None:
    """Create tables if absent.

    Alembic (`migrations/`) owns schema *changes*; this bootstraps a fresh
    database so `docker compose up` and the test suite need no extra step.
    """

    global _initialised

    _ensure()
    assert _engine is not None

    if _initialised:
        return

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    _initialised = True


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    factory = _ensure()

    async with factory() as session:
        yield session
