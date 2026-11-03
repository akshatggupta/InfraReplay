"""v11: outbound calls recorded on capture, answered from the recording on replay.

The real service is an httpx.MockTransport that counts its calls, so every
test can say exactly how many times the outside world was reached.
"""

import json

import httpx
import pytest

from infrareplay.agent.egress import HTTP_SERVICE_UNAVAILABLE, CapturingTransport, call_key
from infrareplay.capture import context

_URL = "http://gateway.test:4000/v1/charges"
_KEY = call_key("POST", "gateway.test:4000", "/v1/charges")


class _Gateway:
    """Stands in for the payment provider and counts what reached it."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        return httpx.Response(200, json={"id": f"ch_live_{self.calls}", "status": "succeeded"})


@pytest.fixture
def harness():
    gateway = _Gateway()
    recorded: list = []
    lookups: list = []

    async def lookup(recording_id: str, correlation_id: str) -> list[dict]:
        lookups.append((recording_id, correlation_id))
        return [
            {
                "key": _KEY,
                "response": {
                    "status": 200,
                    "headers": {"content-type": "application/json"},
                    "body": {"id": "ch_recorded_1", "status": "succeeded"},
                },
            }
        ]

    transport = CapturingTransport(
        record=recorded.extend,
        lookup=lookup,
        service="demo_shop",
        inner=httpx.MockTransport(gateway),
    )

    return transport, gateway, recorded, lookups


async def _charge(transport: CapturingTransport) -> httpx.Response:
    async with httpx.AsyncClient(transport=transport) as client:
        return await client.post(_URL, json={"amount_cents": 4978, "source": "tok_visa"})


async def test_nothing_is_recorded_outside_a_capture(harness):
    transport, gateway, recorded, _ = harness

    response = await _charge(transport)

    assert response.json()["id"] == "ch_live_1"
    assert gateway.calls == 1
    assert recorded == []


async def test_capture_records_the_call_and_its_answer(harness):
    transport, gateway, recorded, _ = harness
    token = context.bind("rec_1", "corr_1")

    try:
        response = await _charge(transport)
    finally:
        context.reset(token)

    request, reply = recorded

    assert gateway.calls == 1
    assert response.status_code == 200
    assert request.payload["direction"] == "outbound"
    assert request.payload["key"] == _KEY
    assert request.payload["body"] == {"amount_cents": 4978, "source": "tok_visa"}
    assert reply.parent_event_id == request.event_id
    assert reply.payload["status"] == 200
    assert reply.payload["stubbed"] is False
    assert {e.correlation_id for e in recorded} == {"corr_1"}


async def test_replay_answers_from_the_recording_and_never_calls_out(harness):
    transport, gateway, recorded, lookups = harness
    token = context.bind("rpl_1", "corr_new", replay_of="rec_1", source_correlation="corr_1")

    try:
        response = await _charge(transport)
    finally:
        context.reset(token)

    assert gateway.calls == 0  # the card was not charged again
    assert response.json() == {"id": "ch_recorded_1", "status": "succeeded"}
    assert lookups == [("rec_1", "corr_1")]
    assert recorded[1].payload["stubbed"] is True


async def test_replay_fails_closed_when_nothing_was_recorded(harness):
    transport, gateway, recorded, _ = harness
    token = context.bind("rpl_1", "corr_new", replay_of="rec_1", source_correlation="corr_1")

    try:
        async with httpx.AsyncClient(transport=transport) as client:
            await client.post(_URL, json={"amount_cents": 1})          # the one recorded answer
            second = await client.post(_URL, json={"amount_cents": 1})  # a call the original never made
    finally:
        context.reset(token)

    assert gateway.calls == 0
    assert second.status_code == HTTP_SERVICE_UNAVAILABLE
    assert json.loads(second.content)["error"] == "external_call_not_recorded"


async def test_live_externals_must_be_asked_for(harness):
    transport, gateway, _, _ = harness
    token = context.bind(
        "rpl_1", "corr_new", replay_of="rec_1", source_correlation="corr_1",
        externals=context.ExternalsMode.LIVE,
    )

    try:
        response = await _charge(transport)
    finally:
        context.reset(token)

    assert gateway.calls == 1
    assert response.json()["id"] == "ch_live_1"
