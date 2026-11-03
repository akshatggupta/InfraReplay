"""Outbound HTTP from the application: recorded on capture, served on replay (v11).

    capture   app ──httpx──> CapturingTransport ──────────────> payment gateway
                                   └─ records the call and its answer

    replay    app ──httpx──> CapturingTransport ──x  (real service never called)
                                   └─ answers with the recorded response

A replay re-runs a customer's checkout; without this it would also re-run
the customer's card charge. The application builds its HTTP client on
`agent.http_transport()` and nothing else changes. Outside a capture or a
replay the transport is a plain pass-through.

On replay the default is recorded, and it fails closed: a call with no
recorded answer gets a 503 explaining why, rather than reaching the real
service. Live calls during a replay need an explicit opt-in.
"""

import json
from collections import Counter
from collections.abc import Awaitable, Callable
from time import time_ns
from uuid import uuid4

import httpx

from infrareplay import config
from infrareplay.capture import context
from infrareplay.schema import Event, EventType

OUTBOUND = "outbound"

HTTP_SERVICE_UNAVAILABLE = 503

_JSON = "application/json"

# Set per hop; a stubbed response recomputes its own.
_DROP_HEADERS = frozenset({"content-length", "content-encoding", "transfer-encoding", "connection"})

Lookup = Callable[[str, str], Awaitable[list[dict]]]
Record = Callable[[list[Event]], None]


def call_key(method: str, host: str, path: str) -> str:
    """How a recorded call is matched on replay: verb, host and path."""

    return f"{method.upper()} {host}{path}"


class CapturingTransport(httpx.AsyncBaseTransport):
    def __init__(
        self,
        *,
        record: Record,
        lookup: Lookup,
        service: str,
        inner: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._record = record
        self._lookup = lookup
        self._service = service
        self._inner = inner or httpx.AsyncHTTPTransport()

        # Per replayed request: how many times each call has been answered,
        # so the second identical call gets the second recorded answer.
        self._served: dict[str, Counter] = {}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        active = context.current()

        if active is None:
            return await self._inner.handle_async_request(request)

        if active.replay_of and active.externals is context.ExternalsMode.RECORDED:
            return await self._serve_recorded(active, request)

        return await self._record_live(active, request)

    async def aclose(self) -> None:
        await self._inner.aclose()

    def forget(self, correlation_id: str) -> None:
        """Drop per-request replay state once that request has answered."""

        self._served.pop(correlation_id, None)

    # ------------------------------------------------------------------ live

    async def _record_live(self, active: context.CaptureContext, request: httpx.Request) -> httpx.Response:
        started = time_ns()
        response = await self._inner.handle_async_request(request)

        try:
            await response.aread()
        finally:
            await response.aclose()

        self._emit(active, request, _response_payload(response), started, stubbed=False)

        return response

    # ---------------------------------------------------------------- replay

    async def _serve_recorded(self, active: context.CaptureContext, request: httpx.Request) -> httpx.Response:
        started = time_ns()
        key = call_key(request.method, request.url.netloc.decode(), request.url.path)

        served = self._served.setdefault(active.correlation_id, Counter())
        nth = served[key]
        served[key] += 1

        recorded = [
            pair for pair in await self._lookup(active.replay_of, active.source_correlation or "")
            if pair["key"] == key
        ]

        if nth < len(recorded):
            payload = recorded[nth]["response"]
        else:
            payload = {
                "status": HTTP_SERVICE_UNAVAILABLE,
                "headers": {"content-type": _JSON},
                "body": {
                    "error": "external_call_not_recorded",
                    "call": key,
                    "detail": "No recorded response for this call; replay does not reach "
                              "external services unless externals are set to live.",
                },
            }

        self._emit(active, request, payload, started, stubbed=True)

        return _stub_response(payload, request)

    # ---------------------------------------------------------------- events

    def _emit(
        self,
        active: context.CaptureContext,
        request: httpx.Request,
        response_payload: dict,
        started: int,
        *,
        stubbed: bool,
    ) -> None:
        finished = time_ns()
        request_id = f"evt_{uuid4().hex[:12]}"

        outbound_request = Event(
            recording_id=active.recording_id,
            event_id=request_id,
            sequence=0,
            timestamp_ns=started,
            duration_ns=finished - started,
            event_type=EventType.HTTP_REQUEST,
            service=self._service,
            correlation_id=active.correlation_id,
            payload=_request_payload(request),
        )
        outbound_response = Event(
            recording_id=active.recording_id,
            event_id=f"evt_{uuid4().hex[:12]}",
            parent_event_id=request_id,
            sequence=0,
            timestamp_ns=finished,
            duration_ns=0,
            event_type=EventType.HTTP_RESPONSE,
            service=self._service,
            correlation_id=active.correlation_id,
            payload={**response_payload, "direction": OUTBOUND, "stubbed": stubbed},
        )

        self._record([outbound_request, outbound_response])


def _request_payload(request: httpx.Request) -> dict:
    host = request.url.netloc.decode()

    return {
        "direction": OUTBOUND,
        "method": request.method,
        "host": host,
        "path": request.url.path,
        "key": call_key(request.method, host, request.url.path),
        "query": dict(request.url.params),
        "headers": dict(request.headers),
        "body": _decode(request.content, request.headers.get("content-type", "")),
    }


def _response_payload(response: httpx.Response) -> dict:
    return {
        "status": response.status_code,
        "headers": dict(response.headers),
        "body": _decode(response.content, response.headers.get("content-type", "")),
    }


def _decode(body: bytes, content_type: str):
    if not body:
        return None

    if len(body) > config.CAPTURE_MAX_BODY_BYTES:
        return {"truncated": True, "total_bytes": len(body)}

    if content_type.startswith(_JSON):
        try:
            return json.loads(body)
        except ValueError:
            pass

    return body.decode("utf-8", errors="replace")


def _stub_response(payload: dict, request: httpx.Request) -> httpx.Response:
    headers = {
        k: v for k, v in (payload.get("headers") or {}).items() if k.lower() not in _DROP_HEADERS
    }
    body = payload.get("body")

    if isinstance(body, (dict, list)):
        content = json.dumps(body).encode()
    else:
        content = (body or "").encode()

    return httpx.Response(payload.get("status", HTTP_SERVICE_UNAVAILABLE), headers=headers, content=content, request=request)
