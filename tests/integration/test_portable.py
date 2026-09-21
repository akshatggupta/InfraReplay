"""v7 DoD: a recording leaves as one file and replays somewhere else."""

import httpx
import pytest

from infrareplay.api import create_app


@pytest.fixture
async def client():
    app = create_app()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        async with app.router.lifespan_context(app):
            yield c


async def test_export_validate_import_then_replay(client: httpx.AsyncClient):
    seeded = (await client.post("/api/demo/seed")).json()
    original = seeded[0]["recording_id"]

    bundle = (await client.get(f"/api/recordings/{original}/export")).json()

    assert bundle["bundle_version"] == 1
    assert len(bundle["events"]) == 14

    checked = (await client.post("/api/recordings/validate", json=bundle)).json()
    assert checked == {
        "valid": True,
        "recording_id": original,
        "events": 14,
        "schema_version": 1,
    }

    # Importing next to the original re-keys instead of colliding.
    imported = (await client.post("/api/recordings/import", json=bundle)).json()

    assert imported["recording_id"] != original
    assert imported["status"] == "completed"
    assert len(imported["events"]) == 14

    replayed = (
        await client.post(
            "/api/replays", json={"recording_id": imported["recording_id"], "target": "mock"}
        )
    ).json()

    assert replayed["summary"]["MATCH"] >= 1


async def test_garbage_is_rejected_with_a_reason(client: httpx.AsyncClient):
    checked = (await client.post("/api/recordings/validate", json={"hello": 1})).json()

    assert checked["valid"] is False
    assert "bundle_version" in checked["error"]

    assert (await client.post("/api/recordings/import", json={"hello": 1})).status_code == 400


async def test_projects_list_counts_recordings(client: httpx.AsyncClient):
    await client.post("/api/demo/seed")

    projects = (await client.get("/api/projects")).json()

    assert projects[0]["project"] == "default"
    assert projects[0]["recordings"] == 2
