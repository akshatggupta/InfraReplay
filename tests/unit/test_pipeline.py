import pytest

from infrareplay.plugins.redaction_default.plugin import REDACTED
from infrareplay.recording import capture_recording, get_recording
from infrareplay.replay.engine import ReplayError, SafetyMode
from infrareplay.replay.runner import run_replay
from infrareplay.schema import RecordingStatus


async def _capture(scenario: str):
    return await capture_recording(
        project="default",
        title=f"t-{scenario}",
        plugin_name="mock_capture",
        plugin_config={"scenario": scenario},
    )


async def test_capture_completes_and_redacts():
    rec = await _capture("clean")

    assert rec.status is RecordingStatus.COMPLETED
    assert len(rec.events) == 8
    assert rec.events[0].payload["headers"]["authorization"] == REDACTED

    stored = await get_recording(rec.recording_id)
    assert stored is not None and len(stored.events) == 8


async def test_replay_buggy_surfaces_match_and_different():
    buggy = await _capture("buggy")

    _, summary = await run_replay(recording_id=buggy.recording_id, target="mock")

    assert summary["DIFFERENT"] >= 1
    assert summary["MATCH"] >= 1


async def test_replay_clean_is_all_match():
    clean = await _capture("clean")

    _, summary = await run_replay(recording_id=clean.recording_id, target="mock")

    assert summary["DIFFERENT"] == 0
    assert summary["MATCH"] >= 1


async def test_replay_refuses_production_target():
    clean = await _capture("clean")

    with pytest.raises(ReplayError, match="production"):
        await run_replay(
            recording_id=clean.recording_id,
            target="https://api.production.example.com",
        )
