"""Every test gets an isolated sqlite DB and blob root."""

import pytest

from infrareplay import config
from infrareplay.db import init_db, reset_engine


@pytest.fixture(autouse=True)
def _isolated_storage(tmp_path, monkeypatch):
    db_path = tmp_path / "meta.db"
    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setattr(config, "BLOB_ROOT", tmp_path / "blobs")

    reset_engine()
    yield
    reset_engine()


@pytest.fixture
async def seeded():
    """Seed the two fixture recordings, return them."""

    from infrareplay import demo

    await init_db()
    return await demo.seed()
