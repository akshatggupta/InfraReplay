"""The app-side half of a capture: context binding plus an ingest client."""

import httpx

from infrareplay import config
from infrareplay.capture import context
from infrareplay.plugins.registry import get_registry
from infrareplay.replay.http_target import CORRELATION_HEADER, RECORDING_HEADER
from infrareplay.schema import Event

_SQL_PLUGIN = "postgres_capture"
_INGEST_PATH = "/api/captures/{rid}/events"
_TIMEOUT_S = 10


class CaptureAgent:
    """Owns the in-app capture plugin and ships its events to InfraReplay."""

    def __init__(
        self,
        engine,
        *,
        api_url: str | None = None,
        service: str = "app",
        plugin_name: str = _SQL_PLUGIN,
    ) -> None:
        self._engine = engine
        self._api_url = (api_url or config.API_URL).rstrip("/")
        self._service = service
        self._plugin = get_registry().capture(plugin_name)()
        self._stream = None

    async def start(self) -> None:
        await self._plugin.start({"engine": self._engine, "service": self._service})
        self._stream = self._plugin.events()

    async def stop(self) -> None:
        await self._plugin.stop()
        self._stream = None

    async def flush(self) -> int:
        """Post what is queued. Never breaks the app if InfraReplay is down.

        Concurrent requests share one queue, so the drain is grouped by the
        recording each event belongs to rather than by the caller.
        """

        events = await self._drain()

        if not events:
            return 0

        batches: dict[str, list[Event]] = {}

        for captured in events:
            batches.setdefault(captured.recording_id, []).append(captured)

        try:
            async with httpx.AsyncClient(
                base_url=self._api_url, timeout=_TIMEOUT_S
            ) as client:
                for recording_id, batch in batches.items():
                    await client.post(
                        _INGEST_PATH.format(rid=recording_id),
                        json=[e.model_dump(mode="json") for e in batch],
                    )
        except httpx.HTTPError:
            return 0  # capture is best-effort; the app's own work still returns

        return len(events)

    async def _drain(self) -> list[Event]:
        """Take what is queued right now. pending() keeps this non-blocking."""

        if self._stream is None:
            return []

        events: list[Event] = []

        while self._plugin.pending():
            events.append(await anext(self._stream))

        return events


class CaptureMiddleware:
    """Pure-ASGI: bind the capture context, flush before the response ships."""

    def __init__(self, app, agent: CaptureAgent) -> None:
        self._app = app
        self._agent = agent

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        recording_id = headers.get(RECORDING_HEADER)

        if not recording_id:
            await self._app(scope, receive, send)
            return

        correlation_id = headers.get(CORRELATION_HEADER, recording_id)

        async def flushing_send(message) -> None:
            if message["type"] == "http.response.body" and not message.get("more_body"):
                await self._agent.flush()

            await send(message)

        token = context.bind(recording_id, correlation_id)

        try:
            await self._app(scope, receive, flushing_send)
        finally:
            context.reset(token)


def instrument(app, engine, *, api_url: str | None = None, service: str = "app") -> CaptureAgent:
    """Attach capture to a Starlette/FastAPI app. Call `await agent.start()`."""

    agent = CaptureAgent(engine, api_url=api_url, service=service)
    app.add_middleware(CaptureMiddleware, agent=agent)

    return agent
