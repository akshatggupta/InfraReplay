# InfraReplay — Architecture

Companion document to `InfraReplay-Python-Spec.md`. That file is the
*build plan* (what to build, in what order). This file is the *system
design* (how the pieces fit together, and why). Give both to your
coding agent.

---

## 1. One-Line Pitch

Record a real HTTP+PostgreSQL workflow, store it as a portable
recording, replay it anywhere, and get an automatic diff — so
reproducing an incident stops being a manual guessing game.

---

## 2. System Context

```
                 ┌─────────────────────────────────────────┐
                 │              Engineer / User             │
                 └───────────────┬──────────────┬───────────┘
                                  │              │
                          CLI (infractl)   Dashboard (NiceGUI)
                                  │              │
                                  ▼              ▼
                         ┌─────────────────────────────┐
                         │        InfraReplay API        │
                         │           (FastAPI)           │
                         └───────┬───────────┬──────────┘
                                 │           │
                    ┌────────────┘           └────────────┐
                    ▼                                      ▼
          ┌───────────────────┐                 ┌───────────────────┐
          │   Plugin Registry   │                 │   Metadata Store    │
          │ (capture/storage/   │                 │    (PostgreSQL)     │
          │ redaction/compare)  │                 └───────────────────┘
          └─────────┬──────────┘
                     │
     ┌───────────────┼────────────────┬───────────────────┐
     ▼               ▼                ▼                    ▼
┌──────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐
│  Mock     │  │ HTTP Capture  │  │ Postgres      │  │ Blob Storage      │
│  Capture  │  │ (reverse proxy)│  │ Capture       │  │ (filesystem/S3)   │
└──────────┘  └──────┬────────┘  └──────┬────────┘  └───────────────────┘
                      │                  │
                      ▼                  ▼
             ┌─────────────────────────────────┐
             │        Target Application         │
             │   (e.g. examples/demo_shop)       │
             └─────────────────────────────────┘
```

Everything under "Plugin Registry" is swappable. The API, dashboard, and
CLI never talk to a concrete capture/storage implementation — only to
the interfaces in `infrareplay/contracts/`.

---

## 3. Components

### 3.1 API (FastAPI)
Owns all state transitions: recording lifecycle, replay runs,
comparisons. Every CLI command and every dashboard action is a thin
client of this API — there is exactly one source of truth, never two
code paths that both know how to "start a recording."

Core resources: `Project`, `Recording`, `Event`, `ReplayRun`,
`ComparisonResult`.

### 3.2 CLI (`infractl`, Typer)
1:1 mapping to API endpoints. No business logic lives in the CLI — it
formats requests and prints responses. This keeps the dashboard and CLI
guaranteed to behave identically, since both are just API clients.

### 3.3 Dashboard (NiceGUI)
Read/write UI over the same API. Three views drive the whole product
experience: **Recordings list → Timeline detail → Replay/Comparison**.
Built in v1 against `MockCapturePlugin` data; unchanged in v2/v3 when
real capture plugins are swapped in underneath.

### 3.4 Plugin Registry
Loads everything registered under the `infrareplay.plugins` entry-point
group at process start, validates each against its declared ABC
(`CapturePlugin`, `StoragePlugin`, `RedactionPlugin`, `ComparatorPlugin`),
and exposes them to the rest of the system **by name only**. This is the
one deliberate indirection layer in the whole system — see §5.

### 3.5 Capture Plugins
- **Mock Capture** — generates synthetic but schema-correct event
  streams. Used for demos, tests, and CI; never talks to a real network
  or database.
- **HTTP Capture** — a Starlette-based reverse proxy sitting in front of
  the target app. Captures request/response, redacts, tags with a
  correlation ID, forwards.
- **Postgres Capture** — SQLAlchemy event listeners (`before/after
  _cursor_execute`) reading the correlation ID out of the current
  `contextvars` context so DB activity is tied back to the HTTP request
  that caused it.

Both real plugins write into the *same* `Event` schema the mock plugin
does — this is what makes them interchangeable.

### 3.6 Storage
Two tiers, matching the original spec:
- **Metadata** (small, structured, queryable) → PostgreSQL, via
  SQLAlchemy/Alembic.
- **Blobs** (large request/response bodies) → `StoragePlugin`
  (filesystem in dev, S3-compatible in prod). Never inline large bodies
  into the metadata DB.

### 3.7 Replay Engine
Loads a `Recording`, selects replayable HTTP events, applies the
substitution engine (swaps dynamic values like UUIDs/timestamps/JWTs per
a configurable mapping), rewrites the target host, sends requests,
records the actual responses as a new `Recording` (the "replay run"),
and links each replayed event back to its original.

Safety is enforced here, not left to convention: replay refuses an
obvious production-looking target unless an explicit unsafe flag is
passed.

### 3.8 Comparison Engine
Takes an original `Recording` and a `ReplayRun`, walks matched event
pairs (via the original→replay event link), and produces a
`ComparisonResult` per event: `MATCH / DIFFERENT / MISSING / NEW /
ERROR`. HTTP and Postgres each get their own `ComparatorPlugin`
implementation, following the same swappable-by-name pattern as capture.

---

## 4. Data Flow

### 4.1 Capture (real, v2+)
```
Client → HTTP Capture Proxy → Target App
              │
              ├─ generates correlation_id
              ├─ redacts headers/body
              └─ writes Event (http.request, http.response)
                        │
Target App → Postgres ──┤
              └─ Postgres Capture reads correlation_id from context
                 writes Event (postgres.query, postgres.result)
                        │
                        ▼
                  Recording Store
             (metadata → Postgres, blobs → StoragePlugin)
```

### 4.2 Capture (mock, v1)
```
infractl demo seed
        │
        ▼
MockCapturePlugin.events()
   generates a fixture Event stream (correlation_id shared,
   sequence numbers, realistic timestamps/durations)
        │
        ▼
  Recording Store          ← identical downstream path to §4.1
```

### 4.3 Replay + Compare
```
infractl replay <id> --target <url>
        │
        ▼
Replay Engine
   load Recording → apply substitutions → rewrite target
   → send requests → capture actual responses
        │
        ▼
  new Recording (ReplayRun), linked event-by-event to the original
        │
        ▼
Comparison Engine
   for each linked pair → ComparatorPlugin.compare()
        │
        ▼
  ComparisonResult (MATCH/DIFFERENT/MISSING/NEW/ERROR)
        │
        ▼
   Dashboard / CLI render
```

Note that §4.3 is identical whether the original `Recording` came from
§4.1 (real capture) or §4.2 (mock capture) — this is the whole point of
the schema-first, plugin-first design.

---

## 5. Plugin Architecture (Design Rationale)

**Why plugins at all:** the original spec's non-goals explicitly rule
out building Kafka/Redis/gRPC support in the MVP, but the project is
meant to be usable by "everyone" — different teams have different
stacks. Rather than the core growing a `if protocol == "kafka"` branch
someday, every protocol (including the two the MVP ships with) is a
peer plugin.

**Why entry points specifically:** it's the same mechanism pytest,
Sphinx, and Flask use. It means a plugin is just a normal pip package —
no InfraReplay-specific registration file, no core-repo PR required to
add support for a new protocol.

**The contract:**
```python
class CapturePlugin(ABC):
    name: str
    event_types: list[str]
    async def start(self, config: dict) -> None: ...
    async def stop(self) -> None: ...
    def events(self) -> AsyncIterator[Event]: ...
```
Any object satisfying this — mock, HTTP, Postgres, or a future
community-built Kafka/gRPC/Redis plugin — is accepted identically by
the recording pipeline. `StoragePlugin`, `RedactionPlugin`, and
`ComparatorPlugin` follow the same shape.

**Why this specific slice was chosen as pluggable and not, say, the
API or the replay engine:** capture sources and storage backends are
where teams' infrastructure actually differs (Kafka shop vs. gRPC shop
vs. plain REST). The API surface, event schema, replay engine, and
comparison categories are the stable "protocol" of InfraReplay itself —
those stay in core so every plugin's output is guaranteed to work with
every dashboard/CLI/replay feature without special-casing.

**Escape hatch for performance:** nothing requires a capture plugin to
be pure Python. A future high-throughput capture agent (e.g. written in
Go or Rust) only needs to write `Event`-schema-compatible records into
the same storage layer — it doesn't need to be a Python `CapturePlugin`
object at all. The schema and storage boundary is the real contract;
the Python ABC is just the convenient default for same-process plugins.

---

## 6. Data Model

```
Project 1───* Recording 1───* Event
                  │
                  │ (a ReplayRun is also a Recording,
                  │  with recording_kind = "replay" and
                  │  a source_recording_id pointing back)
                  │
                  └───* ReplayRun 1───* ComparisonResult
```

Key fields on `Event` (see spec §5 for the full Pydantic model):
`schema_version`, `recording_id`, `event_id`, `parent_event_id`,
`sequence`, `timestamp_ns`, `duration_ns`, `event_type`, `service`,
`correlation_id`, `trace_id`/`span_id` (reused from OpenTelemetry
context when present), `payload`.

`ComparisonResult` links one original `event_id` to one replayed
`event_id` (nullable in either direction, to represent `MISSING`/`NEW`)
plus a `category` and a `diff` payload.

---

## 7. Deployment View

```
docker-compose.yml
├── api            (FastAPI, uvicorn)
├── dashboard       (NiceGUI, separate process/port from API)
├── postgres        (metadata store)
├── minio           (blob storage, S3-compatible, dev only)
└── demo-shop       (target application, from v6 onward)
```

Each service is a normal container; nothing here requires Kubernetes.
The plugin registry loads plugins from whatever's installed in the
`api` container's Python environment — adding a plugin package is a
`pip install` + rebuild, not a code change.

---

## 8. Design Principles

1. **Schema is the real API.** Every component (mock capture, real
   capture, replay, comparison, dashboard) only ever agrees on the
   `Event` schema. This is what makes v1's synthetic-data demo and
   v2/v3's real-data version behave identically at the UI layer.
2. **No component imports a concrete plugin.** Everything goes through
   the registry, by name. This is enforced, not just encouraged — code
   review / CI should reject a direct import of e.g.
   `infrareplay.plugins.http_capture` from `infrareplay.api`.
3. **Safe by default.** Replay refuses production-looking targets;
   capture redacts known-sensitive fields by default; secrets never
   round-trip through the API. These are default behaviors, not opt-in
   flags.
4. **Don't build a second observability platform.** Reuse OpenTelemetry
   trace/span IDs when they exist rather than inventing a parallel
   correlation system.
5. **Every version is a real, running system.** No version's "done"
   state includes code that doesn't work yet — see the roadmap doc.
---

## 9. As Built (v1 → v7)

Two things in §3–§5 turned out to need more than the sketch, and both are
worth knowing before reading the code.

### 9.1 Capture crosses a process boundary

HTTP capture runs in the InfraReplay process (a reverse proxy); SQL capture
runs *inside the target application* (SQLAlchemy listeners). They are two
producers writing into one recording, and they do not share memory:

```
  client ──> ReverseProxy ──(recording id, correlation id)──> app
                 │                                             │ SQL
                 │ http.request / http.response        capture plugin queue
                 ▼                                             │
          capture session ────────> recording <──── POST /api/captures/{id}/events
```

The app-side half (`infrareplay/agent/`) flushes its queue **before the
response leaves the app**, so the proxy never records a response whose
database events have not arrived. No polling, no sleeps, no lost events —
and if InfraReplay is down, the flush fails quietly and the app still
serves its request.

Because arrival order is not causal order, `recording/correlate.py` groups
events by `correlation_id`, orders each group by `timestamp_ns`,
re-sequences the whole recording gap-free, and parents the DB events to the
request that caused them. This runs once, when the recording is finalised.

### 9.2 A replay is a recording, opened before it runs

`run_replay` opens the replay recording *first*, then sends requests
carrying that recording's id. An instrumented target therefore streams its
SQL into the replay run while it is still in flight, and the comparison can
diff database work, not just status codes. The same lifecycle
(`start_recording → append_events → finish_recording`) serves capture and
replay, which is why both end up correlated and sequenced identically.

### 9.3 Comparison needs a notion of "same enough"

Real runs differ by construction: new order ids, new payment references, a
new clock. `comparison/normalize.py` rewrites those to `<id>`, `<uuid>`,
`<timestamp>` and drops volatile headers before either comparator diffs.
Everything else is compared exactly — a 201 that became a 409 is still a
difference, and the tests assert both directions.
