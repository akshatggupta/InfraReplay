"""The real demo traffic records what it claims to.

Regression tests for three bugs found by running the presentation flow end
to end: the "race" gave its shoppers different baskets and never raced,
ordinary traffic did not restock first, and a declined card kept its stock.
"""

from tests.integration.conftest import client, reset_shop

_CONFIRMED = 201
_DECLINED = 402


async def _traffic(api, shop, **body) -> dict:
    async with client(api) as api_client:
        reply = await api_client.post("/api/demo/traffic", json={"upstream": shop.url, **body})

    return reply.json()


def _client_statuses(recording: dict) -> list[int]:
    return [
        e["payload"]["status"]
        for e in recording["events"]
        if e["event_type"] == "http.response" and e["payload"].get("direction") != "outbound"
    ]


async def test_the_race_really_oversells(stack):
    api, shop = stack

    recording = await _traffic(api, shop, checkouts=2, declined=0, concurrent=True, stock=1)

    async with client(shop) as shop_client:
        ledger = (await shop_client.get("/api/v1/orders")).json()

    # Both shoppers reached for the one unit, and both were sold it.
    assert _client_statuses(recording) == [_CONFIRMED, _CONFIRMED]
    assert ledger["confirmed_orders"] == 2


async def test_ordinary_traffic_restocks_first(stack):
    api, shop = stack
    await reset_shop(shop, stock=1)  # the shop's own seed: one unit of each

    recording = await _traffic(api, shop, checkouts=3, declined=1)

    assert sorted(_client_statuses(recording)) == [_CONFIRMED, _CONFIRMED, _DECLINED]


async def test_no_card_token_is_stored(stack):
    # The demo cards travel twice: in the checkout (`token`) and in the shop's
    # call to its gateway (`source`). Neither copy may reach storage.
    api, shop = stack

    recording = await _traffic(api, shop, checkouts=2, declined=1)

    assert "tok_" not in str([e["payload"] for e in recording["events"]])


async def test_a_declined_card_gives_its_stock_back(stack):
    _, shop = stack
    await reset_shop(shop, stock=5)

    async with client(shop) as shop_client:
        refused = await shop_client.post(
            "/api/v1/checkout",
            json={
                "cart_id": "cart_declined",
                "items": [{"sku": "AEROPRESS-GO", "qty": 2}],
                "payment_method": {"type": "card", "token": "tok_decline_insufficient_funds"},
            },
            headers={"x-demo-user": "dana"},
        )
        stock = {i["sku"]: i["available"] for i in (await shop_client.get("/api/v1/inventory")).json()}

    assert refused.status_code == _DECLINED
    assert stock["AEROPRESS-GO"] == 5
