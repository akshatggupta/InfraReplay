"""The demo shop service — the app InfraReplay records, replays and diffs.

    uvicorn examples.demo_shop.app:app --port 3000

Environment:
    DEMO_SHOP_DATABASE_URL     default sqlite+aiosqlite:///./demo_shop.db
    DEMO_SHOP_LOCK_INVENTORY   "1" switches the reservation bug off
    DEMO_SHOP_RACE_WINDOW_S    how wide the read-then-write window is
    INFRAREPLAY_API_URL        where captured SQL is streamed

The app is always instrumented; the agent emits nothing until a request
arrives carrying InfraReplay's capture headers.
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from examples.demo_shop.models import (
    SEED_INVENTORY,
    SEED_STOCK,
    SEED_USER,
    Base,
    Inventory,
    Order,
    OrderItem,
    Payment,
    User,
)
from examples.demo_shop.store import HTTP_UNAUTHORIZED, Reserve, checkout
from infrareplay.agent import instrument

_DEFAULT_DATABASE_URL = "sqlite+aiosqlite:///./demo_shop.db"
SERVICE = "demo_shop"

_USER_HEADER = "x-demo-user"
_SQLITE_BUSY_TIMEOUT_S = 30


def database_url() -> str:
    return os.environ.get("DEMO_SHOP_DATABASE_URL", _DEFAULT_DATABASE_URL)


def race_window_s() -> float:
    return float(os.environ.get("DEMO_SHOP_RACE_WINDOW_S", "0.05"))


def reserve_mode() -> Reserve:
    fixed = os.environ.get("DEMO_SHOP_LOCK_INVENTORY") == "1"

    return Reserve.ATOMIC if fixed else Reserve.READ_THEN_WRITE


def _engine():
    url = database_url()
    args = {"timeout": _SQLITE_BUSY_TIMEOUT_S} if url.startswith("sqlite") else {}

    return create_async_engine(url, connect_args=args)


def create_app() -> FastAPI:
    engine = _engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with sessions() as session:
            await _seed(session, SEED_STOCK)

        await agent.start()
        yield
        await agent.stop()

    app = FastAPI(title="demo_shop", version="1.0.0", lifespan=lifespan)
    agent = instrument(app, engine, service=SERVICE)

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "reserve": reserve_mode().value}

    @app.get("/api/v1/inventory")
    async def inventory() -> list[dict]:
        async with sessions() as session:
            rows = (await session.scalars(select(Inventory))).all()

            return [
                {"sku": r.sku, "name": r.name, "unit_cents": r.unit_cents, "available": r.available}
                for r in rows
            ]

    @app.get("/api/v1/orders")
    async def orders() -> dict:
        """Confirmed orders vs stock left — how an oversell shows up."""

        async with sessions() as session:
            confirmed = await session.scalar(
                select(func.count()).select_from(Order).where(Order.status == "confirmed")
            )
            stock = await session.scalar(select(func.sum(Inventory.available)))

            return {"confirmed_orders": confirmed, "stock_left": stock}

    @app.post("/api/v1/reset")
    async def reset(stock: int = SEED_STOCK) -> dict:
        async with sessions() as session:
            await _seed(session, stock, wipe=True)

            return {"reset": True, "stock": stock, "reserve": reserve_mode().value}

    @app.post("/api/v1/checkout")
    async def create_checkout(request: Request) -> JSONResponse:
        handle = request.headers.get(_USER_HEADER, "")

        if not handle:
            return JSONResponse(
                {"error": "missing_user_header", "header": _USER_HEADER},
                status_code=HTTP_UNAUTHORIZED,
            )

        body = await request.json()

        async with sessions() as session:
            status, payload = await checkout(
                session,
                handle=handle,
                body=body,
                reserve=reserve_mode(),
                race_window_s=race_window_s(),
            )

        return JSONResponse(payload, status_code=status)

    return app


async def _seed(session, stock: int, *, wipe: bool = False) -> None:
    if wipe:
        for table in (Payment, OrderItem, Order, Inventory, User):
            await session.execute(delete(table))

    elif await session.scalar(select(func.count()).select_from(Inventory)):
        return

    session.add(
        User(
            id=SEED_USER.id,
            email=SEED_USER.email,
            name=SEED_USER.name,
            handle=SEED_USER.handle,
        )
    )

    for sku, name, unit_cents in SEED_INVENTORY:
        session.add(Inventory(sku=sku, name=name, unit_cents=unit_cents, available=stock))

    await session.commit()


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("DEMO_SHOP_PORT", "3000")))
