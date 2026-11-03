"""The app-side half of a capture: context binding plus an ingest client."""

import httpx

from infrareplay import config
from infrareplay.capture import context
from infrareplay.plugins.registry import get_registry
from infrareplay.agent.egress import CapturingTransport
from infrareplay.replay.http_target import (
    CORRELATION_HEADER,
    EXTERNALS_HEADER,
    RECORDING_HEADER,
    REPLAY_OF_HEADER,
    SOURCE_CORRELATION_HEADER,
)
from infrareplay.schema import Event
from infrareplay.security import API_KEY_HEADER

_SQL_PLUGIN = "postgres_capture"
_INGEST_PATH = "/api/captures/{rid}/events"
_EXTERNALS_PATH = "/api/recordings/{rid}/externals"
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
        self._headers = {API_KEY_HEADER: config.API_KEY} if config.API_KEY else {}
        self._service = service
        self._plugin = get_registry().capture(plugin_name)()
        self._stream = None

        # One pooled client for the agent's life: building one per flush cost
        # 183 ms median, paid inside every captured request.
        self._client: httpx.AsyncClient | None = None

        # v11: outbound calls waiting for the next flush, the transport that
        # records them, and the recorded answers fetched for a replay.
        self._outbound: list[Event] = []
        self._transport: CapturingTransport | None = None
        self._answers: dict[tuple[str, str], list[dict]] = {}

    def http_transport(self) -> CapturingTransport:
        """The transport to build the app's outbound HTTP client on (v11).

            httpx.AsyncClient(base_url=..., transport=agent.http_transport())
        """

        if self._transport is None:
            self._transport = CapturingTransport(
                record=self._outbound.extend,
                lookup=self._recorded_calls,
                service=self._service,
            )

        return self._transport

    def forget(self, correlation_id: str, replay_of: str | None, source: str | None) -> None:
        """Drop one request's replay state once it has answered."""

        if self._transport is not None:
            self._transport.forget(correlation_id)

        if replay_of:
            self._answers.pop((replay_of, source or ""), None)

    async def _recorded_calls(self, recording_id: str, correlation_id: str) -> list[dict]:
        """The outbound calls the original request made, fetched once per request."""

        key = (recording_id, correlation_id)

        if key in self._answers:
            return self._answers[key]

        if self._client is None:
            return []

        try:
            reply = await self._client.get(
                _EXTERNALS_PATH.format(rid=recording_id), params={"correlation_id": correlation_id}
            )
            answers = reply.json() if reply.is_success else []
        except httpx.HTTPError:
            answers = []  # no answers: the transport fails closed, nothing goes out

        self._answers[key] = answers

        return answers

    async def start(self) -> None:
        await self._plugin.start({"engine": self._engine, "service": self._service})
        self._stream = self._plugin.events()
        self._client = httpx.AsyncClient(
            base_url=self._api_url, timeout=_TIMEOUT_S, headers=self._headers
        )

    async def stop(self) -> None:
        await self._plugin.stop()
        self._stream = None

        if self._client is not None:
            await self._client.aclose()
            self._client = None

        if self._transport is not None:
            await self._transport.aclose()
            self._transport = None

    async def flush(self) -> int:
        """Post what is queued. Never breaks the app if InfraReplay is down.

        Concurrent requests share one queue, so the drain is grouped by the
        recording each event belongs to rather than by the caller.
        """

        events = await self._drain()

        if not events or self._client is None:
            return 0

        batches: dict[str, list[Event]] = {}

        for captured in events:
            batches.setdefault(captured.recording_id, []).append(captured)

        try:
            for recording_id, batch in batches.items():
                await self._client.post(
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

        events, self._outbound[:] = list(self._outbound), []

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
        replay_of = headers.get(REPLAY_OF_HEADER)
        source = headers.get(SOURCE_CORRELATION_HEADER)
        externals = _externals_mode(headers.get(EXTERNALS_HEADER))

        async def flushing_send(message) -> None:
            if message["type"] == "http.response.body" and not message.get("more_body"):
                await self._agent.flush()

            await send(message)

        token = context.bind(
            recording_id,
            correlation_id,
            replay_of=replay_of,
            source_correlation=source,
            externals=externals,
        )

        try:
            await self._app(scope, receive, flushing_send)
        finally:
            context.reset(token)
            self._agent.forget(correlation_id, replay_of, source)


def _externals_mode(raw: str | None) -> context.ExternalsMode:
    """Anything but an explicit "live" is recorded: replay fails safe."""

    if raw == context.ExternalsMode.LIVE.value:
        return context.ExternalsMode.LIVE

    return context.ExternalsMode.RECORDED


def instrument(app, engine, *, api_url: str | None = None, service: str = "app") -> CaptureAgent:
    """Attach capture to a Starlette/FastAPI app. Call `await agent.start()`."""

    agent = CaptureAgent(engine, api_url=api_url, service=service)
    app.add_middleware(CaptureMiddleware, agent=agent)

    return agent
