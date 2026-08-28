# InfraReplay — Python Edition, Incremental Build Spec (v1 → vN)

> Rewrite of the original Go/React InfraReplay spec into a pure-Python,
> plugin-extensible, incrementally-versioned project. Give this whole file
> to a coding agent as its build brief. Each version below must be fully
> working and tested before the next version starts.

---

## 0. What This Project Is

InfraReplay records real application workflows (HTTP requests + the
PostgreSQL activity they trigger), stores them as a portable recording,
and replays them later against a different environment so engineers can
reproduce and compare incidents instead of guessing.

**Core loop:** capture → correlate → store → replay → compare.

This document changes three things about the original spec:

1. **Language:** Go/React → Python end-to-end (backend, CLI, dashboard).
2. **Extensibility:** capture sources, storage backends, redaction rules,
   and comparators are now *plugins* discovered via Python entry points,
   not hardcoded modules — so third parties can add Kafka/gRPC/Redis
   support without forking the core.
3. **Delivery:** the whole project is restructured into versions v1…v13.
   Every version ends in a state a coding agent (or you) can `docker
   compose up` and demo. No version depends on unfinished work from a
   later version.

---

## 1. Tech Stack (Python)

| Concern | Choice | Notes |
|---|---|---|
| API server | **FastAPI** + Uvicorn | async, typed, OpenAPI for free |
| CLI | **Typer** | `infractl` command, subcommands map 1:1 to API |
| Data validation / event schema | **Pydantic v2** | schema versioning built in |
| ORM / migrations | **SQLAlchemy 2.0 (async)** + **Alembic** | metadata DB only |
| HTTP capture proxy | **Starlette** ASGI app + **httpx.AsyncClient** | reverse proxy, async I/O |
| Postgres capture | SQLAlchemy engine **event listeners** (`before_cursor_execute` / `after_cursor_execute`) or a thin `asyncpg`/`psycopg` wrapper | app-level instrumentation, not a wire proxy — matches original MVP scope |
| Object storage (large payloads) | **aioboto3** (S3 API) | local dev → MinIO, prod → S3-compatible |
| Dashboard (MVP) | **NiceGUI** | pure Python, no separate frontend build |
| Dashboard (later, optional) | React/Next, same REST API | only if NiceGUI's UI ceiling becomes a real limiter |
| Metrics | **prometheus-client** | `/metrics` endpoint |
| Logging | **structlog** | structured JSON logs |
| Plugin discovery | **`importlib.metadata.entry_points`** | group `infrareplay.plugins` |
| Packaging | `pyproject.toml`, installable as `pip install infrareplay` | plugins are separate installable packages |
| Testing | **pytest**, **pytest-asyncio**, **testcontainers-python** | integration tests spin up real Postgres/MinIO in Docker |
| Local orchestration | **Docker Compose** | unchanged from original spec |
| Load/benchmark tooling | **Locust** | Python-native, replaces custom Go benchmark harness |

**Why NiceGUI over Streamlit for the dashboard:** Streamlit re-runs the
whole script per interaction, which is awkward for a timeline/click-to-
inspect UI. NiceGUI gives you real per-element event handlers while
staying pure Python.

---

## 2. Plugin Architecture (the "usable by everyone" part)

Everything that InfraReplay needs to be extensible about is defined as
an abstract interface in `infrareplay.contracts`. Plugins implement one
of these and register themselves in their own package's `pyproject.toml`:

```toml
[project.entry-points."infrareplay.plugins"]
http_capture = "infrareplay_http:HttpCapturePlugin"
postgres_capture = "infrareplay_postgres:PostgresCapturePlugin"
```

Core interfaces (sketch — implement as real ABCs in v1):

```python
# infrareplay/contracts/capture.py
from abc import ABC, abstractmethod
from infrareplay.schema import Event

class CapturePlugin(ABC):
    name: str
    event_types: list[str]

    @abstractmethod
    async def start(self, config: dict) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    def events(self) -> "AsyncIterator[Event]": ...


# infrareplay/contracts/storage.py
class StoragePlugin(ABC):
    @abstractmethod
    async def put_blob(self, key: str, data: bytes) -> str: ...

    @abstractmethod
    async def get_blob(self, key: str) -> bytes: ...


# infrareplay/contracts/redaction.py
class RedactionPlugin(ABC):
    @abstractmethod
    def redact(self, event: Event) -> Event: ...


# infrareplay/contracts/comparator.py
class ComparatorPlugin(ABC):
    event_type: str

    @abstractmethod
    def compare(self, original: Event, replayed: Event) -> "ComparisonResult": ...
```

The core ships five built-in plugins — **mock capture** (synthetic test
data, see v1), HTTP capture, Postgres capture, filesystem/S3 storage,
and default field-redaction — registered the same way a third party's
plugin would be. **The core never special-cases its own plugins** — this
is what keeps the system genuinely pluggable instead of "pluggable in
theory." `MockCapturePlugin` in particular is what lets v1 demo the
entire product before any real capture code exists: it's proof, from
day one, that the API/storage/replay/comparison layers only ever talk
to the `CapturePlugin` interface, never to a concrete implementation.

A registry (`infrareplay.plugins.registry`) loads all entry points at
startup, validates they implement the right ABC, and exposes them to the
CLI (`infractl plugins list`) and the capture/replay engines by name.

This is also what makes the Go/Rust-sidecar escape hatch from §0 real:
a future high-performance capture proxy just needs to speak the same
`Event` schema over the same storage interface — it doesn't need to be
a `CapturePlugin` Python object at all, just something that writes
schema-compatible events.

---

## 3. Repository Structure

```
infra-replay/
├── infrareplay/                  # core installable package
│   ├── contracts/                # ABCs: capture, storage, redaction, comparator
│   ├── schema/                   # Pydantic Event models, schema_version
│   ├── plugins/                  # registry + built-in plugins
│   │   ├── http_capture/
│   │   ├── postgres_capture/
│   │   ├── storage_fs/
│   │   ├── storage_s3/
│   │   └── redaction_default/
│   ├── recording/                # lifecycle, metadata
│   ├── replay/                   # replay engine, substitutions
│   ├── comparison/                # comparators, result types
│   ├── api/                      # FastAPI app
│   ├── cli/                      # Typer app (infractl)
│   └── dashboard/                # NiceGUI app
├── migrations/                   # Alembic
├── examples/
│   └── demo_shop/                # FastAPI + Postgres demo app with intentional bug
├── benchmark/                    # Locust files
├── tests/
│   ├── unit/
│   └── integration/               # testcontainers-based
├── docs/
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
└── README.md
```

---

## 4. Versioned Roadmap (v1 → v12)

Each version has a **Definition of Done** the coding agent must satisfy
before moving on. Do not start v(N+1) work until v(N)'s DoD passes.

**Key idea for v1:** don't build plumbing with nothing to look at. Build
the *entire pipeline* — record → timeline → replay → compare — end to
end in v1, but power it with a `MockCapturePlugin` that generates
realistic synthetic events instead of a real HTTP proxy or DB
instrumentation. This is not a throwaway demo: it's the real API,
schema, storage, replay engine, comparator, and dashboard, exercised
through a fake data source. Later versions (v2, v3) add *real* capture
plugins that plug into that exact same pipeline — nothing built in v1
gets rewritten, only the data source changes. This is also the first
proof that the plugin architecture in §2 actually works.

### v1 — Full Pipeline Demo (synthetic data) — the college-demo version
Build:
- Pydantic `Event`/`Recording`/`ReplayRun`/`ComparisonResult` schema
  (with `schema_version`).
- Alembic migrations for metadata.
- Plugin registry, real and working (not a stub).
- **`MockCapturePlugin`**: generates a believable "record a workflow"
  session — e.g. `POST /orders → INSERT order → UPDATE inventory →
  SELECT payment`, with realistic timestamps, durations, and a shared
  `correlation_id` — without any real network/DB traffic. Ships two
  fixture scenarios: one "clean" recording and one "buggy" variant (e.g.
  the payment step fails) so replay/comparison has something real to
  show.
- `storage_fs` plugin (local filesystem, no MinIO needed yet).
- FastAPI: `/api/recordings`, `/api/replays`, `/api/replays/:id/comparison`.
- Typer CLI: `infractl demo seed`, `infractl recordings list/show`,
  `infractl replay <id> --target mock`, `infractl replay compare <id>`.
- NiceGUI dashboard: recordings list → timeline view (nested HTTP→DB
  tree, per §19) → replay button → side-by-side comparison view
  (`MATCH/DIFFERENT/ERROR`, per §18).

**DoD (this is what you present to college):** `docker compose up`,
`infractl demo seed`, open the dashboard, click into a recording, see
the full timeline, hit Replay, see a comparison result rendered with at
least one `DIFFERENT` and one `MATCH` category — all driven by seed
data, zero real capture code required. This is a complete, honest
working prototype of the *concept*; it is transparent in the README that
capture is mocked at this stage.

### v2 — Real HTTP Capture Plugin
Implement `http_capture` as a real `CapturePlugin`: Starlette reverse
proxy, correlation-ID generation, header/body redaction using
`redaction_default`, size limits, recording lifecycle
(`CREATING→RECORDING→COMPLETED/FAILED`). Swap it in alongside
`MockCapturePlugin` (don't delete the mock — keep it for tests/demos).
**DoD:** `infractl capture start --listen :8080 --upstream :3000`,
proxy a curl request through it, `infractl capture stop`, recording
appears in the same dashboard/timeline built in v1, with correctly
redacted `Authorization` header.

### v3 — Real PostgreSQL Capture + Correlation
`postgres_capture` plugin via SQLAlchemy event listeners; correlation ID
propagated from the in-flight HTTP request context (e.g. `contextvars`)
into every DB event.
**DoD:** one *really* recorded HTTP request that triggers 2+ DB queries
shows all events sharing one `correlation_id`, in the right `sequence`
order, in the v1 dashboard.

### v4 — Real Replay Engine
`infractl replay <id> --target <url>` now replays real captured
recordings (not just mock ones) against a live target; safety check
blocking obvious production hosts unless `--unsafe` is passed; basic
substitution engine for dynamic values (UUIDs, timestamps) per §16.
**DoD:** replaying a *really captured* `/orders` request against a
local target produces a new `ReplayRun` through the same engine built
in v1.

### v5 — Comparison Engine Hardening
The comparator interfaces already exist from v1 (they had to, to render
mock comparisons) — this version makes them robust against real-world
noise: timestamp jitter, non-deterministic IDs, floating latency.
**DoD:** replaying the same real recording twice against an unchanged
target yields `MATCH` both times (no false `DIFFERENT`s from jitter).

### v6 — Demo Shop + Intentional Bug
Build `examples/demo_shop` (FastAPI + Postgres): orders, inventory,
payments, users. Introduce the concurrent-inventory-reservation bug from
§21.
**DoD:** the full README demo works end-to-end on *real* capture: record
→ bug occurs → replay → bug reproduced → fix → replay again →
comparison shows fix.

### v7 — CLI Completeness + Export/Import
`export`, `validate`, `projects list` (recordings list/show/replay
already exist from v1).
**DoD:** a recording can be exported to a single file and re-imported
into a fresh instance and replayed there.

### v8 — Security Hardening
Recording encryption at rest (age/Fernet on stored blobs), API access
control (API keys minimum), audit log of capture/replay actions, never
expose raw secrets via API (test this explicitly).
**DoD:** automated tests assert secrets never appear in stored
recordings or API responses; audit log entries exist for every
capture/replay action.

### v9 — Observability
Prometheus metrics (`recordings_total`, `events_captured_total`,
`capture_errors_total`, `replay_total`, `replay_failures_total`,
`storage_bytes`, etc.) + structlog JSON logs.
**DoD:** `/metrics` is scrapeable; a Grafana-importable dashboard JSON
is included in `docs/`.

### v10 — Benchmark Suite
Locust-based load generator hitting the demo shop with/without capture
enabled; measure req/s, P50/P95/P99, CPU, memory, captured bytes/sec, at
light/medium/heavy loads per §23. Publish methodology, not just numbers.
**DoD:** `benchmark/run.sh` produces a reproducible report; README links
to it; no performance claim is made without this report backing it.

### v11 — External Service Mocking
Stub Payment/Email/SMS/Shipping APIs per §17 so replay never hits real
external services by default.
**DoD:** replaying a recording that originally called a payment API
hits the mock instead, and this is the default with no flag needed.

### v12 — Post-MVP Plugins (prove the plugin system, take two)
Only after v1–v11 are stable: build **one** external protocol capture
plugin (Kafka is the best proof) as a *separate installable package*
that a user `pip install`s and it registers itself with zero core code
changes.
**DoD:** `pip install infrareplay-kafka-capture && infractl plugins
list` shows it, and it produces schema-compatible `Event`s the core
UI/replay/comparison code handles without modification.

---

## 5. Event Schema (Pydantic, v1 baseline)

```python
class Event(BaseModel):
    schema_version: int = 1
    recording_id: str
    event_id: str
    parent_event_id: str | None = None
    sequence: int
    timestamp_ns: int
    duration_ns: int
    event_type: Literal["http.request", "http.response",
                         "postgres.query", "postgres.result"]
    service: str
    correlation_id: str
    trace_id: str | None = None
    span_id: str | None = None
    payload: dict
```

Schema versioning rule: never mutate a shipped version's field meaning.
New fields are additive with defaults; breaking changes bump
`schema_version` and require a migration function registered in
`infrareplay.schema.migrations`.

---

## 6. Non-Goals (unchanged from original spec, still apply)

Do not build, at any version below v13: Kafka/Redis/gRPC/K8s support, a
packet-level sniffer, a general observability platform, an
OpenTelemetry replacement, or a SaaS-only product. Integrate with
OpenTelemetry trace/span IDs where they already exist — do not invent a
parallel tracing system.

Do not, at any version: claim zero overhead, claim deterministic replay
before the benchmark suite (v11) proves it, capture secrets by default,
replay against production by default, or publish invented benchmark
numbers.

---

## 7. Testing Strategy Per Version

- **Unit tests** (pytest): schema validation, redaction rules,
  correlation-ID propagation, replay substitutions, comparator logic.
  Written *before* the feature they test, per version.
- **Integration tests** (testcontainers-python): spin up real Postgres +
  MinIO, run capture → store → replay → compare against the demo shop.
  Required starting v3 (once there's real DB traffic to correlate).
- **Failure tests**: app crash mid-capture, DB error, malformed request,
  timeout, partial recording, oversized payload, duplicate event, replay
  target unreachable — required starting v5.

---

## 8. Instructions for the Coding Agent

1. Work strictly in version order; each version's DoD must pass before
   starting the next.
2. Before writing code for a version, write/update: the architecture
   note for that version in `docs/`, the Pydantic models it needs, and
   the Alembic migration if the metadata schema changes.
3. Every plugin — including the four built-in ones — must be written
   against the ABCs in `infrareplay/contracts/`, registered via entry
   points, and loaded through the plugin registry. No core code may
   import a specific plugin implementation directly; it must go through
   the registry by plugin name.
4. Keep `docker compose up` working at the end of every version.
5. Update `README.md`'s demo transcript as each version changes what the
   CLI/dashboard actually does.
6. Do not begin v12 (or any external-protocol plugin) work until v1–v11
   pass their integration tests.