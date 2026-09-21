"""v2 + v3 DoD: a real request, proxied and recorded, with its real SQL.

Both halves run for real — an InfraReplay API on a port, the demo shop on
another, the capture proxy on a third — so nothing here is mocked except the
clock everyone shares.
"""

from infrareplay.plugins.redaction_default.plugin import REDACTED
from tests.integration.conftest import CHECKOUT as _CHECKOUT
from tests.integration.conftest import HEADERS as _HEADERS
from tests.integration.conftest import capture, client


async def test_proxy_records_request_response_and_sql(stack):
    api, shop = stack

    recording = await capture(api, shop)
    events = recording["events"]
    types = [e["event_type"] for e in events]

    assert recording["status"] == "completed"
    assert types[0] == "http.request"
    assert types[-1] == "http.response"
    assert "postgres.query" in types

    # v3: every event of the request shares one correlation id, in order.
    assert len({e["correlation_id"] for e in events}) == 1
    assert [e["sequence"] for e in events] == list(range(len(events)))

    # DB work hangs off the request that caused it.
    request_id = events[0]["event_id"]
    queries = [e for e in events if e["event_type"] == "postgres.query"]

    assert len(queries) >= 2
    assert all(q["parent_event_id"] == request_id for q in queries)

    # v2: redaction applies to really captured traffic, not just fixtures.
    assert events[0]["payload"]["headers"]["authorization"] == REDACTED
    assert events[-1]["payload"]["status"] == 201


async def test_capture_is_off_until_a_session_starts(stack):
    api, shop = stack

    async with client(shop) as direct:
        response = await direct.post("/api/v1/checkout", json=_CHECKOUT, headers=_HEADERS)

    assert response.status_code == 201

    async with client(api) as api_client:
        assert (await api_client.get("/api/recordings")).json() == []
