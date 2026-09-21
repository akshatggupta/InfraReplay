"""Swap dynamic values (UUIDs, timestamps, tokens) before a request is sent.

Two layers, applied in order to every string in the payload:

  1. the caller's explicit mapping — `{"ord_9f2c": "ord_new"}`; values may be
     templates (`${uuid}`, `${now_iso}`, `${now_ns}`) expanded once per run;
  2. auto mode — every UUID-shaped string gets a fresh UUID, the *same*
     replacement each time that value reappears, so a request body and the
     header that references it stay consistent.
"""

import re
from copy import deepcopy
from datetime import datetime, timezone
from enum import Enum
from time import time_ns
from uuid import uuid4

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)
_TEMPLATE_RE = re.compile(r"\$\{(\w+)\}")


class AutoMode(str, Enum):
    OFF = "off"
    FRESH_IDS = "fresh_ids"


def _template_value(name: str) -> str:
    if name == "uuid":
        return str(uuid4())

    if name == "now_iso":
        return datetime.now(timezone.utc).isoformat()

    if name == "now_ns":
        return str(time_ns())

    return "${" + name + "}"


class SubstitutionEngine:
    def __init__(
        self,
        mapping: dict[str, str] | None = None,
        *,
        auto: AutoMode = AutoMode.OFF,
    ) -> None:
        self._mapping = {k: self._expand(v) for k, v in (mapping or {}).items()}
        self._auto = auto

        # Stable per-run memory so one original value always maps to one
        # replacement across every event in the run.
        self._generated: dict[str, str] = {}

    @staticmethod
    def _expand(value: str) -> str:
        return _TEMPLATE_RE.sub(lambda m: _template_value(m.group(1)), value)

    def apply(self, payload: dict) -> dict:
        if not self._mapping and self._auto is AutoMode.OFF:
            return deepcopy(payload)

        return self._walk(deepcopy(payload))

    def _walk(self, value):
        if isinstance(value, dict):
            return {k: self._walk(v) for k, v in value.items()}

        if isinstance(value, list):
            return [self._walk(v) for v in value]

        if isinstance(value, str):
            return self._swap(value)

        return value

    def _swap(self, value: str) -> str:
        if value in self._mapping:
            return self._mapping[value]

        if self._auto is AutoMode.OFF or not _UUID_RE.match(value):
            return value

        return self._generated.setdefault(value, str(uuid4()))
