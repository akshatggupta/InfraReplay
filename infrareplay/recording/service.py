"""The single source of truth for recording state transitions.

Every CLI command and dashboard action reaches this through the API — there
is exactly one code path that knows how to "start a recording".
"""

import json
from uuid import uuid4

from sqlalchemy import func, select

from infrareplay.db import ComparisonRow, EventRow, RecordingRow, get_session, init_db
from infrareplay.plugins.registry import get_registry
from infrareplay.recording.correlate import correlate
from infrareplay.recording.portable import build_bundle, parse_bundle, rekey
from infrareplay.schema import (
    ComparisonResult,
    Event,
    Recording,
    RecordingKind,
    RecordingStatus,
)

_DEFAULT_REDACTION = "redaction_default"
_DEFAULT_STORAGE = "storage_fs"
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


# ---------------------------------------------------------------- lifecycle
#
#   start_recording ──> append_events (n times) ──> finish_recording
#                                   └──> fail_recording
#
# Both capture and replay runs go through these three calls, so a recording
# is always a row in the DB before the first event arrives — which is what
# lets a second process stream events into it (see the /api/captures ingest).


async def start_recording(
    *,
    project: str = "default",
    title: str = "",
    kind: RecordingKind = RecordingKind.CAPTURE,
    capture_plugin: str = "",
    target: str | None = None,
    source_recording_id: str | None = None,
    recording_id: str | None = None,
) -> str:
    await init_db()

    recording_id = recording_id or new_id("rec" if kind is RecordingKind.CAPTURE else "rpl")

    async with get_session() as session:
        session.add(
            RecordingRow(
                recording_id=recording_id,
                project=project,
                kind=kind.value,
                status=RecordingStatus.RECORDING.value,
                title=title,
                capture_plugin=capture_plugin,
                target=target,
                source_recording_id=source_recording_id,
            )
        )
        await session.commit()

    return recording_id


async def append_events(
    recording_id: str,
    events: list[Event],
    *,
    links: dict[str, str] | None = None,
) -> list[Event]:
    """Redact and persist events. `links` maps replayed id -> original id."""

    if not events:
        return []

    redactor = get_registry().redaction(_DEFAULT_REDACTION)()
    links = links or {}

    stored: list[Event] = []

    async with get_session() as session:
        for event in events:
            clean = redactor.redact(event)
            stored.append(clean)
            session.add(_event_to_row(clean, source_event_id=links.get(clean.event_id)))

        await session.commit()

    return stored


async def fail_recording(recording_id: str, reason: str) -> None:
    async with get_session() as session:
        row = await session.get(RecordingRow, recording_id)

        if row is None:
            return

        row.status = RecordingStatus.FAILED.value
        row.title = f"{row.title} (failed: {reason})"
        await session.commit()


async def finish_recording(recording_id: str) -> Recording:
    """Correlate what was collected, snapshot it to blob storage, complete."""

    storage = get_registry().storage(_DEFAULT_STORAGE)()

    async with get_session() as session:
        row = await session.get(RecordingRow, recording_id)

        if row is None:
            raise KeyError(recording_id)

        events = correlate(await _events_for(session, recording_id))

        for event in events:
            stored = await session.get(EventRow, event.event_id)
            stored.sequence = event.sequence
            stored.parent_event_id = event.parent_event_id

        row.blob_locator = await storage.put_blob(
            _SNAPSHOT_KEY.format(rid=recording_id),
            _snapshot_bytes(row, events),
        )
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


# ------------------------------------------------------------ one-shot capture


async def capture_recording(
    *,
    project: str,
    title: str,
    plugin_name: str,
    plugin_config: dict,
) -> Recording:
    """Drive a finite capture plugin (e.g. mock) from start to stop."""

    capture = get_registry().capture(plugin_name)()

    recording_id = await start_recording(
        project=project, title=title, capture_plugin=plugin_name
    )

    try:
        await capture.start({**plugin_config, "recording_id": recording_id})

        async for event in capture.events():
            await append_events(recording_id, [event])

        await capture.stop()

    except Exception as exc:  # noqa: BLE001 - lifecycle must record failure
        await fail_recording(recording_id, str(exc))
        raise

    return await finish_recording(recording_id)


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


async def get_status(recording_id: str) -> RecordingStatus | None:
    """Cheap status probe — used by the ingest endpoint on every event batch."""

    await init_db()

    async with get_session() as session:
        row = await session.get(RecordingRow, recording_id)

        return RecordingStatus(row.status) if row else None


async def _events_for(session, recording_id: str) -> list[Event]:
    stmt = (
        select(EventRow)
        .where(EventRow.recording_id == recording_id)
        .order_by(EventRow.sequence)
    )
    rows = (await session.scalars(stmt)).all()

    return [_row_to_event(r) for r in rows]


# ------------------------------------------------------------ export/import


async def export_recording(recording_id: str) -> dict | None:
    recording = await get_recording(recording_id)

    return build_bundle(recording) if recording else None


async def import_recording(doc: dict) -> Recording:
    """Load a bundle. A clashing id is re-keyed rather than refused."""

    recording = parse_bundle(doc)

    if await get_status(recording.recording_id) is not None:
        recording = rekey(recording)

    await start_recording(
        recording_id=recording.recording_id,
        project=recording.project,
        title=recording.title,
        kind=recording.kind,
        capture_plugin=recording.capture_plugin,
        target=recording.target,
        source_recording_id=recording.source_recording_id,
    )
    await append_events(recording.recording_id, recording.events)

    return await finish_recording(recording.recording_id)


async def list_projects() -> list[dict]:
    await init_db()

    async with get_session() as session:
        stmt = select(
            RecordingRow.project,
            func.count(RecordingRow.recording_id),
            func.max(RecordingRow.created_at),
        ).group_by(RecordingRow.project)

        rows = (await session.execute(stmt)).all()

        return [
            {"project": project, "recordings": count, "last_activity": last}
            for project, count, last in rows
        ]


# ------------------------------------------------------------------- replay


async def link_replay_events(replay_recording_id: str, links: dict[str, str]) -> None:
    """Record which original event each replayed event reproduces."""

    async with get_session() as session:
        for original_event_id, replayed_event_id in links.items():
            row = await session.get(EventRow, replayed_event_id)

            if row is None:
                continue

            row.source_event_id = original_event_id

        await session.commit()


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
