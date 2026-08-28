"""infractl command surface. No business logic here — format and print."""

import json

import httpx
import typer

from infrareplay import config

app = typer.Typer(help="InfraReplay control CLI", no_args_is_help=True)
demo_app = typer.Typer(help="Demo/seed helpers")
recordings_app = typer.Typer(help="Inspect recordings")
plugins_app = typer.Typer(help="Plugin registry")

app.add_typer(demo_app, name="demo")
app.add_typer(recordings_app, name="recordings")
app.add_typer(plugins_app, name="plugins")

_TIMEOUT_S = 30


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


# ------------------------------------------------------------------ replay


@app.command("replay")
def replay(
    recording_id: str,
    target: str = typer.Option("mock", help="replay target url or 'mock'"),
    unsafe: bool = typer.Option(False, help="allow production-looking targets"),
) -> None:
    """Replay a recording and run the comparison."""

    body = {"recording_id": recording_id, "target": target, "unsafe": unsafe}

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
