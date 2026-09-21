"""A recording as one self-contained file.

    infractl recordings export rec_83c0 -o checkout.json
    infractl recordings import checkout.json        # on any other instance

The bundle states its own versions, so a file written by an older build is
upgraded through `infrareplay.schema.migrations` on import instead of being
rejected. Blobs are not referenced: everything needed to replay the
recording is inside the file.
"""

from datetime import datetime, timezone
from uuid import uuid4

from pydantic import ValidationError

from infrareplay.schema import SCHEMA_VERSION, Event, Recording
from infrareplay.schema import migrations

BUNDLE_VERSION = 1

_RECORDING = "recording"
_EVENTS = "events"


class BundleError(ValueError):
    pass


def build_bundle(recording: Recording) -> dict:
    return {
        "bundle_version": BUNDLE_VERSION,
        "schema_version": SCHEMA_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        _RECORDING: recording.model_dump(mode="json", exclude={_EVENTS}),
        _EVENTS: [e.model_dump(mode="json") for e in recording.events],
    }


def parse_bundle(doc: dict) -> Recording:
    """Validate and upgrade a bundle. Raises BundleError with the reason."""

    version = doc.get("bundle_version")

    if version is None:
        raise BundleError("not an InfraReplay bundle: no bundle_version")

    if version > BUNDLE_VERSION:
        raise BundleError(f"bundle_version {version} is newer than {BUNDLE_VERSION}")

    try:
        events = [Event(**_upgraded(raw)) for raw in doc.get(_EVENTS, [])]

        return Recording(**doc[_RECORDING], events=events)

    except (KeyError, TypeError, ValidationError) as exc:
        raise BundleError(str(exc)) from exc


def _upgraded(raw: dict) -> dict:
    if raw.get("schema_version", SCHEMA_VERSION) >= SCHEMA_VERSION:
        return raw

    return migrations.upgrade(raw, SCHEMA_VERSION)


def rekey(recording: Recording) -> Recording:
    """Give the recording and every event a fresh id, keeping the shape.

    Needed when a bundle is imported into an instance that already holds the
    original — ids are primary keys, the linkage between events is not.
    """

    recording_id = f"rec_{uuid4().hex[:12]}"
    renamed = {e.event_id: f"evt_{uuid4().hex[:12]}" for e in recording.events}

    events = [
        event.model_copy(
            update={
                "recording_id": recording_id,
                "event_id": renamed[event.event_id],
                "parent_event_id": renamed.get(event.parent_event_id),
            }
        )
        for event in recording.events
    ]

    return recording.model_copy(update={"recording_id": recording_id, "events": events})
