"""v11 DoD: a replay answers the shop's payment call from the recording.

The payment gateway's own ledger is the witness. It counts every charge it
ever accepted, so "the replay charged nobody" is a number, not a claim.
"""

from infrareplay.plugins.redaction_default.plugin import REDACTED
from tests.integration.conftest import capture, client, replay, reset_shop

_OUTBOUND = "outbound"


async def _ledger(gateway) -> int:
    async with client(gateway) as gateway_client:
        return (await gateway_client.get("/v1/charges")).json()["count"]


def _outbound(recording: dict) -> list[dict]:
    return [e for e in recording["events"] if e["payload"].get("direction") == _OUTBOUND]


async def _recording(api, recording_id: str) -> dict:
    async with client(api) as api_client:
        return (await api_client.get(f"/api/recordings/{recording_id}")).json()


async def test_capture_records_the_payment_call(stack, gateway):
    api, shop = stack

    recorded = await capture(api, shop)
    inbound = recorded["events"][0]
    request, response = _outbound(recorded)

    assert await _ledger(gateway) == 1

    # The call, tied to the checkout that made it.
    assert request["payload"]["method"] == "POST"
    assert request["payload"]["path"] == "/v1/charges"
    assert request["parent_event_id"] == inbound["event_id"]
    assert response["parent_event_id"] == request["event_id"]
    assert response["payload"]["status"] == 200

    # The shop's gateway API key never reaches storage.
    assert request["payload"]["headers"]["authorization"] == REDACTED


async def test_replay_never_charges_the_card_again(stack, gateway):
    api, shop = stack

    recorded = await capture(api, shop)
    charges = await _ledger(gateway)

    await reset_shop(shop, stock=5)
    result = await replay(api, recorded["recording_id"], shop.url)
    run = await _recording(api, result["replay_recording_id"])

    assert await _ledger(gateway) == charges  # nobody was charged again
    assert result["summary"]["DIFFERENT"] == 0
    assert result["summary"]["ERROR"] == 0

    _, stubbed = _outbound(run)
    assert stubbed["payload"]["stubbed"] is True
    assert stubbed["payload"]["status"] == 200


async def test_live_external_calls_need_an_explicit_ask(stack, gateway):
    api, shop = stack

    recorded = await capture(api, shop)
    charges = await _ledger(gateway)

    await reset_shop(shop, stock=5)
    await replay(api, recorded["recording_id"], shop.url, externals="live")

    assert await _ledger(gateway) == charges + 1
