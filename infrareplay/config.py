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
