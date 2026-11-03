# The terminal UI

```bash
infractl tui
```

A full-screen terminal client over the same REST API the CLI and web
dashboard use — it polls, renders and posts, and owns no logic of its own.
Built on [Textual](https://textual.textualize.io/). It honours
`INFRAREPLAY_API_URL` and `INFRAREPLAY_API_KEY` like every other client.

## Screens

| Key | Screen | What it shows |
|---|---|---|
| `1` | **Overview** | Live tiles from `/metrics` — recordings, events, replays, failures, live captures, auth refusals, events dropped (should read 0) — two sparklines (events captured and API requests per 2s, with *now* and *peak* in their titles), every comparison so far as one bar split by category, and the most recent audit entries. Refreshes every 2s. |
| `2` | **Recordings** | Captures and replays, newest first. `enter` opens one. `t` records **real** traffic — three checkouts, one card declined — through a live capture of the shop; `o` records the oversell race (one unit, two shoppers at once). No fixtures. |
| | → **Timeline** | The request waterfall: one row per event, indented under its parent, with a bar showing where it sat in the request and how long it took. The shop's calls to the payment gateway show in amber with ↗; on a replay they read "from the recording". The inspector on the right follows the cursor — SQL with its parameters, request headers and body, or the raw payload. `r` replays, `e` exports a bundle to the current directory, `c` jumps to the comparison of a replay. |
| | → **Replay dialog** | Target (the live shop unless changed), concurrency (above 1 reproduces races), whether production-looking targets are allowed, and whether the target's outbound calls really go out — off by default, so they are answered from the recording and no card is charged twice. A refused replay is a notification, not a crash. |
| | → **Comparison** | The verdict, counts per category, and every compared pair with the differences listed first. The inspector names the exact fields that changed — `rows[0].state  "declined" → "authorized"` — then shows both sides. `o` opens the original recording. |
| `3` | **Captures** | Start a capture proxy (upstream, listen port, title); live sessions update every 2s. `x` stops the selected one and opens what it recorded. |
| `4` | **Audit** | The audit log, live, with outcomes coloured — failed and denied entries stand out. |
| `5` | **Plugins** | Everything the registry loaded, by slot. |
| `6` | **Benchmarks** | The latest `benchmark/run.sh` report: what capture costs per load level — throughput, latency added, completeness, events dropped — and the raw runs. |

`r` refreshes, `q` quits, `ctrl+p` opens Textual's command palette.

## Behaviour worth knowing

- **A down API is reported, not fatal.** The status line turns red with the
  reason and the TUI keeps retrying. The HTTP client's connect timeout is
  3s, because some networks silently drop a connection to a dead port
  instead of refusing it, and a 30s wait would look like a frozen screen.
- **Polling never cancels itself.** A tick is skipped while the previous poll
  is still in flight; an exclusive worker restarted on every tick would
  cancel itself forever against a slow API and never report anything.
- **Switching tabs focuses the tab's table**, so `enter` and `x` act
  immediately instead of landing on the tab bar.
- **Quitting cancels in-flight requests first**, so exiting mid-poll is clean.

## Tests

`tests/integration/test_tui.py` drives the real app headlessly with
Textual's Pilot against a live API: tiles following new work, the full
recording → replay → comparison path, a refused replay, starting and
stopping a capture from the form, the Benchmarks tab, an unreachable API, and API keys. The
end-to-end test presses `t` to record real traffic, opens it, replays it against the live
shop and expects a match. Waits are on conditions (a row appears, a screen opens), not
fixed delays, so a slow machine is not a failing test.
