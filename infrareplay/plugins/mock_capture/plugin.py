"""MockCapturePlugin — proof that the pipeline only talks to CapturePlugin."""

from collections.abc import AsyncIterator

from infrareplay.contracts import CapturePlugin
from infrareplay.plugins.mock_capture.fixtures import Scenario, build
from infrareplay.schema import Event, EventType


class MockCapturePlugin(CapturePlugin):
    name = "mock_capture"
    event_types = [t.value for t in EventType]

    def __init__(self) -> None:
        self._events: list[Event] = []
        self._started = False

    async def start(self, config: dict) -> None:
        """config: {"recording_id": str, "scenario": "clean" | "buggy"}."""

        recording_id = config["recording_id"]
        scenario = Scenario(config.get("scenario", Scenario.CLEAN.value))

        self._events = build(recording_id, scenario)
        self._started = True

    async def stop(self) -> None:
        self._started = False

    async def events(self) -> AsyncIterator[Event]:
        if not self._started:
            raise RuntimeError("MockCapturePlugin.events() before start()")

        for event in self._events:
            yield event
