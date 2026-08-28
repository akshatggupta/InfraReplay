"""Schema-version migration registry.

Rule (spec §5): never mutate a shipped version's field meaning. New fields
are additive with defaults. A breaking change bumps `schema_version` and
registers a migration function here, keyed by the version it upgrades FROM.
"""

from collections.abc import Callable

_MIGRATIONS: dict[int, Callable[[dict], dict]] = {}


def register(from_version: int):
    def wrap(fn: Callable[[dict], dict]) -> Callable[[dict], dict]:
        _MIGRATIONS[from_version] = fn
        return fn

    return wrap


def upgrade(raw: dict, to_version: int) -> dict:
    """Apply chained migrations until `raw` reaches `to_version`."""

    version = raw.get("schema_version", 1)

    while version < to_version:
        migrate = _MIGRATIONS.get(version)

        if migrate is None:
            raise ValueError(f"no migration registered from schema_version {version}")

        raw = migrate(raw)
        version = raw["schema_version"]

    return raw
