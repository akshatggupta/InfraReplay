# 02 · Architecture, and the decisions behind it

`architecture.md` in the repo root has the full design. This file carries the
parts a new session would otherwise have to rediscover — the *why*.

## The one indirection

`infrareplay/plugins/registry.py` loads the `infrareplay.plugins` entry-point
group, validates each object against its ABC (`CapturePlugin`,
`StoragePlugin`, `RedactionPlugin`, `ComparatorPlugin`) and hands them out
**by name only**. No core module imports a concrete plugin. Keep it that way;
it is the project's central claim.

## Recording lifecycle (three steps, not one)

```
start_recording ──> append_events (n times) ──> finish_recording
                                 └──> fail_recording
```

v1 had a single `capture_recording()` that drove a plugin to exhaustion and
wrote the result. Live capture cannot work that way: events arrive over
minutes, and **some of them come from another process**, which needs the
recording row to exist before the first event does. Both capture and replay
now go through these three calls (`infrareplay/recording/service.py`).

`finish_recording` is where correlation happens, once.

## Capture crosses a process boundary

The proxy sees HTTP. Only the application sees the SQL it runs. They are two
producers writing into one recording and they do not share memory:

```
  client ──> ReverseProxy ──(recording id, correlation id)──> app
                 │                                             │ SQL
                 │ http.request / http.response        capture plugin queue
                 ▼                                             │
          capture session ────────> recording <──── POST /api/captures/{id}/events
```

**The ordering trick:** `infrareplay/agent/agent.py`'s `CaptureMiddleware`
is pure ASGI and flushes the SQL queue inside a wrapped `send`, on the final
`http.response.body` message — i.e. *before the response leaves the app*. So
the proxy can never record a response whose database events have not landed
yet. No polling, no sleeping, no lost events. If InfraReplay is down the
flush fails quietly and the app still serves its request.

Two more details that matter:

- The agent drains with `plugin.pending()` (queue size) so the drain never
  blocks, and groups events by `event.recording_id` before posting —
  concurrent requests share one queue, so a flush can carry another
  request's events.
- Queries outside a bound correlation context are ignored entirely
  (`infrareplay/capture/context.py`), so an instrumented app records nothing
  while no capture is running.

## Correlation on write

`infrareplay/recording/correlate.py`: arrival order is not causal order.
Events are grouped by `correlation_id`, each group ordered by `timestamp_ns`,
the whole recording re-sequenced gap-free, and DB events parented to the
request that caused them. Recorded parent links are never overwritten —
correlation only fills gaps.

## A replay is a recording, opened before it runs

`run_replay` (`infrareplay/replay/runner.py`) creates the replay recording
row *first*, then sends requests carrying that recording's id in the
`x-infrareplay-recording-id` header. An instrumented target therefore streams
its SQL into the replay run while the run is still in flight, which is what
lets the comparison diff database work and not just status codes.

Order inside `run_replay`: `ensure_safe` → `start_recording` →
`replay_recording` → `append_events` → `finish_recording` (picks up ingested
events) → `link_streams` → `link_replay_events` → `compare_run` →
`save_comparison`.

`ensure_safe` runs **before** the recording is opened, so a refused
production target leaves no junk row behind. The engine checks again
internally — safety is the engine's invariant, not the caller's good manners.

## Event linking is positional

`infrareplay/replay/linking.py`: event ids are regenerated every run, so two
events correspond when they sit in the same request group, share an
`event_type`, and are the Nth of that type in the group — signature
`(group_index, event_type, occurrence)`. Anything unpaired falls out as
MISSING / NEW.

## Comparison needs "same enough"

`infrareplay/comparison/normalize.py` rewrites volatile values to their shape
before either comparator diffs:

```
"ord_9f2c1a7b3e00"                      -> "<id>"
"3fa85f64-5717-4562-b3fc-2c963f66afa6"  -> "<uuid>"
"2026-09-21T10:00:00.412Z"              -> "<timestamp>"
```

Volatile headers (`date`, `server`, `etag`, `x-request-id`, the capture
headers, …) are dropped; `INFRAREPLAY_IGNORE_FIELDS` adds named keys.
Everything else is compared exactly — a 201 that became a 409 is still a
difference, and the tests assert both directions.

Note: only `http.response` and `postgres.result` have comparators.
`http.request` / `postgres.query` have none by design, so SQL *text* is not
compared — results are.

## Substitution

`infrareplay/replay/substitution.py`, two layers: the caller's explicit
mapping (values may be templates `${uuid}`, `${now_iso}`, `${now_ns}`,
expanded once per run), then `AutoMode.FRESH_IDS`, which gives every
UUID-shaped string a fresh UUID — the same replacement each time that value
reappears, so a body and the header referencing it stay consistent. Default
is `AutoMode.OFF`.

## The demo shop's bug

`examples/demo_shop/store.py`:

- `Reserve.READ_THEN_WRITE` (default) — availability is read, decided on in
  Python, then written back as an absolute value, with
  `DEMO_SHOP_RACE_WINDOW_S` standing in for real work between the two. Two
  interleaved checkouts both believe the last unit is theirs → oversell.
- `Reserve.ATOMIC` (`DEMO_SHOP_LOCK_INVENTORY=1`) — one conditional
  `UPDATE inventory SET available = available - :qty WHERE available >= :qty`;
  the loser's UPDATE matches no row and gets 409.

Auth is the `x-demo-user` header on purpose — see `05-gotchas.md`.

## v11: outbound calls — recorded on capture, answered on replay

`infrareplay/agent/egress.py`. The app builds its outbound HTTP client on
`agent.http_transport()` (a `CapturingTransport`); the demo shop's payment
client does. Three modes, chosen per request from the capture context:

```
no capture context   → plain pass-through, nothing recorded
capture              → real call, recorded as http.request/response with
                        direction: "outbound", in the inbound request's group
replay (default)     → NOT sent; the recorded answer is returned
                        (GET /api/recordings/{id}/externals?correlation_id=…);
                        no recorded answer → 503 external_call_not_recorded
replay + "live"      → really sent (explicit opt-in only)
```

The replay engine tells the target which original request each replayed one
reproduces: `x-infrareplay-replay-of` and `x-infrareplay-source-correlation`
(plus `x-infrareplay-externals`). The agent's middleware binds them into the
context; the transport matches calls by `METHOD host/path`, the Nth identical
call getting the Nth recorded answer. Only inbound requests are replayed.
Correlation hangs an outbound request under the inbound one (its group's
first request).

Proof is external, not internal: the payment gateway keeps a ledger of every
charge (`GET /v1/charges`), and the integration test shows its count does not
move on replay.

## v10: performance lessons now in the code

- One pooled HTTP connection per component (proxy: a bare transport, so no
  cookie jar is shared between users; agent: one client). Never build an httpx
  client in a request path.
- The capture writer batches whatever is queued into one transaction and can
  never die; a batch that fails after retries is counted, not lost silently.
- SQLite in WAL mode with a 30 s busy timeout; event writes retry on a lock.
- The remaining ceiling under load is the single API process (proxy + ingest +
  writes in one event loop). Not addressed; stated in `docs/v10.md`.
