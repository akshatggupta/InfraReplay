"""v4 + v5 + v6 DoD: replay a real capture at a live target and diff it.

v4  a really captured request is replayed against a running service
v5  replaying twice against an unchanged service matches both times
v6  the demo shop's oversell bug is reproduced on replay, then the fix shows
"""

from tests.integration.conftest import capture, client, replay, reset_shop

_CONFIRMED = 201
_CONFLICT = 409


def _statuses(recording: dict) -> list[int]:
    return [
        e["payload"].get("status")
        for e in recording["events"]
        if e["event_type"] == "http.response"
    ]


async def _recording(api, recording_id: str) -> dict:
    async with client(api) as api_client:
        return (await api_client.get(f"/api/recordings/{recording_id}")).json()


async def test_replay_against_live_target_captures_its_sql(stack):
    api, shop = stack

    recorded = await capture(api, shop)
    await reset_shop(shop, stock=5)

    summary = await replay(api, recorded["recording_id"], shop.url)
    run = await _recording(api, summary["replay_recording_id"])

    assert summary["summary"]["MATCH"] >= 1
    assert summary["summary"]["ERROR"] == 0

    # The replay is a real recording: its own HTTP *and* the SQL the target
    # ran, streamed back by the agent while the replay was in flight.
    types = {e["event_type"] for e in run["events"]}
    assert types == {"http.request", "http.response", "postgres.query", "postgres.result"}
    assert _statuses(run) == [_CONFIRMED]


async def test_replaying_twice_is_stable(stack):
    api, shop = stack

    recorded = await capture(api, shop)
    await reset_shop(shop, stock=5)

    first = await replay(api, recorded["recording_id"], shop.url)
    second = await replay(api, recorded["recording_id"], shop.url)

    # Fresh order ids and payment references every run — and still no diff.
    assert first["summary"]["DIFFERENT"] == 0
    assert second["summary"]["DIFFERENT"] == 0
    assert second["summary"]["MATCH"] == first["summary"]["MATCH"]


async def test_oversell_is_reproduced_then_fixed(stack, monkeypatch):
    api, shop = stack

    # One unit in stock, two shoppers at once: both get an order.
    recorded = await capture(api, shop, requests=2, concurrent=True)
    assert _statuses(recorded) == [_CONFIRMED, _CONFIRMED]

    async with client(shop) as shop_client:
        ledger = (await shop_client.get("/api/v1/orders")).json()

    assert ledger == {"confirmed_orders": 2, "stock_left": 1}  # 1 unit sold twice

    await reset_shop(shop, stock=1)
    reproduced = await replay(api, recorded["recording_id"], shop.url, concurrency=2)

    assert reproduced["summary"]["DIFFERENT"] == 0  # the bug is still there

    monkeypatch.setenv("DEMO_SHOP_LOCK_INVENTORY", "1")
    await reset_shop(shop, stock=1)
    fixed = await replay(api, recorded["recording_id"], shop.url, concurrency=2)

    assert fixed["summary"]["DIFFERENT"] >= 1
    assert sorted(_statuses(await _recording(api, fixed["replay_recording_id"]))) == [
        _CONFIRMED,
        _CONFLICT,
    ]
