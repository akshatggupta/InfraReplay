"""v8 DoD, on a live stack with keys on and blobs encrypted.

  1. secrets never appear in stored recordings or in any API response;
  2. every capture and replay action leaves an audit entry;
  3. without a valid key, /api refuses — and the refusal is audited too.

Secrets are real traffic here: they go through the capture proxy, into the
demo shop, and back out through replay with a substituted credential.
"""

from pathlib import Path

import httpx
import pytest

from infrareplay import config
from infrareplay.api import create_app
from infrareplay.plugins.storage_encrypted.plugin import MAGIC, EncryptedStoragePlugin
from infrareplay.security import generate_key
from tests.integration.conftest import capture, client, replay, reset_shop
from tests.support.live import LiveServer

_API_SECRET = "s3cret-ci-key-7f3a"

# Each value carries a marker that must never be found anywhere afterwards.
_HEADERS = {
    "x-demo-user": "dana",
    "authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJkYW5hIn0.SECRETsignature",
    "cookie": "session=SECRET-COOKIE-9f8e7d6c",
}

_BODY = {
    "cart_id": "cart_7b21f0",
    "items": [{"sku": "AEROPRESS-GO", "qty": 1}],
    "payment_method": {"type": "card", "token": "tok_live_SECRETcard42", "cvv": "SECRET-CVV-737"},
    "note": "call back re eyJhbGciOiJIUzI1NiJ9.eyJub3RlIjoxfQ.SECRETnotejwt",
}

_FRESH_CREDENTIAL = "Bearer SECRET-FRESH-TOKEN-0123456789abcdef"

_MARKERS = (
    "SECRETsignature",
    "SECRET-COOKIE",
    "SECRETcard42",
    "SECRET-CVV",
    "SECRETnotejwt",
    "SECRET-FRESH",
    _API_SECRET,
)


@pytest.fixture
async def secure_stack(tmp_path, monkeypatch, gateway):
    monkeypatch.setenv("DEMO_SHOP_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/shop.db")
    monkeypatch.setenv("DEMO_SHOP_RACE_WINDOW_S", "0")

    monkeypatch.setattr(config, "API_KEYS", {_API_SECRET: "ci"})
    monkeypatch.setattr(config, "API_KEY", _API_SECRET)
    monkeypatch.setattr(config, "STORAGE_PLUGIN", "storage_encrypted")
    monkeypatch.setattr(config, "ENCRYPTION_KEY", generate_key())

    async with LiveServer(create_app()) as api:
        monkeypatch.setattr(config, "API_URL", api.url)

        from examples.demo_shop.app import create_app as create_shop

        async with LiveServer(create_shop()) as shop:
            yield api, shop


def _leaks(text: str) -> list[str]:
    return [m for m in _MARKERS if m in text]


async def test_secrets_never_reach_storage_or_the_api(secure_stack):
    api, shop = secure_stack

    recorded = await capture(api, shop, headers=_HEADERS, body=_BODY)
    rid = recorded["recording_id"]

    await reset_shop(shop, stock=5)
    replayed = await replay(
        api, rid, shop.url, substitutions={"***REDACTED***": _FRESH_CREDENTIAL}
    )

    # Every surface the API exposes for this recording and its replay.
    async with client(api) as api_client:
        responses = [
            (await api_client.get(path)).text
            for path in (
                "/api/recordings",
                f"/api/recordings/{rid}",
                f"/api/recordings/{rid}/export",
                f"/api/recordings/{replayed['replay_recording_id']}",
                f"/api/replays/{replayed['replay_recording_id']}/comparison",
                "/api/audit",
            )
        ]

    assert replayed["summary"]["MATCH"] >= 1  # the replay still works when masked
    assert {path: _leaks(text) for path, text in enumerate(responses)} == {
        i: [] for i in range(len(responses))
    }

    # The blob on disk: encrypted, and clean even once decrypted.
    blob_key = f"recordings/{rid}/recording.json"
    on_disk = (Path(config.BLOB_ROOT) / blob_key).read_bytes()

    assert on_disk.startswith(MAGIC)
    assert _leaks(on_disk.decode("latin-1")) == []
    assert _leaks((await EncryptedStoragePlugin().get_blob(blob_key)).decode()) == []

    # The metadata database file itself, byte for byte.
    db_file = Path(config.DATABASE_URL.split(":///", 1)[1])
    assert _leaks(db_file.read_bytes().decode("latin-1")) == []


async def test_every_capture_and_replay_action_is_audited(secure_stack):
    api, shop = secure_stack

    recorded = await capture(api, shop)
    rid = recorded["recording_id"]

    await reset_shop(shop, stock=5)
    await replay(api, rid, shop.url)
    await replay(api, rid, "https://api.production.example.com")  # refused

    async with client(api) as api_client:
        bundle = (await api_client.get(f"/api/recordings/{rid}/export")).json()
        await api_client.post("/api/recordings/import", json=bundle)

        entries = (await api_client.get("/api/audit")).json()

    seen = {(e["action"], e["outcome"]) for e in entries}

    assert {
        ("capture.start", "ok"),
        ("capture.stop", "ok"),
        ("replay.run", "ok"),
        ("replay.run", "failed"),
        ("recording.export", "ok"),
        ("recording.import", "ok"),
    } <= seen

    assert {e["actor"] for e in entries} == {"ci"}

    refused = next(e for e in entries if e["outcome"] == "failed")
    assert "production" in refused["detail"]["error"]


async def test_api_refuses_without_a_valid_key_and_says_so(secure_stack):
    api, _ = secure_stack

    async with httpx.AsyncClient(base_url=api.url, timeout=30) as anonymous:
        missing = await anonymous.get("/api/recordings")
        wrong = await anonymous.get("/api/recordings", headers={"x-api-key": "guess"})
        health = (await anonymous.get("/health")).json()
        docs = await anonymous.get("/docs")

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert health["auth"] == "api-keys"  # public, and honest about the mode
    assert docs.status_code == 200

    async with client(api) as authed:
        assert (await authed.get("/api/recordings")).status_code == 200

        denied = (await authed.get("/api/audit", params={"action": "auth.denied"})).json()

    assert len(denied) == 2
    assert {d["actor"] for d in denied} == {"unknown"}
    assert all(_API_SECRET not in str(d) and "guess" not in str(d) for d in denied)
