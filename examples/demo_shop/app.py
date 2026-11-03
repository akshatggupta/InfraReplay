"""The demo shop service — the app InfraReplay records, replays and diffs.

    uvicorn examples.demo_shop.app:app --port 3000

Environment:
    DEMO_SHOP_DATABASE_URL     default sqlite+aiosqlite:///./demo_shop.db
    DEMO_SHOP_LOCK_INVENTORY   "1" switches the reservation bug off
    DEMO_SHOP_RACE_WINDOW_S    how wide the read-then-write window is
    DEMO_SHOP_PAYMENTS_URL     the payment gateway (default :4000)
    DEMO_SHOP_PAYMENTS_KEY     the shop's API key at the gateway
    INFRAREPLAY_API_URL        where captured SQL is streamed

The inventory fix can also be flipped while the shop runs — a feature flag,
so a presenter can replay the bug, switch the fix on, and replay again:

    POST /api/v1/admin/inventory-lock?enabled=true

The app is always instrumented; the agent emits nothing until a request
arrives carrying InfraReplay's capture headers.
"""

import os
from contextlib import asynccontextmanager

import httpx
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
_DEFAULT_PAYMENTS_URL = "http://127.0.0.1:4000"
_DEFAULT_PAYMENTS_KEY = "gw_test_key_demo"
_PAYMENTS_TIMEOUT_S = 10
_SQLITE_BUSY_TIMEOUT_S = 30


def database_url() -> str:
    return os.environ.get("DEMO_SHOP_DATABASE_URL", _DEFAULT_DATABASE_URL)


def payments_url() -> str:
    return os.environ.get("DEMO_SHOP_PAYMENTS_URL", _DEFAULT_PAYMENTS_URL)


def payments_key() -> str:
    return os.environ.get("DEMO_SHOP_PAYMENTS_KEY", _DEFAULT_PAYMENTS_KEY)


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
        await payments.aclose()
        await agent.stop()

    app = FastAPI(title="demo_shop", version="1.0.0", lifespan=lifespan)
    agent = instrument(app, engine, service=SERVICE)

    # The gateway client sits on the agent's transport: its calls are
    # recorded during a capture and answered from the recording on replay.
    payments = httpx.AsyncClient(
        base_url=payments_url(),
        transport=agent.http_transport(),
        headers={"authorization": f"Bearer {payments_key()}"},
        timeout=_PAYMENTS_TIMEOUT_S,
    )

    # None: follow DEMO_SHOP_LOCK_INVENTORY. Set by the admin flag below.
    lock: dict[str, bool | None] = {"enabled": None}

    def mode() -> Reserve:
        if lock["enabled"] is None:
            return reserve_mode()

        return Reserve.ATOMIC if lock["enabled"] else Reserve.READ_THEN_WRITE

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "reserve": mode().value, "payments": payments_url()}

    @app.post("/api/v1/admin/inventory-lock")
    async def inventory_lock(enabled: bool) -> dict:
        """The fix as a runtime flag: no restart between "bug" and "fixed"."""

        lock["enabled"] = enabled

        return {"reserve": mode().value}

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

            return {"reset": True, "stock": stock, "reserve": mode().value}

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
                reserve=mode(),
                race_window_s=race_window_s(),
                payments=payments,
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
