"""PostgresCapturePlugin — SQL captured where it is issued: inside the app.

SQLAlchemy raises `before_cursor_execute` / `after_cursor_execute` on every
engine, so this is app-level instrumentation rather than a wire proxy — the
scope the spec asks for. The correlation id comes from the request context
(`infrareplay.capture.context`), which is what ties a query back to the HTTP
request that caused it.

Queries running outside a bound context are ignored, so an instrumented app
records nothing while no capture is in flight.
"""

import asyncio
from collections.abc import AsyncIterator
from time import time_ns
from uuid import uuid4

from sqlalchemy import event

from infrareplay.capture import context
from infrareplay.contracts import CapturePlugin
from infrareplay.schema import Event, EventType

_START_KEY = "infrareplay_started_ns"
_NO_ROWCOUNT = -1


class PostgresCapturePlugin(CapturePlugin):
    name = "postgres_capture"
    event_types = [EventType.PG_QUERY.value, EventType.PG_RESULT.value]

    def __init__(self) -> None:
        self._queue: asyncio.Queue[Event | None] = asyncio.Queue()
        self._engine = None
        self._service = "app"
        self._sequence = 0

    async def start(self, config: dict) -> None:
        """config: {"engine": SQLAlchemy engine, "service": name}."""

        engine = config["engine"]
        self._engine = getattr(engine, "sync_engine", engine)
        self._service = config.get("service", self._service)

        event.listen(self._engine, "before_cursor_execute", self._before)
        event.listen(self._engine, "after_cursor_execute", self._after)

    async def stop(self) -> None:
        if self._engine is not None:
            event.remove(self._engine, "before_cursor_execute", self._before)
            event.remove(self._engine, "after_cursor_execute", self._after)
            self._engine = None

        await self._queue.put(None)

    async def events(self) -> AsyncIterator[Event]:
        while True:
            captured = await self._queue.get()

            if captured is None:
                return

            yield captured

    def pending(self) -> int:
        """Events queued but not yet yielded — lets a caller drain without waiting."""

        return self._queue.qsize()

    # --------------------------------------------------------------- listeners

    def _before(self, conn, cursor, statement, parameters, ctx, executemany) -> None:
        conn.info[_START_KEY] = time_ns()

    def _after(self, conn, cursor, statement, parameters, ctx, executemany) -> None:
        active = context.current()

        if active is None:
            return  # not capturing: the uninstrumented fast path

        started = conn.info.pop(_START_KEY, time_ns())
        elapsed = time_ns() - started

        query = self._event(
            EventType.PG_QUERY,
            {"sql": statement.strip(), "params": _jsonable(parameters)},
            active,
            timestamp_ns=started,
            duration_ns=elapsed,
        )

        self._queue.put_nowait(query)
        self._queue.put_nowait(
            self._event(
                EventType.PG_RESULT,
                _result_payload(cursor),
                active,
                timestamp_ns=started + elapsed,
                duration_ns=0,
                parent=query.event_id,
            )
        )

    def _event(
        self,
        event_type: EventType,
        payload: dict,
        active: context.CaptureContext,
        *,
        timestamp_ns: int,
        duration_ns: int,
        parent: str | None = None,
    ) -> Event:
        captured = Event(
            recording_id=active.recording_id,
            event_id=f"evt_{uuid4().hex[:12]}",
            parent_event_id=parent,
            sequence=self._sequence,
            timestamp_ns=timestamp_ns,
            duration_ns=duration_ns,
            event_type=event_type,
            service=self._service,
            correlation_id=active.correlation_id,
            payload=payload,
        )

        self._sequence += 1

        return captured


def _result_payload(cursor) -> dict:
    rowcount = getattr(cursor, "rowcount", _NO_ROWCOUNT)

    return {
        "rows_affected": None if rowcount == _NO_ROWCOUNT else rowcount,
        "returns_rows": cursor.description is not None,
    }


def _jsonable(parameters) -> list:
    """Driver params are tuples/dicts of arbitrary Python objects."""

    if parameters is None:
        return []

    if isinstance(parameters, dict):
        parameters = list(parameters.values())

    return [p if isinstance(p, (str, int, float, bool, type(None))) else str(p) for p in parameters]
