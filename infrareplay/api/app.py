"""App factory and route definitions.

Routes are thin: parse the request, call a service, return the model. All
business logic lives in `recording`, `replay`, and `comparison`.
"""

import json
from contextlib import asynccontextmanager
from time import perf_counter
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from infrareplay import config, demo
from infrareplay.capture.context import ExternalsMode
from infrareplay.demo_traffic import record_traffic
from infrareplay.capture import (
    CaptureError,
    active_sessions,
    ingest_events,
    start_session,
    stop_session,
)
from infrareplay.db import init_db
from infrareplay.observability import (
    bind_request,
    clear_request,
    configure,
    get_logger,
    metrics,
)
from infrareplay.plugins.registry import PluginError, get_registry
from infrareplay.recording import (
    BundleError,
    export_recording,
    list_externals,
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
from infrareplay.security import (
    ANONYMOUS,
    API_KEY_HEADER,
    AuditAction,
    Outcome,
    auth_enabled,
    list_entries,
    record,
    resolve_actor,
)

_NOT_FOUND = 404
_BAD_REQUEST = 400
_UNAUTHORIZED = 401
_BAD_GATEWAY = 502

# Open without a key: liveness, scraping, and the API's own documentation.
_PUBLIC_PATHS = frozenset(
    {"/health", "/metrics", "/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"}
)

_UNKNOWN_CALLER = "unknown"

_REQUEST_ID_HEADER = "x-request-id"
_UNMATCHED_ROUTE = "unmatched"

_log = get_logger(__name__)


def _actor(request: Request) -> str:
    return getattr(request.state, "actor", ANONYMOUS)


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

    # The live shop unless named; "mock" is the synthetic stand-in for tests.
    target: str = Field(default_factory=lambda: config.DEFAULT_TARGET)
    unsafe: bool = False
    substitutions: dict[str, str] | None = None

    # Give UUID-shaped values a fresh identity on the way out.
    auto_substitute: bool = False

    # Requests sent at once — set it to reproduce a race.
    concurrency: int = 1

    # Outbound calls the target makes: answered from the recording (default)
    # or, only when asked, really sent (v11).
    externals: ExternalsMode = ExternalsMode.RECORDED


class TrafficRequest(BaseModel):
    """Real checkouts through a live capture of the shop — no fixtures."""

    upstream: str = Field(default_factory=lambda: config.DEMO_SHOP_URL)
    title: str = "Checkout — live traffic"
    checkouts: int = Field(3, ge=1, le=50)

    # How many of them use a card the gateway declines.
    declined: int = Field(1, ge=0)
    concurrent: bool = False

    # Restock the shop first (every product), so ordinary traffic is not
    # refused for lack of stock; 1 sets up the oversell race; null leaves it.
    stock: int | None = 100


class ReplayResponse(BaseModel):
    replay_recording_id: str
    source_recording_id: str
    target: str
    summary: dict[str, int]


@asynccontextmanager
async def _lifespan(_: FastAPI):
    configure()
    await init_db()
    get_registry()

    _log.info(
        "api.started",
        auth="api-keys" if auth_enabled() else "open",
        storage=config.STORAGE_PLUGIN,
    )

    if not auth_enabled():
        _log.warning("api.auth_open", hint="set INFRAREPLAY_API_KEYS to require keys")

    yield


def create_app() -> FastAPI:
    app = FastAPI(title="InfraReplay", version="0.1.0", lifespan=_lifespan)

    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        """Every /api route needs a valid x-api-key once keys are configured.

        A refusal is itself audited: a run of auth.denied rows is what a
        probe looks like.
        """

        if request.url.path in _PUBLIC_PATHS:
            return await call_next(request)

        actor = resolve_actor(request.headers.get(API_KEY_HEADER))

        if actor is None:
            metrics.AUTH_DENIED.inc()
            _log.warning("auth.denied", method=request.method, path=request.url.path)

            await record(
                AuditAction.AUTH_DENIED,
                actor=_UNKNOWN_CALLER,
                outcome=Outcome.DENIED,
                target=f"{request.method} {request.url.path}",
            )
            return JSONResponse(
                {"detail": f"missing or invalid {API_KEY_HEADER}"},
                status_code=_UNAUTHORIZED,
            )

        request.state.actor = actor
        return await call_next(request)

    @app.middleware("http")
    async def observe(request: Request, call_next):
        """Request id, latency histogram, one log line per request.

        Registered after `authenticate`, so it wraps it: refused calls are
        timed and logged too, under the same request id.
        """

        request_id = request.headers.get(_REQUEST_ID_HEADER) or uuid4().hex[:16]

        clear_request()
        bind_request(request_id=request_id)

        started = perf_counter()
        response = await call_next(request)
        seconds = perf_counter() - started

        # The route *template*, never the raw path: ids would explode the series.
        route = getattr(request.scope.get("route"), "path", _UNMATCHED_ROUTE)

        metrics.HTTP_SECONDS.labels(request.method, route, str(response.status_code)).observe(seconds)
        response.headers[_REQUEST_ID_HEADER] = request_id

        _log.info(
            "http.request",
            method=request.method,
            route=route,
            status=response.status_code,
            ms=round(seconds * 1000, 2),
            actor=getattr(request.state, "actor", None),
        )

        return response

    @app.get("/metrics", include_in_schema=False)
    async def prometheus() -> Response:
        body, content_type = metrics.render()

        return Response(content=body, media_type=content_type)

    @app.get("/health")
    async def health() -> dict:
        # Says out loud when the API is open, so nobody assumes otherwise.
        return {
            "status": "ok",
            "auth": "api-keys" if auth_enabled() else "open",
            "storage": config.STORAGE_PLUGIN,
        }

    @app.get("/api/plugins")
    async def plugins() -> list[dict]:
        return get_registry().describe()

    @app.post("/api/demo/traffic", response_model=Recording)
    async def demo_traffic(req: TrafficRequest, request: Request) -> Recording:
        """Record real checkout traffic: real HTTP, SQL and payment calls."""

        try:
            recording = await record_traffic(
                upstream=req.upstream,
                title=req.title,
                checkouts=req.checkouts,
                declined=min(req.declined, req.checkouts),
                concurrent=req.concurrent,
                stock=req.stock,
            )
        except (CaptureError, httpx.HTTPError) as exc:
            raise HTTPException(_BAD_GATEWAY, f"could not drive the shop at {req.upstream}: {exc}")

        await record(
            AuditAction.DEMO_TRAFFIC,
            actor=_actor(request),
            recording_id=recording.recording_id,
            target=req.upstream,
            detail={"checkouts": req.checkouts, "events": len(recording.events)},
        )

        return recording

    @app.get("/api/benchmarks/latest")
    async def benchmark_latest() -> dict:
        """The newest benchmark/run.sh report (v10), as its summary.json."""

        summary = config.BENCHMARK_DIR / "latest" / "summary.json"

        if not summary.exists():
            raise HTTPException(_NOT_FOUND, "no benchmark report yet: run benchmark/run.sh")

        return json.loads(summary.read_text())

    @app.post("/api/demo/seed", response_model=list[Recording])
    async def seed(request: Request) -> list[Recording]:
        seeded = await demo.seed()

        await record(
            AuditAction.DEMO_SEED,
            actor=_actor(request),
            detail={"recordings": [r.recording_id for r in seeded]},
        )

        return seeded

    # --------------------------------------------------------- live capture

    @app.post("/api/captures", response_model=CaptureResponse)
    async def start_capture(req: CaptureRequest, request: Request) -> CaptureResponse:
        upstream = req.config.get("upstream")

        try:
            session = await start_session(
                plugin_name=req.plugin,
                project=req.project,
                title=req.title,
                config=req.config,
            )
        except (CaptureError, PluginError) as exc:
            await record(
                AuditAction.CAPTURE_START,
                actor=_actor(request),
                outcome=Outcome.FAILED,
                target=upstream,
                detail={"plugin": req.plugin, "error": str(exc)},
            )
            raise HTTPException(_BAD_REQUEST, str(exc))

        described = session.describe()

        await record(
            AuditAction.CAPTURE_START,
            actor=_actor(request),
            recording_id=session.recording_id,
            target=upstream,
            detail={"plugin": req.plugin, "listen_port": described["listen_port"]},
        )

        return CaptureResponse(**described)

    @app.get("/api/captures", response_model=list[CaptureResponse])
    async def captures() -> list[CaptureResponse]:
        return [CaptureResponse(**s) for s in active_sessions()]

    @app.post("/api/captures/{recording_id}/stop", response_model=Recording)
    async def stop_capture(recording_id: str, request: Request) -> Recording:
        try:
            stopped = await stop_session(recording_id)
        except CaptureError as exc:
            await record(
                AuditAction.CAPTURE_STOP,
                actor=_actor(request),
                outcome=Outcome.FAILED,
                recording_id=recording_id,
                detail={"error": str(exc)},
            )
            raise HTTPException(_BAD_REQUEST, str(exc))

        await record(
            AuditAction.CAPTURE_STOP,
            actor=_actor(request),
            recording_id=recording_id,
            detail={"events": len(stopped.events)},
        )

        return stopped

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
    async def export(recording_id: str, request: Request) -> dict:
        bundle = await export_recording(recording_id)

        await record(
            AuditAction.RECORDING_EXPORT,
            actor=_actor(request),
            outcome=Outcome.OK if bundle else Outcome.FAILED,
            recording_id=recording_id,
        )

        if bundle is None:
            raise HTTPException(_NOT_FOUND, f"recording {recording_id} not found")

        return bundle

    @app.post("/api/recordings/import", response_model=Recording)
    async def import_bundle(bundle: dict, request: Request) -> Recording:
        try:
            imported = await import_recording(bundle)
        except BundleError as exc:
            await record(
                AuditAction.RECORDING_IMPORT,
                actor=_actor(request),
                outcome=Outcome.FAILED,
                detail={"error": str(exc)},
            )
            raise HTTPException(_BAD_REQUEST, str(exc))

        await record(
            AuditAction.RECORDING_IMPORT,
            actor=_actor(request),
            recording_id=imported.recording_id,
            detail={"events": len(imported.events)},
        )

        return imported

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

    @app.get("/api/recordings/{recording_id}/externals")
    async def externals(recording_id: str, correlation_id: str) -> list[dict]:
        """The outbound calls one recorded request made, for replay to answer from."""

        return await list_externals(recording_id, correlation_id)

    @app.get("/api/projects")
    async def projects() -> list[dict]:
        return await list_projects()

    @app.get("/api/audit")
    async def audit(
        limit: int = 100,
        action: AuditAction | None = None,
        recording_id: str | None = None,
    ) -> list[dict]:
        return await list_entries(limit=limit, action=action, recording_id=recording_id)

    # ----------------------------------------------------------------- replay

    @app.get("/api/replays", response_model=list[Recording])
    async def replays() -> list[Recording]:
        return await list_recordings(RecordingKind.REPLAY)

    @app.post("/api/replays", response_model=ReplayResponse)
    async def create_replay(req: ReplayRequest, request: Request) -> ReplayResponse:
        safety = SafetyMode.UNSAFE if req.unsafe else SafetyMode.SAFE
        auto = AutoMode.FRESH_IDS if req.auto_substitute else AutoMode.OFF

        # Substitution *keys* only: the values may be fresh credentials.
        detail = {
            "unsafe": req.unsafe,
            "concurrency": req.concurrency,
            "externals": req.externals.value,
            "substituted": sorted(req.substitutions or {}),
        }

        try:
            run, summary = await run_replay(
                recording_id=req.recording_id,
                target=req.target,
                safety=safety,
                substitutions=req.substitutions,
                auto=auto,
                concurrency=req.concurrency,
                externals=req.externals,
            )
        except ReplayError as exc:
            await record(
                AuditAction.REPLAY_RUN,
                actor=_actor(request),
                outcome=Outcome.FAILED,
                recording_id=req.recording_id,
                target=req.target,
                detail={**detail, "error": str(exc)},
            )
            raise HTTPException(_BAD_REQUEST, str(exc))

        await record(
            AuditAction.REPLAY_RUN,
            actor=_actor(request),
            recording_id=req.recording_id,
            target=req.target,
            detail={**detail, "replay_recording_id": run.replay_recording_id, "summary": summary},
        )

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
