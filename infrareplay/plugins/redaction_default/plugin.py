"""DefaultRedactionPlugin — masks known-sensitive header and body keys."""

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

        return value

    def _scrub_entry(self, key: str, value):
        if key.lower() in _SENSITIVE_KEYS:
            return REDACTED

        return self._scrub(value)
