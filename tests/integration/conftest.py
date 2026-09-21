"""A live stack: the InfraReplay API and the demo shop, both on real ports."""

import asyncio

import httpx
import pytest

from infrareplay import config
from infrareplay.api import create_app
from tests.support.live import LiveServer

CHECKOUT = {
    "cart_id": "cart_7b21f0",
    "items": [{"sku": "AEROPRESS-GO", "qty": 1}],
    "payment_method": {"type": "card", "token": "tok_live_x2Kd9pQ", "last4": "4242"},
}

HEADERS = {
    "x-demo-user": "dana",
    "authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.checkout.redact-me",
}

_TIMEOUT_S = 30


@pytest.fixture
async def stack(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_SHOP_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/shop.db")
    monkeypatch.setenv("DEMO_SHOP_RACE_WINDOW_S", "0.05")
    monkeypatch.delenv("DEMO_SHOP_LOCK_INVENTORY", raising=False)

    async with LiveServer(create_app()) as api:
        monkeypatch.setattr(config, "API_URL", api.url)

        from examples.demo_shop.app import create_app as create_shop

        async with LiveServer(create_shop()) as shop:
            yield api, shop


def client(server: LiveServer) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=server.url, timeout=_TIMEOUT_S)


async def capture(api, shop, *, requests: int = 1, concurrent: bool = False) -> dict:
    """Record `requests` checkouts through a live capture proxy."""

    async with client(api) as api_client:
        started = (
            await api_client.post(
                "/api/captures",
                json={
                    "plugin": "http_capture",
                    "title": "live checkout",
                    "config": {
                        "listen_port": 0,
                        "listen_host": "127.0.0.1",
                        "upstream": shop.url,
                    },
                },
            )
        ).json()

        proxy = f"http://127.0.0.1:{started['listen_port']}"

        async with httpx.AsyncClient(base_url=proxy, timeout=_TIMEOUT_S) as through:
            calls = [
                through.post("/api/v1/checkout", json=CHECKOUT, headers=HEADERS)
                for _ in range(requests)
            ]

            if concurrent:
                await asyncio.gather(*calls)
            else:
                for call in calls:
                    await call

        return (
            await api_client.post(f"/api/captures/{started['recording_id']}/stop")
        ).json()


async def replay(api, recording_id: str, target: str, **options) -> dict:
    async with client(api) as api_client:
        response = await api_client.post(
            "/api/replays",
            json={"recording_id": recording_id, "target": target, **options},
        )

        return response.json()


async def reset_shop(shop, stock: int = 1) -> None:
    async with client(shop) as shop_client:
        await shop_client.post("/api/v1/reset", params={"stock": stock})
