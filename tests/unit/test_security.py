"""v8: keys, encryption at rest, and redaction by shape."""

import pytest

from infrareplay import config
from infrareplay.plugins.redaction_default.plugin import REDACTED, DefaultRedactionPlugin
from infrareplay.plugins.storage_encrypted.plugin import MAGIC, EncryptedStoragePlugin
from infrareplay.plugins.storage_fs.plugin import FilesystemStoragePlugin
from infrareplay.schema import Event, EventType
from infrareplay.security import EncryptionKeyError, generate_key, resolve_actor

_PLAINTEXT = b'{"card_holder": "Dana Lang", "note": "plaintext-marker"}'
_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJkYW5hIn0.c2lnbmF0dXJlLXNlY3JldA"


# --------------------------------------------------------------- encryption


async def test_encrypted_blob_roundtrips_and_is_opaque_on_disk(tmp_path):
    store = EncryptedStoragePlugin(inner=FilesystemStoragePlugin(root=tmp_path), key=generate_key())

    await store.put_blob("recordings/r/recording.json", _PLAINTEXT)

    on_disk = (tmp_path / "recordings/r/recording.json").read_bytes()

    assert on_disk.startswith(MAGIC)
    assert b"plaintext-marker" not in on_disk
    assert await store.get_blob("recordings/r/recording.json") == _PLAINTEXT


async def test_wrong_key_fails_loudly(tmp_path):
    inner = FilesystemStoragePlugin(root=tmp_path)

    await EncryptedStoragePlugin(inner=inner, key=generate_key()).put_blob("k", _PLAINTEXT)

    with pytest.raises(EncryptionKeyError, match="wrong key"):
        await EncryptedStoragePlugin(inner=inner, key=generate_key()).get_blob("k")


async def test_plaintext_blob_is_reported_not_misread(tmp_path):
    inner = FilesystemStoragePlugin(root=tmp_path)
    await inner.put_blob("k", _PLAINTEXT)

    with pytest.raises(ValueError, match="not written by encrypted storage"):
        await EncryptedStoragePlugin(inner=inner, key=generate_key()).get_blob("k")


def test_missing_key_names_the_fix(monkeypatch):
    monkeypatch.setattr(config, "ENCRYPTION_KEY", "")

    with pytest.raises(EncryptionKeyError, match="infractl security keygen"):
        EncryptedStoragePlugin()


# ------------------------------------------------------------------- access


def test_api_keys_parse_with_and_without_labels():
    assert config._parse_api_keys("ci:s3cret, t0ken ,") == {"s3cret": "ci", "t0ken": "api-key"}


def test_resolve_actor(monkeypatch):
    monkeypatch.setattr(config, "API_KEYS", {})
    assert resolve_actor(None) == "anonymous"  # no keys configured: open

    monkeypatch.setattr(config, "API_KEYS", {"s3cret": "ci"})
    assert resolve_actor("s3cret") == "ci"
    assert resolve_actor("wrong") is None
    assert resolve_actor(None) is None


# ---------------------------------------------------------------- redaction


def _event(event_type: EventType, payload: dict) -> Event:
    return Event(
        recording_id="r",
        event_id="e",
        sequence=0,
        timestamp_ns=1,
        duration_ns=1,
        event_type=event_type,
        service="s",
        correlation_id="c",
        payload=payload,
    )


def test_secrets_in_sql_params_are_masked_by_shape():
    # No key name flags a positional parameter; the value's shape does.
    query = _event(
        EventType.PG_QUERY,
        {"sql": "INSERT INTO sessions VALUES ($1, $2)", "params": ["usr_1", f"Bearer {_JWT}"]},
    )

    out = DefaultRedactionPlugin().redact(query)

    assert out.payload["params"] == ["usr_1", REDACTED]


def test_secrets_inside_free_text_are_masked():
    out = DefaultRedactionPlugin().redact(
        _event(EventType.HTTP_REQUEST, {"body": {"note": f"retry with {_JWT} please",
                                                  "key": "sk_live_4eC39HqLyjWDarjtT1zdp7dc"}})
    )

    assert out.payload["body"]["note"] == f"retry with {REDACTED} please"
    assert out.payload["body"]["key"] == REDACTED


def test_ordinary_text_is_left_alone():
    payload = {"body": {"note": "Bearer of good news", "ref": "pi_3Qx8Zk2eRfL0aQ"}}

    assert DefaultRedactionPlugin().redact(_event(EventType.HTTP_REQUEST, payload)).payload == payload
