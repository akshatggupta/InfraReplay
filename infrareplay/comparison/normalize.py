"""Make two runs comparable: erase what changes on every run by design.

Without this a replay against an *unchanged* service is a wall of false
DIFFERENTs — a new order id, a fresh payment reference, a Date header, a
different wall clock. Normalisation rewrites those values to their *shape*
before the diff runs, so a genuine change still shows and noise does not.

    "ord_9f2c1a7b3e00"                      -> "<id>"
    "3fa85f64-5717-4562-b3fc-2c963f66afa6"  -> "<uuid>"
    "2026-09-21T10:00:00.412Z"              -> "<timestamp>"

Anything else is compared exactly, which is the point: a 201 that became a
409, or a row count that changed, is still a difference.
"""

import re

from infrareplay import config

UUID = "<uuid>"
TIMESTAMP = "<timestamp>"
ID = "<id>"

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)

# 2026-09-21T10:00:00[.412][Z|+01:00], with a space separator allowed.
_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$"
)

# Generated identifiers: a short prefix and a hex tail — ord_9f2c1a7b3e00.
_PREFIXED_ID_RE = re.compile(r"^[a-z]{2,6}_[0-9a-f]{8,}$", re.I)

# Set per hop, never equal between two runs.
_VOLATILE_HEADERS = frozenset(
    {
        "date",
        "server",
        "etag",
        "content-length",
        "set-cookie",
        "x-request-id",
        "x-infrareplay-correlation-id",
        "x-infrareplay-recording-id",
    }
)


def normalize(value):
    """Recursively replace volatile values with a stable placeholder."""

    if isinstance(value, dict):
        return {
            k: normalize(v)
            for k, v in value.items()
            if k.lower() not in _VOLATILE_HEADERS and k not in config.IGNORE_FIELDS
        }

    if isinstance(value, list):
        return [normalize(v) for v in value]

    if isinstance(value, str):
        return _placeholder(value)

    return value


def _placeholder(value: str) -> str:
    if _UUID_RE.match(value):
        return UUID

    if _TIMESTAMP_RE.match(value):
        return TIMESTAMP

    if _PREFIXED_ID_RE.match(value):
        return ID

    return value
