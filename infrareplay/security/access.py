"""Who is calling? API keys in, an audit actor out.

    INFRAREPLAY_API_KEYS="ci:s3cret,dana:t0ken"

    x-api-key: t0ken   ->  actor "dana"
    x-api-key: wrong   ->  denied
    (no keys set)      ->  actor "anonymous", API open

Comparison is constant-time, so the response time leaks nothing about how
much of a guessed key was right.
"""

import hmac

from infrareplay import config

API_KEY_HEADER = "x-api-key"
ANONYMOUS = "anonymous"


def auth_enabled() -> bool:
    return bool(config.API_KEYS)


def resolve_actor(provided: str | None) -> str | None:
    """Return the caller's label, or None when the key is refused."""

    if not auth_enabled():
        return ANONYMOUS

    if not provided:
        return None

    for secret, label in config.API_KEYS.items():
        if hmac.compare_digest(secret.encode(), provided.encode()):
            return label

    return None
