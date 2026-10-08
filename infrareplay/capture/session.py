"""Capture sessions — a plugin running in the background of the API process.

    POST /api/captures            start_session ──> plugin.start()
                                                       │ events()
                                                       ▼
                                                  append_events()
    POST /api/captures/{id}/stop  stop_session  ──> plugin.stop()
                                                ──> finish_recording()

Events can also arrive from another process entirely — an instrumented app
posting the SQL it ran (see `infrareplay.agent`). `ingest_events` is the
door for those, and the reason a recording row exists before any event does.
"""

import asyncio

from infrareplay.contracts import CapturePlugin
from infrareplay.observability import get_logger, metrics
from infrareplay.plugins.registry import get_registry
from infrareplay.recording import (
    append_events,
    fail_recording,
    finish_recording,
    get_status,
    start_recording,
)
from infrareplay.schema import Event, Recording, RecordingStatus

_UPSTREAM = "upstream"

_log = get_logger(__name__)


class CaptureError(RuntimeError):
    pass


class CaptureSession:
    def __init__(
        self,
        *,
        recording_id: str,
        plugin_name: str,
        plugin: CapturePlugin,
        title: str,
        target: str,
    ) -> None:
        self.recording_id = recording_id
        self.plugin_name = plugin_name
        self.title = title
        self.target = target

        self._plugin = plugin
        self._drain: asyncio.Task | None = None

    def describe(self) -> dict:
        return {
            "recording_id": self.recording_id,
            "plugin": self.plugin_name,
            "title": self.title,
            "target": self.target,
            "listen_port": getattr(self._plugin, "port", 0),
        }

    async def open(self, config: dict) -> None:
        try:
            await self._plugin.start({**config, "recording_id": self.recording_id})
        except Exception as exc:  # noqa: BLE001 - lifecycle must record failure
            await fail_recording(self.recording_id, str(exc))
            raise CaptureError(str(exc)) from exc

        self._drain = asyncio.create_task(self._pump())

    async def close(self) -> Recording:
        await self._plugin.stop()

        if self._drain is not None:
            await self._drain

        return await finish_recording(self.recording_id)

    async def _pump(self) -> None:
        async for event in self._plugin.events():
            await append_events(self.recording_id, [event])


_SESSIONS: dict[str, CaptureSession] = {}


async def start_session(
    *,
    plugin_name: str,
    project: str = "default",
    title: str = "",
    config: dict,
) -> CaptureSession:
    plugin = get_registry().capture(plugin_name)()

    recording_id = await start_recording(
        project=project,
        title=title or f"{plugin_name} capture",
        capture_plugin=plugin_name,
        target=config.get(_UPSTREAM),
    )

    session = CaptureSession(
        recording_id=recording_id,
        plugin_name=plugin_name,
        plugin=plugin,
        title=title or f"{plugin_name} capture",
        target=config.get(_UPSTREAM, ""),
    )

    await session.open(config)
    _SESSIONS[recording_id] = session

    metrics.ACTIVE_CAPTURES.inc()
    _log.info(
        "capture.started",
        recording_id=recording_id,
        plugin=plugin_name,
        upstream=session.target,
        listen_port=session.describe()["listen_port"],
    )

    return session


async def stop_session(recording_id: str) -> Recording:
    session = _SESSIONS.pop(recording_id, None)

    if session is None:
        raise CaptureError(f"no live capture session {recording_id!r}")

    metrics.ACTIVE_CAPTURES.dec()
    recording = await session.close()

    _log.info("capture.stopped", recording_id=recording_id, events=len(recording.events))

    return recording


def active_sessions() -> list[dict]:
    return [s.describe() for s in _SESSIONS.values()]


async def ingest_events(recording_id: str, events: list[Event]) -> int:
    """Accept events produced outside this process."""

    status = await get_status(recording_id)

    if status is None:
        raise CaptureError(f"recording {recording_id!r} not found")

    if status is not RecordingStatus.RECORDING:
        raise CaptureError(f"recording {recording_id!r} is {status.value}, not open")

    stamped = [e.model_copy(update={"recording_id": recording_id}) for e in events]
    await append_events(recording_id, stamped)

    return len(stamped)
