"""Real demo traffic: checkouts driven through a live capture, no fixtures.

    POST /api/demo/traffic
        a capture proxy starts in front of the shop, real checkouts go through
        it, the capture stops, and the recording comes back

Everything in the result is real: the HTTP exchange, every SQL statement the
shop ran, and the shop's call to its payment gateway. The only scripted part
is the customer — something has to press "buy".

The oversell race is the same call with two concurrent checkouts and the
shop's stock reset to one unit first.
"""

import asyncio
from uuid import uuid4

import httpx

from infrareplay.capture import start_session, stop_session
from infrareplay.schema import Recording

_CHECKOUT_PATH = "/api/v1/checkout"
_RESET_PATH = "/api/v1/reset"
_LOOPBACK = "127.0.0.1"
_TIMEOUT_S = 30

# A session-style user header plus a bearer token the capture must mask.
_HEADERS = {
    "x-demo-user": "dana",
    "authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJkYW5hIn0.c2Vzc2lvbi10b2tlbg",
}

_GOOD_CARD = "tok_visa_4242"
_DECLINED_CARD = "tok_decline_insufficient_funds"

_ITEMS = (
    [{"sku": "AEROPRESS-GO", "qty": 1}],
    [{"sku": "AEROPRESS-GO", "qty": 1}, {"sku": "FILTER-350", "qty": 2}],
    [{"sku": "FILTER-350", "qty": 1}],
)


async def record_traffic(
    *,
    upstream: str,
    title: str,
    checkouts: int,
    declined: int,
    concurrent: bool,
    stock: int | None,
) -> Recording:
    """Capture `checkouts` real purchases, the last `declined` of them refused."""

    if stock is not None:
        await _reset(upstream, stock)

    session = await start_session(
        plugin_name="http_capture",
        title=title,
        config={"upstream": upstream, "listen_port": 0, "listen_host": _LOOPBACK},
    )
    proxy = f"http://{_LOOPBACK}:{session.describe()['listen_port']}"

    # Concurrent shoppers all reach for the same single unit — that is the
    # race. Sequential ones get varied baskets, like ordinary traffic.
    bodies = [
        _checkout(0 if concurrent else i, refused=i >= checkouts - declined)
        for i in range(checkouts)
    ]

    try:
        async with httpx.AsyncClient(base_url=proxy, timeout=_TIMEOUT_S) as client:
            calls = [client.post(_CHECKOUT_PATH, json=body, headers=_HEADERS) for body in bodies]

            if concurrent:
                await asyncio.gather(*calls)
            else:
                for call in calls:
                    await call
    finally:
        recording = await stop_session(session.recording_id)

    return recording


async def _reset(upstream: str, stock: int) -> None:
    """Straight to the shop, around the proxy: setup is not part of the recording."""

    async with httpx.AsyncClient(base_url=upstream, timeout=_TIMEOUT_S) as client:
        response = await client.post(_RESET_PATH, params={"stock": stock})
        response.raise_for_status()


def _checkout(index: int, *, refused: bool) -> dict:
    return {
        "cart_id": f"cart_{uuid4().hex[:8]}",
        "items": _ITEMS[index % len(_ITEMS)],
        "payment_method": {
            "type": "card",
            "token": _DECLINED_CARD if refused else _GOOD_CARD,
            "last4": "0002" if refused else "4242",
        },
    }
