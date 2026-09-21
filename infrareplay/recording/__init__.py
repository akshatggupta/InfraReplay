"""Recording lifecycle and persistence."""

from infrareplay.recording.correlate import correlate
from infrareplay.recording.portable import BundleError, build_bundle, parse_bundle
from infrareplay.recording.service import (
    append_events,
    capture_recording,
    export_recording,
    fail_recording,
    finish_recording,
    get_comparison,
    get_recording,
    get_status,
    import_recording,
    link_replay_events,
    list_projects,
    list_recordings,
    new_id,
    save_comparison,
    start_recording,
)

__all__ = [
    "BundleError",
    "append_events",
    "build_bundle",
    "capture_recording",
    "correlate",
    "export_recording",
    "fail_recording",
    "finish_recording",
    "get_comparison",
    "get_recording",
    "get_status",
    "import_recording",
    "link_replay_events",
    "list_projects",
    "list_recordings",
    "new_id",
    "parse_bundle",
    "save_comparison",
    "start_recording",
]
