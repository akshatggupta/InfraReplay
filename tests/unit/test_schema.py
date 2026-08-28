import pytest
from pydantic import ValidationError

from infrareplay.schema import Event, EventType
from infrareplay.schema import migrations


def _event(**over) -> dict:
    base = dict(
        recording_id="rec_1",
        event_id="evt_1",
        sequence=0,
        timestamp_ns=1,
        duration_ns=1,
        event_type=EventType.HTTP_REQUEST,
        service="svc",
        correlation_id="corr_1",
    )
    base.update(over)
    return base


def test_event_defaults_schema_version_1():
    assert Event(**_event()).schema_version == 1


def test_event_rejects_unknown_event_type():
    with pytest.raises(ValidationError):
        Event(**_event(event_type="kafka.produce"))


def test_migration_chain_runs_registered_steps():
    @migrations.register(1)
    def _v1_to_v2(raw: dict) -> dict:
        raw["schema_version"] = 2
        raw["added"] = True
        return raw

    out = migrations.upgrade({"schema_version": 1}, to_version=2)

    assert out == {"schema_version": 2, "added": True}
    migrations._MIGRATIONS.clear()


def test_migration_missing_step_raises():
    with pytest.raises(ValueError):
        migrations.upgrade({"schema_version": 1}, to_version=9)
