"""infractl command surface. No business logic here — format and print."""

import json
from pathlib import Path

import httpx
import typer

from infrareplay import config

app = typer.Typer(help="InfraReplay control CLI", no_args_is_help=True)
demo_app = typer.Typer(help="Demo/seed helpers")
recordings_app = typer.Typer(help="Inspect recordings")
plugins_app = typer.Typer(help="Plugin registry")
capture_app = typer.Typer(help="Live capture sessions")
projects_app = typer.Typer(help="Projects")

app.add_typer(demo_app, name="demo")
app.add_typer(recordings_app, name="recordings")
app.add_typer(plugins_app, name="plugins")
app.add_typer(capture_app, name="capture")
app.add_typer(projects_app, name="projects")

_TIMEOUT_S = 30
_LOCALHOST = "http://127.0.0.1"


def _client() -> httpx.Client:
    return httpx.Client(base_url=config.API_URL, timeout=_TIMEOUT_S)


def _echo_json(data) -> None:
    typer.echo(json.dumps(data, indent=2, default=str))


def _fail(resp: httpx.Response) -> None:
    detail = resp.json().get("detail", resp.text) if resp.content else resp.reason_phrase
    typer.secho(f"error {resp.status_code}: {detail}", fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


# ------------------------------------------------------------------- demo


@demo_app.command("seed")
def demo_seed() -> None:
    """Create the clean and buggy fixture recordings."""

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
    target: str = typer.Option("mock", help="replay target url or 'mock'"),
    unsafe: bool = typer.Option(False, help="allow production-looking targets"),
    sub: list[str] = typer.Option(
        [], "--sub", help="substitution old=new, repeatable; ${uuid} expands"
    ),
    auto_subs: bool = typer.Option(False, help="give UUID-shaped values a fresh id"),
    concurrency: int = typer.Option(1, help="requests in flight at once"),
) -> None:
    """Replay a recording and run the comparison."""

    body = {
        "recording_id": recording_id,
        "target": target,
        "unsafe": unsafe,
        "auto_substitute": auto_subs,
        "substitutions": dict(p.split("=", 1) for p in sub) or None,
        "concurrency": concurrency,
    }

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
