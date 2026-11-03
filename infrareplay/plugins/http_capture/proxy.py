"""The reverse proxy itself. Knows nothing about recordings or storage.

    client ──> ReverseProxy ──> upstream app
                   │
                   └── emit(Event)  http.request, http.response

Every forwarded request carries a correlation id downstream so an
instrumented app can tag the SQL it runs with the request that caused it.
"""

import asyncio
import json
from collections.abc import Awaitable, Callable
from time import time_ns
from urllib.parse import urlparse
from uuid import uuid4

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from infrareplay import config
from infrareplay.replay.http_target import CORRELATION_HEADER, RECORDING_HEADER
from infrareplay.schema import Event, EventType

_ALL_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]

# Hop-by-hop headers belong to the connection, not the message.
_DROP_HEADERS = frozenset(
    {"host", "content-length", "connection", "transfer-encoding", "keep-alive"}
)

# The body is passed on already decompressed, so its encoding header must go
# too — kept, it would tell the client to gunzip plain bytes.
_DROP_RESPONSE_HEADERS = _DROP_HEADERS | {"content-encoding"}

_JSON = "application/json"
_STARTUP_POLL_S = 0.01
_STARTUP_TIMEOUT_S = 10

Emit = Callable[[Event], Awaitable[None]]


class ReverseProxy:
    def __init__(
        self,
        *,
        recording_id: str,
        upstream: str,
        emit: Emit,
        max_body_bytes: int = config.CAPTURE_MAX_BODY_BYTES,
    ) -> None:
        self._recording_id = recording_id
        self._upstream = upstream.rstrip("/")
        self._emit = emit
        self._max_body = max_body_bytes
        self._service = urlparse(upstream).netloc or upstream

        self._sequence = 0
        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task | None = None

        # One connection pool for the proxy's life. Building an httpx client
        # per request cost 183 ms median (it loads a TLS bundle) and was most
        # of capture's overhead in the v10 benchmark. A transport rather than
        # a client: a client keeps a cookie jar, and a reverse proxy must never
        # send one user's cookies on the next user's request.
        self._transport: httpx.AsyncHTTPTransport | None = None
        self._timeout = httpx.Timeout(config.CAPTURE_TIMEOUT_S).as_dict()

    # ------------------------------------------------------------- lifecycle

    async def serve(self, host: str, port: int) -> int:
        """Start listening. Returns the bound port (port 0 picks a free one)."""

        self._transport = httpx.AsyncHTTPTransport()

        app = Starlette(routes=[Route("/{path:path}", self._handle, methods=_ALL_METHODS)])
        settings = uvicorn.Config(
            app, host=host, port=port, log_level="warning", lifespan="off"
        )

        self._server = uvicorn.Server(settings)
        self._task = asyncio.create_task(self._server.serve())

        await self._await_startup()

        return self._bound_port(port)

    async def shutdown(self) -> None:
        if self._server is None or self._task is None:
            return

        self._server.should_exit = True
        await self._task

        if self._transport is not None:
            await self._transport.aclose()

        self._server = None
        self._task = None
        self._transport = None

    async def _await_startup(self) -> None:
        waited = 0.0

        while not (self._server and self._server.started):
            if waited > _STARTUP_TIMEOUT_S:
                raise RuntimeError("capture proxy failed to start")

            await asyncio.sleep(_STARTUP_POLL_S)
            waited += _STARTUP_POLL_S

    def _bound_port(self, requested: int) -> int:
        if requested or not self._server or not self._server.servers:
            return requested

        return self._server.servers[0].sockets[0].getsockname()[1]

    # ---------------------------------------------------------------- proxying

    async def _handle(self, request: Request) -> Response:
        correlation_id = request.headers.get(
            CORRELATION_HEADER, f"corr_{uuid4().hex[:12]}"
        )
        body = await request.body()

        headers = {
            k: v for k, v in request.headers.items() if k.lower() not in _DROP_HEADERS
        }

        captured = self._event(
            EventType.HTTP_REQUEST,
            {
                "method": request.method,
                "path": request.url.path,
                "http_version": request.scope.get("http_version", "1.1"),
                "client_ip": request.client.host if request.client else "",
                "headers": headers,
                "query": dict(request.query_params),
                "body": self._decode(body, request.headers.get("content-type", "")),
            },
            correlation_id,
            duration_ns=0,
        )
        await self._emit(captured)

        started = time_ns()
        upstream = await self._forward(request, body, headers, correlation_id)
        elapsed = time_ns() - started

        captured.duration_ns = elapsed

        await self._emit(
            self._event(
                EventType.HTTP_RESPONSE,
                _response_payload(upstream, self._decode),
                correlation_id,
                duration_ns=elapsed,
                parent=captured.event_id,
            )
        )

        return _client_response(upstream)

    async def _forward(
        self, request: Request, body: bytes, headers: dict, correlation_id: str
    ) -> httpx.Response | Exception:
        forwarded = {
            **headers,
            CORRELATION_HEADER: correlation_id,
            RECORDING_HEADER: self._recording_id,
        }

        upstream = httpx.Request(
            request.method,
            f"{self._upstream}{request.url.path}",
            params=dict(request.query_params),
            content=body or None,
            headers=forwarded,
            extensions={"timeout": self._timeout},
        )

        try:
            response = await self._transport.handle_async_request(upstream)

            try:
                await response.aread()
            finally:
                await response.aclose()

        except httpx.RequestError as exc:
            return exc

        return response

    # ----------------------------------------------------------------- events

    def _event(
        self,
        event_type: EventType,
        payload: dict,
        correlation_id: str,
        *,
        duration_ns: int,
        parent: str | None = None,
    ) -> Event:
        event = Event(
            recording_id=self._recording_id,
            event_id=f"evt_{uuid4().hex[:12]}",
            parent_event_id=parent,
            sequence=self._sequence,
            timestamp_ns=time_ns(),
            duration_ns=duration_ns,
            event_type=event_type,
            service=self._service,
            correlation_id=correlation_id,
            payload=payload,
        )

        self._sequence += 1

        return event

    def _decode(self, body: bytes, content_type: str):
        if not body:
            return None

        if len(body) > self._max_body:
            return {
                "truncated": True,
                "captured_bytes": self._max_body,
                "total_bytes": len(body),
            }

        if content_type.startswith(_JSON):
            try:
                return json.loads(body)
            except ValueError:
                pass

        return body.decode("utf-8", errors="replace")


def _response_payload(upstream: httpx.Response | Exception, decode) -> dict:
    if isinstance(upstream, Exception):
        return {"error": f"{type(upstream).__name__}: {upstream}"}

    return {
        "status": upstream.status_code,
        "headers": dict(upstream.headers),
        "body": decode(upstream.content, upstream.headers.get("content-type", "")),
    }


_BAD_GATEWAY = 502


def _client_response(upstream: httpx.Response | Exception) -> Response:
    if isinstance(upstream, Exception):
        return Response(f"upstream unreachable: {upstream}", status_code=_BAD_GATEWAY)

    headers = {
        k: v for k, v in upstream.headers.items() if k.lower() not in _DROP_RESPONSE_HEADERS
    }

    return Response(
        content=upstream.content, status_code=upstream.status_code, headers=headers
    )
