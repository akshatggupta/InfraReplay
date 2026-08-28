import pytest

from infrareplay.plugins.redaction_default.plugin import REDACTED, DefaultRedactionPlugin
from infrareplay.plugins.registry import PluginError, get_registry
from infrareplay.plugins.storage_fs.plugin import FilesystemStoragePlugin
from infrareplay.schema import Event, EventType


def test_registry_resolves_builtins_by_name():
    reg = get_registry()

    assert reg.capture("mock_capture").name == "mock_capture"
    assert reg.storage("storage_fs").name == "storage_fs"
    assert reg.comparator_for("http.response").event_type == "http.response"


def test_registry_unknown_name_lists_known():
    with pytest.raises(PluginError, match="known:"):
        get_registry().capture("does_not_exist")


async def test_mock_capture_yields_full_workflow():
    plugin = get_registry().capture("mock_capture")()
    await plugin.start({"recording_id": "rec_x", "scenario": "buggy"})

    events = [e async for e in plugin.events()]

    assert [e.sequence for e in events] == list(range(8))
    assert events[0].event_type is EventType.HTTP_REQUEST
    assert events[-1].payload["status"] == 402
    assert len({e.correlation_id for e in events}) == 1


async def test_storage_fs_roundtrip_and_key_guard(tmp_path):
    store = FilesystemStoragePlugin(root=tmp_path)

    await store.put_blob("recordings/a/b.json", b"hi")
    assert await store.get_blob("recordings/a/b.json") == b"hi"

    with pytest.raises(KeyError):
        await store.get_blob("missing")

    with pytest.raises(ValueError):
        await store.put_blob("../escape", b"x")


def test_default_redaction_masks_authorization():
    event = Event(
        recording_id="r",
        event_id="e",
        sequence=0,
        timestamp_ns=1,
        duration_ns=1,
        event_type=EventType.HTTP_REQUEST,
        service="s",
        correlation_id="c",
        payload={"headers": {"authorization": "Bearer abc", "accept": "*/*"}},
    )

    out = DefaultRedactionPlugin().redact(event)

    assert out.payload["headers"]["authorization"] == REDACTED
    assert out.payload["headers"]["accept"] == "*/*"
    assert event.payload["headers"]["authorization"] == "Bearer abc"  # original intact
