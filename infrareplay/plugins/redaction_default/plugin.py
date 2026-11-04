"""DefaultRedactionPlugin — masks secrets before an event is ever stored.

Two nets, applied everywhere in the payload:

  1. by key   — a value under `authorization`, `cookie`, `token`, `cvv`, …
                is replaced whole, whatever it looks like;
  2. by shape — a bearer token, a JWT or a provider key is masked wherever
                it appears, including inside SQL parameter lists and
                free-text fields that no key name would flag.

    {"note": "retry with eyJhbGciOi.eyJzdWIi.c2lnbmF0dXJl"}
        -> {"note": "retry with ***REDACTED***"}

Shape matching cannot know every secret format; it catches the common ones
that turn up in real traffic. Anything else needs its key on the list, or
on INFRAREPLAY_REDACT_KEYS for names only one application uses.
"""

import re

from infrareplay import config
from infrareplay.contracts import RedactionPlugin
from infrareplay.schema import Event

REDACTED = "***REDACTED***"

# Matched case-insensitively against header names and body keys.
_SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
        "password",
        "secret",
        "token",
        "access_token",
        "refresh_token",
        "card_number",
        "cvv",
    }
)

# Lengths are floors, so ordinary prose ("Bearer of good news") is left alone.
_SECRET_SHAPES = (
    re.compile(r"\bBearer\s+[A-Za-z0-9\-._~+/]{16,}=*", re.IGNORECASE),
    re.compile(r"\beyJ[\w-]{4,}\.[\w-]{4,}\.[\w-]{4,}"),
    re.compile(r"\b(?:sk|pk|rk|tok)_(?:live|test)_[A-Za-z0-9]{6,}"),
    # A card token, whatever key carries it: a shop forwards the customer's
    # `token` to its gateway as `source`, `card`, `payment_method`, …
    re.compile(r"\btok_[A-Za-z0-9_]{4,}"),
)


class DefaultRedactionPlugin(RedactionPlugin):
    name = "redaction_default"

    def redact(self, event: Event) -> Event:
        clone = event.model_copy(deep=True)
        clone.payload = self._scrub(clone.payload)

        return clone

    def _scrub(self, value):
        if isinstance(value, dict):
            return {k: self._scrub_entry(k, v) for k, v in value.items()}

        if isinstance(value, list):
            return [self._scrub(v) for v in value]

        if isinstance(value, str):
            return _mask_shapes(value)

        return value

    def _scrub_entry(self, key: str, value):
        lowered = key.lower()

        if lowered in _SENSITIVE_KEYS or lowered in config.REDACT_KEYS:
            return REDACTED

        return self._scrub(value)


def _mask_shapes(text: str) -> str:
    for shape in _SECRET_SHAPES:
        text = shape.sub(REDACTED, text)

    return text
