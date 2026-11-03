# 05 · Gotchas already hit and fixed

Read this before debugging. Every item here cost real time once.

## 1. A stale `infrareplay.egg-info/` hides new plugins

Symptom: you add an entry point to `pyproject.toml`, reinstall, and
`infractl plugins list` still does not show it.

Cause: `importlib.metadata` scans `sys.path`, and a leftover
`infrareplay.egg-info/` in the repo root shadows the venv's `dist-info`.
Its `entry_points.txt` is stale.

Fix:

```bash
rm -rf infrareplay.egg-info
pip install -e . --no-deps --no-build-isolation
```

`--no-build-isolation` needs `setuptools` in the venv (it is installed now).
This would also have broken the demo on a fresh clone, not just locally.

## 2. `timestamp_ns` must be 64-bit

Nanosecond wall clocks are ~1.8e18. SQLite does not care; Postgres `INTEGER`
is 4 bytes and overflows, so every insert under Compose fails. Columns are
`BigInteger` with migration `0002_bigint_clock`. Verified for real:
`timestamp_ns = 1790139326678423871` stored under Compose.

## 3. Do not publish Postgres 5432 in Compose

`docker compose up` failed with
`Bind for 0.0.0.0:5432 failed: port is already allocated` — this machine runs
another Postgres container (`rss-db`). Nothing outside the Compose network
needs that port, so the mapping was removed. Add it back only when you want
psql from the host, and expect the collision.

## 4. Under Compose, `:3000` is the wrong upstream

`--upstream :3000` expands to `127.0.0.1:3000`, which inside the `api`
container is the API itself. Use the service name: `--upstream http://shop:3000`
and `--target http://shop:3000`. In the single all-in-one container, `:3000`
is correct, because everything shares one network namespace.

## 5. Redaction breaks replay — that is the point

`redaction_default` masks `authorization`, so a replayed request carries
`***REDACTED***` and a real auth check would reject it. The demo shop
therefore authenticates on `x-demo-user` (documented as a stand-in for a
session cookie) so the demo flows, while the recorded `authorization` header
still shows masked in the timeline. In real use the answer is substitution:
`--sub '***REDACTED***=Bearer <fresh-token>'`.

## 6. A race only reproduces under concurrency

Replayed one request at a time, the second checkout just finds the shelf
empty — the oversell does not come back. `--concurrency N` is what makes a
recorded race reproducible, and `docs/v6.md` says so explicitly. Do not
"fix" this; it is honest behaviour, and the note belongs in the demo.

## 7. What SQL capture cannot see

- `BEGIN` / `COMMIT` are issued on the connection, not through a cursor, so
  the SQLAlchemy listener never sees them. Statements inside the transaction
  are captured.
- Result **rows** are not read back — that would consume the cursor. Results
  record `rows_affected` and `returns_rows`. `rows_affected` is `None` for
  SELECTs (driver rowcount is -1).
- Only `http.response` and `postgres.result` have comparators, so SQL text is
  never diffed.

All three are stated in `docs/v3.md` and on the status page.

## 8. NiceGUI is slow to boot, and tests must allow for it

The dashboard takes ~15–20s to come up on this machine. Consequences:

- `tests/integration/test_dashboard_pages.py` uses `retries=50` on every
  `should_see`; with the default 3 retries (0.3s) the page is still painting
  and assertions fail against an empty column.
- The Docker `HEALTHCHECK` uses `--start-period=45s`.
- Early manual checks that waited 8–12s produced empty logs and `000`
  responses — the service was fine, the wait was not.

## 9. NiceGUI test plugin: use the user-only plugin

`-p nicegui.testing.plugin` imports selenium and fails. Use
`-p nicegui.testing.user_plugin` (already in `pyproject.toml` `addopts`) and
mark the test `@pytest.mark.nicegui_main_file(dash.__file__)`. That fixture
reloads the main module, so monkeypatching `dash._API` in the test does not
affect the module serving pages — which is why that test runs against a real
stack instead of a patched one.

## 10. The healthcheck has to know its role

One image serves four roles. An API-only healthcheck marks the `shop` and
`dashboard` containers unhealthy forever, which breaks
`depends_on: service_healthy`. `docker/healthcheck.py` reads
`INFRAREPLAY_ROLE` and checks the right port(s).

## 11. `pkill -f` can kill the shell running it

`pkill -f "examples.demo_shop.app"` matched the invoking bash command line
(which contains that string) and killed the session's own shell — exit 144,
twice. The `[e]xamples` bracket trick does not help when the *pattern itself*
appears unbracketed elsewhere in the same command. Prefer killing by captured
PID, or put the pkill in its own invocation.

## 12. A refused replay must not leave a row

`ensure_safe` runs in `run_replay` **before** `start_recording`, so refusing
a production target creates nothing. Before that fix, the dashboard listed a
failed replay whose comparison page said "No comparison found". There is a
test: `test_refused_replay_leaves_no_recording_behind`.

## 13. The agent's queue is shared across requests

Concurrent requests share one capture-plugin queue, so a flush can pick up
another request's events. `CaptureAgent.flush()` therefore groups by
`event.recording_id` and posts each group to its own recording. Do not
revert to flushing "the current request's" events by the URL's id —
`ingest_events` stamps the path's recording id onto whatever it receives.

## 14. No browser in this session

There are no `mcp__Claude_Browser__*`, `mcp__claude-in-chrome__*` or
`mcp__computer-use__*` tools, and no `enable__mcp__*` toggles. `WebFetch`
exists but refuses `localhost`. Visual checks were done with NiceGUI's
headless fixture and HTTP status codes. A `browseros-neo` MCP server is now
configured but **timed out connecting** (`CONNECT_TIMEOUT`, 30s) as of
2026-10-08 — so it is a connection problem to fix, not a missing capability.

## 15. Unrelated file in the repo root

`their_catalogue.json` (a travel-listings JSON) appeared in the repo root on
2026-09-23. It is not part of InfraReplay, is untracked, and was left alone.
Do not commit it.

## 16. The Prometheus registry is process-wide

Counters accumulate across every test in a run. A test asserting an absolute
(`recordings == 2`) passes alone and fails in the suite (it saw 32). Assert
**deltas** — before/after the work — as `test_observability.py` and
`test_tui.py` do.

## 17. A labelled metric does not exist until it is incremented

`replay_failures_total{reason="refused"}` is absent from `/metrics` until the
first refusal, so dashboards show *no data* instead of 0.
`observability/metrics.py` creates every known label combination at zero on
import. Add new label values there too.

## 18. Textual: exclusive workers on a timer cancel themselves

`@work(exclusive=True)` restarted every 2s cancels the previous run. Against an
API slower than the tick, the poll never completes and the TUI never reports
the outage. The app skips a tick while a poll is in flight (`_tick`).

## 19. Textual: focus after switching tabs, and shutdown order

- After switching tabs, focus stays on the tab bar, so `enter` does nothing.
  `_FOCUS` maps each tab to the widget to focus.
- Workers can outlive the HTTP client on quit → crash. `on_unmount` cancels
  workers before closing the client, and a closed `Api` raises `ApiError`.

## 20. WSL drops connections to dead localhost ports

A connect to a port nothing listens on hangs (~3.8s here) instead of being
refused. The TUI client uses a 3s connect timeout so "API down" shows within
seconds, not after the 30s request timeout.

## 21. Seeing the TUI without a terminal

There is no browser or screen here, but Textual can export SVG
(`app.save_screenshot()` inside `run_test`) and `cairosvg` (installed in the
venv) rasterises it to PNG, which the Read tool can show. Box-drawing glyphs
(the big `Digits`) and `█` bars render slightly off in cairosvg's font — a
renderer artefact, not a TUI bug.

## 22. Docker Desktop's WSL integration keeps turning off

It was on on 2026-09-23 and off again on 2026-10-08 (`docker` not found).
Check `docker version` before planning anything that needs it.

---

## Found on 2026-11-02 (v10, v11, the move to /mnt/e)

## 23. Moving the repo breaks the venv in two places

After the move from `/mnt/d` to `/mnt/e`, `import infrareplay` still worked
from the repo root (cwd is on `sys.path`) but **`infractl`, `uvicorn` and
`pytest` were dead**: their shebangs pointed at `/mnt/d/.../python3`, and the
editable install's finder mapped the package to `/mnt/d`. Fix:

```bash
grep -l '/mnt/d/InfraReplay' .venv/bin/* | xargs sed -i 's#/mnt/d/InfraReplay#/mnt/e/InfraReplay#g'
.venv/bin/python -m pip install -e . --no-deps --no-build-isolation
```

Stale `__pycache__` also kept old paths in tracebacks; delete them.

## 24. Building an httpx client costs ~183 ms here

`httpx.AsyncClient()` loads a TLS certificate bundle; on this slow disk that
is 183 ms median, up to 1 s. The capture proxy and the agent used to build
one **per request** — that was most of capture's overhead (v10 found it).
Never create an httpx client inside a request path; keep one per component.
The proxy uses a bare `AsyncHTTPTransport`, not a client, so no cookie jar
is shared between users.

## 25. Locust's `--run-time` fires early on a slow filesystem

The timer runs on gevent's loop clock, which is stale after Locust's slow
imports; a "10 s" run measured 4 s. `benchmark/drive.py` drives Locust as a
library and times the window itself. Don't go back to the CLI flag.

## 26. Never run tests while a benchmark is measuring

They compete for CPU. It happened once: the heavy level of the `pooled` run
is contaminated and excluded from the docs. Run benchmarks alone.

## 27. Don't chain a restore after a command that can fail

`sed … report.md && cp <restore>` — the sed failed (file missing), `&&`
skipped the restore, and a "fixed" benchmark ran on unfixed code. That run
became `results/robustness-only/` (honestly relabelled). Verify file state
(`grep -c` for the change) before measuring.

## 28. SQLite needs WAL and a real busy timeout under load

Default lock wait is 5 s; under heavy capture the API hit `database is
locked` and dropped events. `db/engine.py` now sets WAL and 30 s for SQLite.
The capture writer batches and never dies; drops are counted in
`infrareplay_events_dropped_total`.

## 29. Completeness by totals reads over 100%

Requests in flight when load stops are captured but not counted by Locust.
Completeness is judged per request (each captured request has all its
events), not by comparing totals.

## 30. The demo shop seeds 1 unit per product

Ordinary traffic must restock first or the second checkout gets 409.
`/api/demo/traffic` restocks to 100 by default; the race passes `stock: 1`.
Before replaying the race on stage: `examples/demo_shop/control.sh restock 1`.

## 31. Outbound calls are `http.request`/`http.response` with `direction: "outbound"`

No schema change was needed for v11, but anything that iterates HTTP events
must decide whether it wants inbound only: the replay engine skips outbound
requests (the app makes them again), and tests counting "the client's
statuses" must filter them out (`test_live_replay._statuses`).

## 32. TUI tests: wait for conditions, not for time

A fixed `pause(6)` passed alone and failed in the full suite on this slow
disk. `test_tui._until()` waits for a row / screen to appear, with a 30 s
ceiling.
