"""The single source of truth for recording state transitions.

Every CLI command and dashboard action reaches this through the API — there
is exactly one code path that knows how to "start a recording".
"""

import json
from uuid import uuid4

from sqlalchemy import select

from infrareplay.db import ComparisonRow, EventRow, RecordingRow, get_session, init_db
from infrareplay.plugins.registry import get_registry
from infrareplay.schema import (
    ComparisonResult,
    Event,
    Recording,
    RecordingKind,
    RecordingStatus,
    ReplayRun,
)

_DEFAULT_REDACTION = "redaction_default"
_SNAPSHOT_KEY = "recordings/{rid}/recording.json"


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


# ------------------------------------------------------------------ mapping


def _row_to_event(row: EventRow) -> Event:
    return Event(
        schema_version=row.schema_version,
        recording_id=row.recording_id,
        event_id=row.event_id,
        parent_event_id=row.parent_event_id,
        sequence=row.sequence,
        timestamp_ns=row.timestamp_ns,
        duration_ns=row.duration_ns,
        event_type=row.event_type,
        service=row.service,
        correlation_id=row.correlation_id,
        trace_id=row.trace_id,
        span_id=row.span_id,
        payload=row.payload,
    )


def _event_to_row(event: Event, source_event_id: str | None = None) -> EventRow:
    return EventRow(
        event_id=event.event_id,
        recording_id=event.recording_id,
        parent_event_id=event.parent_event_id,
        source_event_id=source_event_id,
        schema_version=event.schema_version,
        sequence=event.sequence,
        timestamp_ns=event.timestamp_ns,
        duration_ns=event.duration_ns,
        event_type=event.event_type.value,
        service=event.service,
        correlation_id=event.correlation_id,
        trace_id=event.trace_id,
        span_id=event.span_id,
        payload=event.payload,
    )


def _row_to_recording(row: RecordingRow, events: list[Event]) -> Recording:
    return Recording(
        recording_id=row.recording_id,
        project=row.project,
        kind=RecordingKind(row.kind),
        status=RecordingStatus(row.status),
        title=row.title,
        capture_plugin=row.capture_plugin,
        source_recording_id=row.source_recording_id,
        target=row.target,
        created_at=row.created_at,
        events=events,
    )


# ------------------------------------------------------------------ capture


async def capture_recording(
    *,
    project: str,
    title: str,
    plugin_name: str,
    plugin_config: dict,
) -> Recording:
    """Drive a capture plugin end to end and persist the result."""

    await init_db()

    registry = get_registry()
    capture = registry.capture(plugin_name)()
    redactor = registry.redaction(_DEFAULT_REDACTION)()
    storage = registry.storage("storage_fs")()

    recording_id = new_id("rec")
    plugin_config = {**plugin_config, "recording_id": recording_id}

    async with get_session() as session:
        row = RecordingRow(
            recording_id=recording_id,
            project=project,
            kind=RecordingKind.CAPTURE.value,
            status=RecordingStatus.RECORDING.value,
            title=title,
            capture_plugin=plugin_name,
        )
        session.add(row)
        await session.commit()

        events: list[Event] = []

        try:
            await capture.start(plugin_config)

            async for raw in capture.events():
                clean = redactor.redact(raw)
                events.append(clean)
                session.add(_event_to_row(clean))

            await capture.stop()

        except Exception as exc:  # noqa: BLE001 - lifecycle must record failure
            row.status = RecordingStatus.FAILED.value
            row.title = f"{title} (capture failed: {exc})"
            await session.commit()
            raise

        locator = await storage.put_blob(
            _SNAPSHOT_KEY.format(rid=recording_id),
            _snapshot_bytes(row, events),
        )

        row.blob_locator = locator
        row.status = RecordingStatus.COMPLETED.value
        await session.commit()

        return _row_to_recording(row, events)


def _snapshot_bytes(row: RecordingRow, events: list[Event]) -> bytes:
    doc = {
        "recording_id": row.recording_id,
        "project": row.project,
        "kind": row.kind,
        "title": row.title,
        "events": [e.model_dump(mode="json") for e in events],
    }

    return json.dumps(doc, indent=2).encode()


# --------------------------------------------------------------------- read


async def list_recordings(kind: RecordingKind | None = None) -> list[Recording]:
    await init_db()

    async with get_session() as session:
        stmt = select(RecordingRow).order_by(RecordingRow.created_at.desc())

        if kind is not None:
            stmt = stmt.where(RecordingRow.kind == kind.value)

        rows = (await session.scalars(stmt)).all()

        return [_row_to_recording(r, []) for r in rows]


async def get_recording(recording_id: str) -> Recording | None:
    await init_db()

    async with get_session() as session:
        row = await session.get(RecordingRow, recording_id)

        if row is None:
            return None

        events = await _events_for(session, recording_id)

        return _row_to_recording(row, events)


async def _events_for(session, recording_id: str) -> list[Event]:
    stmt = (
        select(EventRow)
        .where(EventRow.recording_id == recording_id)
        .order_by(EventRow.sequence)
    )
    rows = (await session.scalars(stmt)).all()

    return [_row_to_event(r) for r in rows]


# ------------------------------------------------------------------- replay


async def save_replay_run(
    *,
    source_recording_id: str,
    target: str,
    replayed_events: list[Event],
    links: dict[str, str],
) -> ReplayRun:
    """Persist a replay as a Recording of kind REPLAY, linked event by event."""

    await init_db()

    replay_id = replayed_events[0].recording_id if replayed_events else new_id("rpl")
    inverse = {replay_eid: orig_eid for orig_eid, replay_eid in links.items()}

    async with get_session() as session:
        session.add(
            RecordingRow(
                recording_id=replay_id,
                project="default",
                kind=RecordingKind.REPLAY.value,
                status=RecordingStatus.COMPLETED.value,
                title=f"Replay of {source_recording_id}",
                source_recording_id=source_recording_id,
                target=target,
            )
        )

        for event in replayed_events:
            session.add(_event_to_row(event, source_event_id=inverse.get(event.event_id)))

        await session.commit()

    return ReplayRun(
        replay_recording_id=replay_id,
        source_recording_id=source_recording_id,
        target=target,
        event_links=links,
    )


# --------------------------------------------------------------- comparison


async def save_comparison(
    replay_recording_id: str, results: list[ComparisonResult]
) -> None:
    await init_db()

    async with get_session() as session:
        for r in results:
            session.add(
                ComparisonRow(
                    replay_recording_id=replay_recording_id,
                    original_event_id=r.original_event_id,
                    replayed_event_id=r.replayed_event_id,
                    event_type=r.event_type,
                    category=r.category.value,
                    diff=r.diff,
                )
            )

        await session.commit()


async def get_comparison(replay_recording_id: str) -> list[ComparisonResult]:
    await init_db()

    async with get_session() as session:
        stmt = (
            select(ComparisonRow)
            .where(ComparisonRow.replay_recording_id == replay_recording_id)
            .order_by(ComparisonRow.id)
        )
        rows = (await session.scalars(stmt)).all()

        return [
            ComparisonResult(
                replay_recording_id=r.replay_recording_id,
                original_event_id=r.original_event_id,
                replayed_event_id=r.replayed_event_id,
                event_type=r.event_type,
                category=r.category,
                diff=r.diff,
            )
            for r in rows
        ]
