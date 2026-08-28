"""App factory and route definitions.

Routes are thin: parse the request, call a service, return the model. All
business logic lives in `recording`, `replay`, and `comparison`.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from infrareplay import demo
from infrareplay.db import init_db
from infrareplay.plugins.registry import get_registry
from infrareplay.recording import get_comparison, get_recording, list_recordings
from infrareplay.replay.engine import ReplayError, SafetyMode
from infrareplay.replay.runner import run_replay
from infrareplay.schema import ComparisonResult, Recording, RecordingKind

_NOT_FOUND = 404
_BAD_REQUEST = 400


class ReplayRequest(BaseModel):
    recording_id: str
    target: str = "mock"
    unsafe: bool = False
    substitutions: dict[str, str] | None = None


class ReplayResponse(BaseModel):
    replay_recording_id: str
    source_recording_id: str
    target: str
    summary: dict[str, int]


@asynccontextmanager
async def _lifespan(_: FastAPI):
    await init_db()
    get_registry()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="InfraReplay", version="0.1.0", lifespan=_lifespan)

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.get("/api/plugins")
    async def plugins() -> list[dict]:
        return get_registry().describe()

    @app.post("/api/demo/seed", response_model=list[Recording])
    async def seed() -> list[Recording]:
        return await demo.seed()

    @app.get("/api/recordings", response_model=list[Recording])
    async def recordings(kind: RecordingKind | None = None) -> list[Recording]:
        return await list_recordings(kind)

    @app.get("/api/recordings/{recording_id}", response_model=Recording)
    async def recording(recording_id: str) -> Recording:
        found = await get_recording(recording_id)

        if found is None:
            raise HTTPException(_NOT_FOUND, f"recording {recording_id} not found")

        return found

    @app.get("/api/replays", response_model=list[Recording])
    async def replays() -> list[Recording]:
        return await list_recordings(RecordingKind.REPLAY)

    @app.post("/api/replays", response_model=ReplayResponse)
    async def create_replay(req: ReplayRequest) -> ReplayResponse:
        safety = SafetyMode.UNSAFE if req.unsafe else SafetyMode.SAFE

        try:
            run, summary = await run_replay(
                recording_id=req.recording_id,
                target=req.target,
                safety=safety,
                substitutions=req.substitutions,
            )
        except ReplayError as exc:
            raise HTTPException(_BAD_REQUEST, str(exc))

        return ReplayResponse(
            replay_recording_id=run.replay_recording_id,
            source_recording_id=run.source_recording_id,
            target=run.target,
            summary=summary,
        )

    @app.get(
        "/api/replays/{replay_recording_id}/comparison",
        response_model=list[ComparisonResult],
    )
    async def comparison(replay_recording_id: str) -> list[ComparisonResult]:
        results = await get_comparison(replay_recording_id)

        if not results:
            raise HTTPException(_NOT_FOUND, "no comparison for that replay")

        return results

    return app


app = create_app()
