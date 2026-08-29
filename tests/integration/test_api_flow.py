"""The v1 DoD, exercised through the HTTP API the CLI and dashboard use."""

import httpx
import pytest

from infrareplay.api import create_app


@pytest.fixture
async def client():
    app = create_app()

    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as c:
        # Trigger lifespan (init_db + registry).
        async with app.router.lifespan_context(app):
            yield c


async def test_seed_list_show_replay_compare(client: httpx.AsyncClient):
    seeded = (await client.post("/api/demo/seed")).json()
    assert len(seeded) == 2

    listing = (await client.get("/api/recordings")).json()
    assert len(listing) == 2

    buggy = next(r for r in seeded if "fails" in r["title"])
    detail = (await client.get(f"/api/recordings/{buggy['recording_id']}")).json()
    assert len(detail["events"]) == 14

    replay = (
        await client.post(
            "/api/replays", json={"recording_id": buggy["recording_id"], "target": "mock"}
        )
    ).json()

    assert replay["summary"]["DIFFERENT"] >= 1
    assert replay["summary"]["MATCH"] >= 1

    comparison = (
        await client.get(
            f"/api/replays/{replay['replay_recording_id']}/comparison"
        )
    ).json()

    categories = {row["category"] for row in comparison}
    assert "MATCH" in categories
    assert "DIFFERENT" in categories

    replays = (await client.get("/api/replays")).json()
    assert any(r["recording_id"] == replay["replay_recording_id"] for r in replays)


async def test_replay_missing_recording_is_400(client: httpx.AsyncClient):
    resp = await client.post(
        "/api/replays", json={"recording_id": "rec_nope", "target": "mock"}
    )
    assert resp.status_code == 400
