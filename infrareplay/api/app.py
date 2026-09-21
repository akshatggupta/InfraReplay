"""App factory and route definitions.

Routes are thin: parse the request, call a service, return the model. All
business logic lives in `recording`, `replay`, and `comparison`.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from infrareplay import demo
from infrareplay.capture import (
    CaptureError,
    active_sessions,
    ingest_events,
    start_session,
    stop_session,
)
from infrareplay.db import init_db
from infrareplay.plugins.registry import PluginError, get_registry
from infrareplay.recording import (
    BundleError,
    export_recording,
    get_comparison,
    get_recording,
    import_recording,
    list_projects,
    list_recordings,
    parse_bundle,
)
from infrareplay.replay.engine import ReplayError, SafetyMode
from infrareplay.replay.runner import run_replay
from infrareplay.replay.substitution import AutoMode
from infrareplay.schema import ComparisonResult, Event, Recording, RecordingKind

_NOT_FOUND = 404
_BAD_REQUEST = 400


class CaptureRequest(BaseModel):
    plugin: str = "http_capture"
    project: str = "default"
    title: str = ""

    # Plugin-specific: http_capture takes {upstream, listen_port}.
    config: dict = {}


class CaptureResponse(BaseModel):
    recording_id: str
    plugin: str
    title: str
    target: str = ""
    listen_port: int = 0


class ReplayRequest(BaseModel):
    recording_id: str
    target: str = "mock"
    unsafe: bool = False
    substitutions: dict[str, str] | None = None

    # Give UUID-shaped values a fresh identity on the way out.
    auto_substitute: bool = False

    # Requests sent at once — set it to reproduce a race.
    concurrency: int = 1


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

    # --------------------------------------------------------- live capture

    @app.post("/api/captures", response_model=CaptureResponse)
    async def start_capture(req: CaptureRequest) -> CaptureResponse:
        try:
            session = await start_session(
                plugin_name=req.plugin,
                project=req.project,
                title=req.title,
                config=req.config,
            )
        except (CaptureError, PluginError) as exc:
            raise HTTPException(_BAD_REQUEST, str(exc))

        return CaptureResponse(**session.describe())

    @app.get("/api/captures", response_model=list[CaptureResponse])
    async def captures() -> list[CaptureResponse]:
        return [CaptureResponse(**s) for s in active_sessions()]

    @app.post("/api/captures/{recording_id}/stop", response_model=Recording)
    async def stop_capture(recording_id: str) -> Recording:
        try:
            return await stop_session(recording_id)
        except CaptureError as exc:
            raise HTTPException(_BAD_REQUEST, str(exc))

    @app.post("/api/captures/{recording_id}/events")
    async def ingest(recording_id: str, events: list[Event]) -> dict:
        """Door for events produced in another process (see infrareplay.agent)."""

        try:
            return {"ingested": await ingest_events(recording_id, events)}
        except CaptureError as exc:
            raise HTTPException(_BAD_REQUEST, str(exc))

    # ------------------------------------------------------------- recordings

    @app.get("/api/recordings", response_model=list[Recording])
    async def recordings(kind: RecordingKind | None = None) -> list[Recording]:
        return await list_recordings(kind)

    @app.get("/api/recordings/{recording_id}", response_model=Recording)
    async def recording(recording_id: str) -> Recording:
        found = await get_recording(recording_id)

        if found is None:
            raise HTTPException(_NOT_FOUND, f"recording {recording_id} not found")

        return found

    @app.get("/api/recordings/{recording_id}/export")
    async def export(recording_id: str) -> dict:
        bundle = await export_recording(recording_id)

        if bundle is None:
            raise HTTPException(_NOT_FOUND, f"recording {recording_id} not found")

        return bundle

    @app.post("/api/recordings/import", response_model=Recording)
    async def import_bundle(bundle: dict) -> Recording:
        try:
            return await import_recording(bundle)
        except BundleError as exc:
            raise HTTPException(_BAD_REQUEST, str(exc))

    @app.post("/api/recordings/validate")
    async def validate_bundle(bundle: dict) -> dict:
        """Answers "would this import?" without writing anything."""

        try:
            recording = parse_bundle(bundle)
        except BundleError as exc:
            return {"valid": False, "error": str(exc)}

        return {
            "valid": True,
            "recording_id": recording.recording_id,
            "events": len(recording.events),
            "schema_version": recording.schema_version,
        }

    @app.get("/api/projects")
    async def projects() -> list[dict]:
        return await list_projects()

    # ----------------------------------------------------------------- replay

    @app.get("/api/replays", response_model=list[Recording])
    async def replays() -> list[Recording]:
        return await list_recordings(RecordingKind.REPLAY)

    @app.post("/api/replays", response_model=ReplayResponse)
    async def create_replay(req: ReplayRequest) -> ReplayResponse:
        safety = SafetyMode.UNSAFE if req.unsafe else SafetyMode.SAFE
        auto = AutoMode.FRESH_IDS if req.auto_substitute else AutoMode.OFF

        try:
            run, summary = await run_replay(
                recording_id=req.recording_id,
                target=req.target,
                safety=safety,
                substitutions=req.substitutions,
                auto=auto,
                concurrency=req.concurrency,
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
