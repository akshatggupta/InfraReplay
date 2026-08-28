"""Swap dynamic values (UUIDs, timestamps, tokens) before a request is sent.

v1 supports literal string replacement via an explicit mapping. Later
versions extend this with pattern-based rules (§16).
"""

from copy import deepcopy


class SubstitutionEngine:
    def __init__(self, mapping: dict[str, str] | None = None) -> None:
        self._mapping = dict(mapping or {})

    def apply(self, payload: dict) -> dict:
        if not self._mapping:
            return deepcopy(payload)

        return self._walk(deepcopy(payload))

    def _walk(self, value):
        if isinstance(value, dict):
            return {k: self._walk(v) for k, v in value.items()}

        if isinstance(value, list):
            return [self._walk(v) for v in value]

        if isinstance(value, str):
            return self._mapping.get(value, value)

        return value
