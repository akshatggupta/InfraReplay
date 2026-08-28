"""Seed data: the clean and buggy fixture recordings."""

from infrareplay.plugins.mock_capture.fixtures import Scenario
from infrareplay.recording import capture_recording
from infrareplay.schema import Recording

_SCENARIOS = {
    Scenario.CLEAN: "Checkout — clean run",
    Scenario.BUGGY: "Checkout — payment step fails",
}


async def seed() -> list[Recording]:
    recordings: list[Recording] = []

    for scenario, title in _SCENARIOS.items():
        rec = await capture_recording(
            project="default",
            title=title,
            plugin_name="mock_capture",
            plugin_config={"scenario": scenario.value},
        )
        recordings.append(rec)

    return recordings
