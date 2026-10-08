"""The audit log: every capture, replay and bundle action, and every refusal.

Written at the API boundary, where the caller is known — the CLI and the
dashboard both go through the API, so nothing reaches a recording without
passing here. Entries are only ever appended.
"""

from enum import Enum

from sqlalchemy import select

from infrareplay.db import AuditRow, get_session, init_db

_DEFAULT_LIMIT = 100


class AuditAction(str, Enum):
    CAPTURE_START = "capture.start"
    CAPTURE_STOP = "capture.stop"
    REPLAY_RUN = "replay.run"
    RECORDING_EXPORT = "recording.export"
    RECORDING_IMPORT = "recording.import"
    DEMO_SEED = "demo.seed"
    AUTH_DENIED = "auth.denied"


class Outcome(str, Enum):
    OK = "ok"
    FAILED = "failed"
    DENIED = "denied"


async def record(
    action: AuditAction,
    *,
    actor: str,
    outcome: Outcome = Outcome.OK,
    recording_id: str | None = None,
    target: str | None = None,
    detail: dict | None = None,
) -> None:
    await init_db()

    async with get_session() as session:
        session.add(
            AuditRow(
                action=action.value,
                actor=actor,
                outcome=outcome.value,
                recording_id=recording_id,
                target=target,
                detail=detail or {},
            )
        )
        await session.commit()


async def list_entries(
    *,
    limit: int = _DEFAULT_LIMIT,
    action: AuditAction | None = None,
    recording_id: str | None = None,
) -> list[dict]:
    """Newest first."""

    await init_db()

    async with get_session() as session:
        stmt = select(AuditRow).order_by(AuditRow.id.desc()).limit(limit)

        if action is not None:
            stmt = stmt.where(AuditRow.action == action.value)

        if recording_id is not None:
            stmt = stmt.where(AuditRow.recording_id == recording_id)

        rows = (await session.scalars(stmt)).all()

        return [
            {
                "id": r.id,
                "created_at": r.created_at,
                "action": r.action,
                "actor": r.actor,
                "outcome": r.outcome,
                "recording_id": r.recording_id,
                "target": r.target,
                "detail": r.detail,
            }
            for r in rows
        ]
