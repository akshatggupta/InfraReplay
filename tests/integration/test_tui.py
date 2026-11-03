"""The TUI, driven headlessly by Textual's Pilot against a live stack.

These press real keys and assert on what the widgets hold, so they cover
the wiring — focus, workers, screens — not just the rendering helpers. The
traffic is real: pressing `t` drives real checkouts through a real capture
of the demo shop, which pays through the real payment gateway.
"""

import io

import httpx
import pytest
from rich.console import Console
from textual.widgets import DataTable, Digits, Static

from infrareplay import config
from infrareplay.api import create_app
from infrareplay.tui.app import InfraReplayApp
from infrareplay.tui.client import _CONNECT_TIMEOUT_S, Api
from infrareplay.tui.screens import ComparisonScreen, RecordingScreen, ReplayDialog
from tests.support.live import LiveServer

_SIZE = (150, 46)
_SETTLE_S = 1.5
_WAIT_S = 30.0  # a ceiling, not a delay: each wait ends as soon as its condition holds


@pytest.fixture
async def api_server():
    async with LiveServer(create_app()) as server:
        yield server


@pytest.fixture
def live(stack, monkeypatch):
    """The live stack, with the TUI's defaults pointed at its shop."""

    api, shop = stack
    monkeypatch.setattr(config, "DEMO_SHOP_URL", shop.url)
    monkeypatch.setattr(config, "DEFAULT_TARGET", shop.url)

    return api, shop


def _plain(widget: Static) -> str:
    """What a Static shows, as text — renders Rich groups and tables too."""

    console = Console(width=200, file=io.StringIO(), record=True)
    console.print(widget.content)

    return console.export_text()


async def _until(pilot, condition, what: str) -> None:
    """Wait for the UI to reach a state, rather than for a fixed time."""

    waited = 0.0

    while not condition():
        if waited > _WAIT_S:
            raise AssertionError(f"timed out after {_WAIT_S:.0f}s waiting for {what}")

        await pilot.pause(0.2)
        waited += 0.2


def _tile(app, name: str) -> int:
    return int(app.query_one(f"#tile-{name}", Digits).value)


async def _record(api_url: str) -> dict:
    async with httpx.AsyncClient(base_url=api_url, timeout=30) as c:
        return (await c.post("/api/demo/traffic", json={"checkouts": 2, "declined": 0})).json()


async def test_overview_tiles_follow_new_work(live):
    # Counters are process-wide and other tests have moved them, so this
    # asserts on what changes while the TUI watches, not on absolutes.
    api, _ = live
    app = InfraReplayApp(api=Api(api.url))

    async with app.run_test(size=_SIZE) as pilot:
        await pilot.pause(_SETTLE_S)

        recordings, events = _tile(app, "recordings"), _tile(app, "events")

        recorded = await _record(api.url)
        await _until(pilot, lambda: _tile(app, "recordings") > recordings, "the tiles to update")

        assert "connected" in _plain(app.query_one("#status", Static))
        assert _tile(app, "recordings") - recordings == 1
        assert _tile(app, "events") - events == len(recorded["events"])
        assert _tile(app, "dropped") == 0
        assert app.query_one("#recent-table", DataTable).row_count >= 1


async def test_record_replay_compare_on_real_traffic(live):
    api, _ = live
    app = InfraReplayApp(api=Api(api.url))

    async with app.run_test(size=_SIZE) as pilot:
        # `t`: three real checkouts through a live capture, one card declined.
        await pilot.press("t")

        table = app.query_one("#recordings-table", DataTable)
        await _until(pilot, lambda: table.row_count == 1, "the new recording to be listed")
        await pilot.pause(0.3)  # let the tab's focus settle on the table

        await pilot.press("enter")
        await _until(
            pilot,
            lambda: isinstance(app.screen, RecordingScreen)
            and app.screen.query_one("#events", DataTable).row_count > 0,
            "the recording's timeline",
        )

        timeline = app.screen.query_one("#events", DataTable)
        titles = [str(timeline.get_row_at(i)[2]) for i in range(timeline.row_count)]

        assert any("/v1/charges" in t for t in titles)  # the real payment calls
        assert any("INSERT INTO orders" in t for t in titles)  # the real SQL

        await pilot.press("r")
        await _until(pilot, lambda: isinstance(app.screen, ReplayDialog), "the replay dialog")

        await pilot.press("enter")  # default target: the live shop
        await _until(
            pilot,
            lambda: isinstance(app.screen, ComparisonScreen)
            and app.screen.query_one("#pairs", DataTable).row_count > 0,
            "the comparison",
        )

        # The same shop, the same answers from the gateway's recording.
        assert "matched" in _plain(app.screen.query_one("#verdict", Static))


async def test_refused_replay_is_a_toast_not_a_crash(live):
    api, _ = live
    recorded = await _record(api.url)
    app = InfraReplayApp(api=Api(api.url))

    async with app.run_test(size=_SIZE) as pilot:
        app.push_screen(RecordingScreen(app.api, recorded["recording_id"]))
        await pilot.pause(_SETTLE_S)

        await pilot.press("r")
        await pilot.pause(0.5)

        app.screen.query_one("#target").value = "https://api.production.example.com"
        await pilot.press("enter")
        await pilot.pause(_SETTLE_S)

        assert isinstance(app.screen, RecordingScreen)  # stayed put
        assert any("production" in n.message for n in app._notifications)


async def test_capture_start_and_stop_from_the_tui(api_server):
    app = InfraReplayApp(api=Api(api_server.url))

    async with app.run_test(size=_SIZE) as pilot:
        await pilot.press("3")
        await pilot.pause(0.5)

        app.query_one("#port").value = "0"  # any free port
        app.query_one("#capture-title").value = "from the tui"
        await pilot.click("#start-capture")
        await pilot.pause(_SETTLE_S)

        sessions = app.query_one("#captures-table", DataTable)
        assert sessions.row_count == 1

        await pilot.press("x")
        await pilot.pause(_SETTLE_S)

        assert isinstance(app.screen, RecordingScreen)  # opens what was recorded
        assert sessions.row_count == 0


async def test_benchmarks_tab_shows_the_latest_report(api_server, tmp_path, monkeypatch):
    latest = tmp_path / "latest"
    latest.mkdir()
    (latest / "summary.json").write_text(
        '{"started_at": "2026-11-02T00:00:00+00:00", '
        '"machine": {"cpu": "test cpu", "logical_cores": 4, "ram_gb": 8.0}, '
        '"backend": {"api_db": "sqlite", "shop_db": "sqlite"}, '
        '"levels": [{"level": "light", "users": 5, '
        '"overhead": {"throughput_change_pct": -12.5, "checkout_p50_added_ms": 9.0, '
        '"checkout_p95_added_ms": 20.0, "checkout_p99_added_ms": 30.0}, '
        '"direct": {"stats": {}}, '
        '"captured": {"stats": {}, "capture": {"completeness_pct": 100.0, "events_dropped": 0}}}]}'
    )
    monkeypatch.setattr(config, "BENCHMARK_DIR", tmp_path)

    app = InfraReplayApp(api=Api(api_server.url))

    async with app.run_test(size=_SIZE) as pilot:
        await pilot.press("6")
        await pilot.pause(_SETTLE_S)

        shown = _plain(app.query_one("#benchmarks-view", Static))

        assert "-12.5%" in shown
        assert "+9.0 ms" in shown
        assert "100.0%" in shown


async def test_unreachable_api_is_reported_and_survived():
    app = InfraReplayApp(api=Api("http://127.0.0.1:9"))  # nothing listens on 9

    async with app.run_test(size=_SIZE) as pilot:
        # Past the client's connect timeout: some networks drop rather than refuse.
        await pilot.pause(_CONNECT_TIMEOUT_S + 1.5)

        assert "unreachable" in _plain(app.query_one("#status", Static))
        assert app.is_running


async def test_sends_the_api_key_when_keys_are_on(api_server, monkeypatch):
    monkeypatch.setattr(config, "API_KEYS", {"s3cret": "tui"})

    app = InfraReplayApp(api=Api(api_server.url, api_key="s3cret"))

    async with app.run_test(size=_SIZE) as pilot:
        await pilot.pause(_SETTLE_S)

        status = _plain(app.query_one("#status", Static))

        assert "connected" in status
        assert "api-keys" in status
