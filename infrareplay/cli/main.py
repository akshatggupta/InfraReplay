"""infractl command surface. No business logic here — format and print."""

import json
from pathlib import Path

import httpx
import typer

from infrareplay import config
from infrareplay.security import API_KEY_HEADER, generate_key

app = typer.Typer(help="InfraReplay control CLI", no_args_is_help=True)
demo_app = typer.Typer(help="Demo/seed helpers")
recordings_app = typer.Typer(help="Inspect recordings")
plugins_app = typer.Typer(help="Plugin registry")
capture_app = typer.Typer(help="Live capture sessions")
projects_app = typer.Typer(help="Projects")
audit_app = typer.Typer(help="Audit log of capture, replay and bundle actions")
security_app = typer.Typer(help="Keys for encryption at rest")
bench_app = typer.Typer(help="Capture-overhead benchmark (run it with benchmark/run.sh)")

app.add_typer(demo_app, name="demo")
app.add_typer(recordings_app, name="recordings")
app.add_typer(plugins_app, name="plugins")
app.add_typer(capture_app, name="capture")
app.add_typer(projects_app, name="projects")
app.add_typer(audit_app, name="audit")
app.add_typer(security_app, name="security")
app.add_typer(bench_app, name="benchmark")

_TIMEOUT_S = 30
_LOCALHOST = "http://127.0.0.1"


def _client() -> httpx.Client:
    headers = {API_KEY_HEADER: config.API_KEY} if config.API_KEY else {}

    return httpx.Client(base_url=config.API_URL, timeout=_TIMEOUT_S, headers=headers)


def _echo_json(data) -> None:
    typer.echo(json.dumps(data, indent=2, default=str))


def _fail(resp: httpx.Response) -> None:
    detail = resp.json().get("detail", resp.text) if resp.content else resp.reason_phrase
    typer.secho(f"error {resp.status_code}: {detail}", fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


# ------------------------------------------------------------------- demo


@demo_app.command("traffic")
def demo_traffic(
    checkouts: int = typer.Option(3, help="real checkouts to send"),
    declined: int = typer.Option(1, help="how many of them use a declined card"),
    concurrent: bool = typer.Option(False, help="send them all at once"),
    stock: int = typer.Option(None, help="restock every product to this first (default 100)"),
    title: str = typer.Option("Checkout — live traffic", help="recording title"),
    upstream: str = typer.Option(None, help="the shop (default: the API's demo shop)"),
) -> None:
    """Record real checkouts through a live capture of the demo shop."""

    body = {"checkouts": checkouts, "declined": declined, "concurrent": concurrent, "title": title}

    # Unset means "the API's default" (restock first) — not "leave the shop as is".
    if stock is not None:
        body["stock"] = stock

    if upstream:
        body["upstream"] = upstream

    _show_traffic(body)


@demo_app.command("race")
def demo_race() -> None:
    """Record the oversell race: one unit in stock, two shoppers at once."""

    _show_traffic(
        {"checkouts": 2, "declined": 0, "concurrent": True, "stock": 1,
         "title": "Checkout — oversell race"}
    )


def _show_traffic(body: dict) -> None:
    with _client() as c:
        resp = c.post("/api/demo/traffic", json=body)

    if resp.is_error:
        _fail(resp)

    rec = resp.json()
    events = rec["events"]
    statuses = [
        str(e["payload"].get("status"))
        for e in events
        if e["event_type"] == "http.response" and e["payload"].get("direction") != "outbound"
    ]
    outbound = sum(1 for e in events if e["event_type"] == "http.request"
                   and e["payload"].get("direction") == "outbound")

    typer.echo(f"{rec['recording_id']}  [{rec['status']}]  {rec['title']}")
    typer.echo(f"  {len(events)} events · responses {', '.join(statuses)} · {outbound} payment-gateway calls")


@demo_app.command("seed")
def demo_seed() -> None:
    """Create the two SYNTHETIC fixture recordings (tests and offline use)."""

    with _client() as c:
        resp = c.post("/api/demo/seed")

    if resp.is_error:
        _fail(resp)

    for rec in resp.json():
        typer.echo(f"{rec['recording_id']}  {rec['title']}")


# -------------------------------------------------------------- recordings


@recordings_app.command("list")
def recordings_list(
    kind: str = typer.Option(None, help="filter: capture | replay"),
) -> None:
    params = {"kind": kind} if kind else {}

    with _client() as c:
        resp = c.get("/api/recordings", params=params)

    if resp.is_error:
        _fail(resp)

    for rec in resp.json():
        typer.echo(
            f"{rec['recording_id']}  [{rec['kind']}/{rec['status']}]  {rec['title']}"
        )


@recordings_app.command("show")
def recordings_show(recording_id: str) -> None:
    with _client() as c:
        resp = c.get(f"/api/recordings/{recording_id}")

    if resp.is_error:
        _fail(resp)

    _echo_json(resp.json())


@recordings_app.command("export")
def recordings_export(
    recording_id: str,
    out: str = typer.Option(None, "-o", "--out", help="file to write, default stdout"),
) -> None:
    """Write a recording to one portable file."""

    with _client() as c:
        resp = c.get(f"/api/recordings/{recording_id}/export")

    if resp.is_error:
        _fail(resp)

    if out is None:
        _echo_json(resp.json())
        return

    Path(out).write_text(json.dumps(resp.json(), indent=2))
    typer.echo(f"{recording_id} -> {out}")


@recordings_app.command("import")
def recordings_import(path: str) -> None:
    """Load a bundle into this instance."""

    bundle = json.loads(Path(path).read_text())

    with _client() as c:
        resp = c.post("/api/recordings/import", json=bundle)

    if resp.is_error:
        _fail(resp)

    rec = resp.json()
    typer.echo(f"{rec['recording_id']}  [{rec['status']}]  {len(rec['events'])} events")


@recordings_app.command("validate")
def recordings_validate(path: str) -> None:
    """Check a bundle without importing it."""

    bundle = json.loads(Path(path).read_text())

    with _client() as c:
        resp = c.post("/api/recordings/validate", json=bundle)

    if resp.is_error:
        _fail(resp)

    out = resp.json()

    if not out["valid"]:
        typer.secho(f"invalid: {out['error']}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    typer.echo(f"valid  {out['recording_id']}  {out['events']} events  schema v{out['schema_version']}")


# ---------------------------------------------------------------- projects


@projects_app.command("list")
def projects_list() -> None:
    with _client() as c:
        resp = c.get("/api/projects")

    if resp.is_error:
        _fail(resp)

    for row in resp.json():
        typer.echo(f"{row['project']:<20} {row['recordings']:>3} recordings  {row['last_activity']}")


# --------------------------------------------------------------------- tui


@app.command("tui")
def tui() -> None:
    """Open the terminal UI: live metrics, recordings, replays, captures, audit."""

    # Imported here so every other command starts without loading Textual.
    from infrareplay.tui.app import main

    main()


# --------------------------------------------------------------- benchmark


@bench_app.command("show")
def benchmark_show() -> None:
    """The latest benchmark/run.sh report: what capture costs under load."""

    with _client() as c:
        resp = c.get("/api/benchmarks/latest")

    if resp.is_error:
        _fail(resp)

    report = resp.json()
    typer.echo(f"run {report['started_at']} · {report['machine']['cpu']}")
    typer.echo(f"{'load':<8}{'users':>6}{'throughput':>12}{'p50 added':>11}{'p95 added':>11}{'complete':>10}{'dropped':>9}")

    for row in report["levels"]:
        o, cap = row["overhead"], row["captured"]["capture"]
        typer.echo(
            f"{row['level']:<8}{row['users']:>6}{_pct(o['throughput_change_pct']):>12}"
            f"{_ms(o['checkout_p50_added_ms']):>11}{_ms(o['checkout_p95_added_ms']):>11}"
            f"{_pct(cap.get('completeness_pct'), signed=False):>10}{cap.get('events_dropped', '—')!s:>9}"
        )


def _pct(value, signed: bool = True) -> str:
    if value is None:
        return "—"

    return f"{value:+.1f}%" if signed else f"{value:.1f}%"


def _ms(value) -> str:
    return "—" if value is None else f"{value:+.0f} ms"


# ------------------------------------------------------------------- audit


@audit_app.command("list")
def audit_list(
    action: str = typer.Option(None, help="e.g. replay.run, capture.start, auth.denied"),
    recording: str = typer.Option(None, help="only entries for this recording id"),
    limit: int = typer.Option(50, help="newest first"),
) -> None:
    params = {"limit": limit}

    if action:
        params["action"] = action

    if recording:
        params["recording_id"] = recording

    with _client() as c:
        resp = c.get("/api/audit", params=params)

    if resp.is_error:
        _fail(resp)

    for e in resp.json():
        typer.echo(
            f"{e['created_at'][:19]}  {e['outcome']:<6} {e['action']:<17} "
            f"{e['actor']:<12} {e['recording_id'] or '-':<17} {e['target'] or ''}"
        )


# ---------------------------------------------------------------- security


@security_app.command("keygen")
def security_keygen() -> None:
    """Print a fresh key for INFRAREPLAY_ENCRYPTION_KEY. Local; calls no API."""

    typer.echo(generate_key())


# ----------------------------------------------------------------- capture


def _as_url(value: str) -> str:
    """Accept ':3000' as shorthand for a local port, like the spec's examples."""

    if value.startswith(":"):
        return f"{_LOCALHOST}{value}"

    if value.startswith(("http://", "https://")):
        return value

    return f"{_LOCALHOST}:{value}"


def _as_port(value: str) -> int:
    return int(value.lstrip(":"))


@capture_app.command("start")
def capture_start(
    listen: str = typer.Option(..., "--listen", help="port to listen on, e.g. :8080"),
    upstream: str = typer.Option(..., "--upstream", help="app to proxy, e.g. :3000"),
    plugin: str = typer.Option("http_capture", help="capture plugin"),
    title: str = typer.Option("", help="recording title"),
) -> None:
    """Start a live capture. Traffic through --listen is recorded."""

    body = {
        "plugin": plugin,
        "title": title,
        "config": {"listen_port": _as_port(listen), "upstream": _as_url(upstream)},
    }

    with _client() as c:
        resp = c.post("/api/captures", json=body)

    if resp.is_error:
        _fail(resp)

    out = resp.json()
    typer.echo(f"{out['recording_id']}  listening :{out['listen_port']} -> {out['target']}")


@capture_app.command("stop")
def capture_stop(recording_id: str) -> None:
    """Stop a live capture and finalise its recording."""

    with _client() as c:
        resp = c.post(f"/api/captures/{recording_id}/stop")

    if resp.is_error:
        _fail(resp)

    rec = resp.json()
    typer.echo(f"{rec['recording_id']}  [{rec['status']}]  {len(rec['events'])} events")


@capture_app.command("list")
def capture_list() -> None:
    """Show live capture sessions."""

    with _client() as c:
        resp = c.get("/api/captures")

    if resp.is_error:
        _fail(resp)

    for s in resp.json():
        typer.echo(f"{s['recording_id']}  :{s['listen_port']} -> {s['target']}  {s['plugin']}")


# ------------------------------------------------------------------ replay


@app.command("replay")
def replay(
    recording_id: str,
    target: str = typer.Option(None, help="target URL (default: the live demo shop); 'mock' is synthetic"),
    unsafe: bool = typer.Option(False, help="allow production-looking targets"),
    sub: list[str] = typer.Option(
        [], "--sub", help="substitution old=new, repeatable; ${uuid} expands"
    ),
    auto_subs: bool = typer.Option(False, help="give UUID-shaped values a fresh id"),
    concurrency: int = typer.Option(1, help="requests in flight at once"),
    externals: str = typer.Option(
        "recorded", help="the target's outbound calls: recorded (default, safe) or live"
    ),
) -> None:
    """Replay a recording and run the comparison."""

    body = {
        "recording_id": recording_id,
        "externals": externals,
        "unsafe": unsafe,
        "auto_substitute": auto_subs,
        "substitutions": dict(p.split("=", 1) for p in sub) or None,
        "concurrency": concurrency,
    }

    if target:
        body["target"] = target

    with _client() as c:
        resp = c.post("/api/replays", json=body)

    if resp.is_error:
        _fail(resp)

    out = resp.json()
    typer.echo(f"replay {out['replay_recording_id']} against {out['target']}")
    typer.echo(f"summary: {out['summary']}")


@app.command("compare")
def compare(replay_recording_id: str) -> None:
    """Show the comparison for a completed replay run."""

    with _client() as c:
        resp = c.get(f"/api/replays/{replay_recording_id}/comparison")

    if resp.is_error:
        _fail(resp)

    for row in resp.json():
        typer.echo(f"{row['category']:<10} {row['event_type']:<16} {row['diff'] or ''}")


# ----------------------------------------------------------------- plugins


@plugins_app.command("list")
def plugins_list() -> None:
    with _client() as c:
        resp = c.get("/api/plugins")

    if resp.is_error:
        _fail(resp)

    for p in resp.json():
        typer.echo(f"{p['slot']:<12} {p['name']:<20} {p['impl']}")


if __name__ == "__main__":
    app()
