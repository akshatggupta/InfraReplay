"""Send recorded HTTP requests at a live target and record what came back.

The replayed request carries two headers the captured one did not:

    x-infrareplay-recording-id     the replay run these events belong to
    x-infrareplay-correlation-id   this request's group

An instrumented target (see `infrareplay.agent`) reads them and streams the
SQL it runs back into the same replay recording — which is what lets the
comparison engine diff database work, not just status codes.
"""

from time import time_ns
from urllib.parse import urlparse
from uuid import uuid4

import httpx

from infrareplay.schema import Event, EventType

RECORDING_HEADER = "x-infrareplay-recording-id"
CORRELATION_HEADER = "x-infrareplay-correlation-id"

# Describe the captured hop, not the replayed one.
_DROP_HEADERS = frozenset(
    {"host", "content-length", "connection", "transfer-encoding", "keep-alive"}
)

_TIMEOUT_S = 30
_JSON = "application/json"


class HttpTarget:
    """One live replay target. Use as an async context manager."""

    def __init__(
        self,
        base_url: str,
        *,
        recording_id: str,
        timeout_s: float = _TIMEOUT_S,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._recording_id = recording_id
        self._service = urlparse(base_url).netloc or base_url
        self._client = httpx.AsyncClient(timeout=timeout_s, follow_redirects=False)
        self._sequence = 0

    async def __aenter__(self) -> "HttpTarget":
        return self

    async def __aexit__(self, *_) -> None:
        await self._client.aclose()

    async def send(self, payload: dict) -> list[Event]:
        """Replay one http.request payload; return the request/response pair."""

        correlation_id = f"corr_{uuid4().hex[:12]}"
        headers = self._headers(payload, correlation_id)

        request = self._event(
            EventType.HTTP_REQUEST,
            {**payload, "headers": headers},
            correlation_id,
            duration_ns=0,
        )

        started = time_ns()

        try:
            response = await self._client.request(
                payload.get("method", "GET"),
                f"{self._base_url}{payload.get('path', '/')}",
                params=payload.get("query") or None,
                json=payload.get("body") if payload.get("body") else None,
                headers=headers,
            )
            result = _response_payload(response)

        except httpx.RequestError as exc:
            result = {"error": f"{type(exc).__name__}: {exc}"}

        elapsed = time_ns() - started

        request.duration_ns = elapsed
        reply = self._event(
            EventType.HTTP_RESPONSE,
            result,
            correlation_id,
            duration_ns=elapsed,
            parent=request.event_id,
        )

        return [request, reply]

    # ---------------------------------------------------------------- helpers

    def _headers(self, payload: dict, correlation_id: str) -> dict:
        headers = {
            k: v
            for k, v in (payload.get("headers") or {}).items()
            if k.lower() not in _DROP_HEADERS
        }
        headers[RECORDING_HEADER] = self._recording_id
        headers[CORRELATION_HEADER] = correlation_id

        return headers

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


def _response_payload(response: httpx.Response) -> dict:
    return {
        "status": response.status_code,
        "headers": dict(response.headers),
        "body": _decode(response),
    }


def _decode(response: httpx.Response):
    if response.headers.get("content-type", "").startswith(_JSON):
        try:
            return response.json()
        except ValueError:
            pass

    return response.text
