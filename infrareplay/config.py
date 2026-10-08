"""Process-wide configuration, sourced from the environment."""

import os
from pathlib import Path

# Metadata DB. SQLite by default so local dev and CI need nothing running;
# docker-compose overrides this with async Postgres.
DATABASE_URL = os.environ.get(
    "INFRAREPLAY_DATABASE_URL", "sqlite+aiosqlite:///./infrareplay.db"
)

# Blob storage root for the filesystem storage plugin.
BLOB_ROOT = Path(os.environ.get("INFRAREPLAY_BLOB_ROOT", "./data/blobs"))

API_URL = os.environ.get("INFRAREPLAY_API_URL", "http://127.0.0.1:8000")

# A replay target matching any of these fragments is refused unless the
# caller passes an explicit unsafe override.
PROD_TARGET_MARKERS = ("prod", "production", "live")

# Live capture limits. Bodies above this are recorded as a truncation note
# instead of the payload, so one big upload cannot blow up a recording.
CAPTURE_MAX_BODY_BYTES = int(os.environ.get("INFRAREPLAY_MAX_BODY_BYTES", 64 * 1024))

CAPTURE_TIMEOUT_S = float(os.environ.get("INFRAREPLAY_CAPTURE_TIMEOUT_S", 30))

# Payload keys dropped before a comparison, on top of the built-in volatile
# ones. Comma-separated, e.g. INFRAREPLAY_IGNORE_FIELDS=reference,eta_days
IGNORE_FIELDS = frozenset(
    f.strip() for f in os.environ.get("INFRAREPLAY_IGNORE_FIELDS", "").split(",") if f.strip()
)


# ---------------------------------------------------------------- security
#
# Blob storage is chosen by registry name. `storage_encrypted` wraps
# `storage_fs` with Fernet and needs INFRAREPLAY_ENCRYPTION_KEY; generate one
# with `infractl security keygen`.
STORAGE_PLUGIN = os.environ.get("INFRAREPLAY_STORAGE_PLUGIN", "storage_fs")
ENCRYPTION_KEY = os.environ.get("INFRAREPLAY_ENCRYPTION_KEY", "")


def _parse_api_keys(raw: str) -> dict[str, str]:
    """"ci:s3cret,dana:t0ken" -> {"s3cret": "ci", "t0ken": "dana"}.

    The label is what the audit log records as the actor; a bare secret with
    no label is recorded as "api-key".
    """

    keys: dict[str, str] = {}

    for entry in (e.strip() for e in raw.split(",")):
        if not entry:
            continue

        label, _, secret = entry.rpartition(":")
        keys[secret] = label or "api-key"

    return keys


# Server side: accepted keys. Empty means the API is open — the dev default,
# announced on /health and in the startup log rather than silently assumed.
API_KEYS = _parse_api_keys(os.environ.get("INFRAREPLAY_API_KEYS", ""))

# Client side: the key the CLI and dashboard send.
API_KEY = os.environ.get("INFRAREPLAY_API_KEY", "")
