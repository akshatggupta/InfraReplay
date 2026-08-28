"""The one deliberate indirection layer.

Loads everything registered under the `infrareplay.plugins` entry-point
group, validates each against its declared ABC, and hands them to the rest
of the system BY NAME ONLY. No caller imports a concrete plugin.
"""

from importlib.metadata import entry_points

from infrareplay.contracts import (
    CapturePlugin,
    ComparatorPlugin,
    RedactionPlugin,
    StoragePlugin,
)

ENTRY_POINT_GROUP = "infrareplay.plugins"

_ABC_BY_SLOT = {
    "capture": CapturePlugin,
    "storage": StoragePlugin,
    "redaction": RedactionPlugin,
    "comparator": ComparatorPlugin,
}


class PluginError(RuntimeError):
    pass


class PluginRegistry:
    """Name -> plugin class, grouped by contract."""

    def __init__(self) -> None:
        self._slots: dict[str, dict[str, type]] = {s: {} for s in _ABC_BY_SLOT}
        self._loaded = False

    # ------------------------------------------------------------------ load

    def load(self) -> "PluginRegistry":
        if self._loaded:
            return self

        for ep in entry_points(group=ENTRY_POINT_GROUP):
            self._register(ep.name, ep.load())

        self._loaded = True
        return self

    def _register(self, ep_name: str, obj: type) -> None:
        slot = self._slot_for(obj)

        if slot is None:
            raise PluginError(
                f"entry point {ep_name!r} -> {obj!r} implements no known contract"
            )

        name = getattr(obj, "name", "") or ep_name
        self._slots[slot][name] = obj

    @staticmethod
    def _slot_for(obj: type) -> str | None:
        for slot, abc in _ABC_BY_SLOT.items():
            if isinstance(obj, type) and issubclass(obj, abc):
                return slot

        return None

    # --------------------------------------------------------------- lookup

    def capture(self, name: str) -> type[CapturePlugin]:
        return self._get("capture", name)

    def storage(self, name: str) -> type[StoragePlugin]:
        return self._get("storage", name)

    def redaction(self, name: str) -> type[RedactionPlugin]:
        return self._get("redaction", name)

    def comparator_for(self, event_type: str) -> type[ComparatorPlugin] | None:
        for cls in self._slots["comparator"].values():
            if cls.event_type == event_type:
                return cls

        return None

    def _get(self, slot: str, name: str) -> type:
        try:
            return self._slots[slot][name]
        except KeyError:
            known = ", ".join(sorted(self._slots[slot])) or "(none)"
            raise PluginError(f"no {slot} plugin named {name!r}; known: {known}")

    # ------------------------------------------------------------------ list

    def describe(self) -> list[dict]:
        rows: list[dict] = []

        for slot, plugins in self._slots.items():
            for name, cls in sorted(plugins.items()):
                rows.append(
                    {
                        "name": name,
                        "slot": slot,
                        "impl": f"{cls.__module__}.{cls.__qualname__}",
                    }
                )

        return rows


_REGISTRY: PluginRegistry | None = None


def get_registry() -> PluginRegistry:
    """Process-wide singleton, loaded on first use."""

    global _REGISTRY

    if _REGISTRY is None:
        _REGISTRY = PluginRegistry().load()

    return _REGISTRY
