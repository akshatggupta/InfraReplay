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
