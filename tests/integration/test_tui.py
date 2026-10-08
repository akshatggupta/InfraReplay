"""The TUI, driven headlessly by Textual's Pilot against a live API.

These press real keys and assert on what the widgets hold, so they cover
the wiring — focus, workers, screens — not just the rendering helpers.
"""

import io

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
_POLL_WAIT_S = 3.0  # longer than one poll tick


@pytest.fixture
async def api_server():
    async with LiveServer(create_app()) as server:
        yield server


async def _seeded(server) -> dict:
    api = Api(server.url)

    try:
        seeded = await api.seed()
    finally:
        await api.close()

    return next(r for r in seeded if "fails" in r["title"])


def _plain(widget: Static) -> str:
    """What a Static shows, as text — renders Rich groups and tables too."""

    console = Console(width=200, file=io.StringIO(), record=True)
    console.print(widget.content)

    return console.export_text()


def _tile(app, name: str) -> int:
    return int(app.query_one(f"#tile-{name}", Digits).value)


async def test_overview_tiles_follow_new_work(api_server):
    # Counters are process-wide and other tests have moved them, so this
    # asserts on what changes while the TUI watches, not on absolutes.
    app = InfraReplayApp(api=Api(api_server.url))

    async with app.run_test(size=_SIZE) as pilot:
        await pilot.pause(_SETTLE_S)

        recordings, events = _tile(app, "recordings"), _tile(app, "events")

        await _seeded(api_server)
        await pilot.pause(_POLL_WAIT_S)

        assert "connected" in _plain(app.query_one("#status", Static))
        assert _tile(app, "recordings") - recordings == 2
        assert _tile(app, "events") - events == 28
        assert app.query_one("#recent-table", DataTable).row_count >= 1


async def test_recording_to_replay_to_comparison(api_server):
    buggy = await _seeded(api_server)

    app = InfraReplayApp(api=Api(api_server.url))

    async with app.run_test(size=_SIZE) as pilot:
        await pilot.press("2")
        await pilot.pause(_SETTLE_S)

        table = app.query_one("#recordings-table", DataTable)
        keys = [row.value for row in table.rows]
        table.move_cursor(row=keys.index(buggy["recording_id"]))

        # Enter works straight away: switching tabs focuses the table.
        await pilot.press("enter")
        await pilot.pause(_SETTLE_S)

        assert isinstance(app.screen, RecordingScreen)
        assert app.screen.query_one("#events", DataTable).row_count == 14

        await pilot.press("r")
        await pilot.pause(0.5)
        assert isinstance(app.screen, ReplayDialog)

        await pilot.press("enter")  # default target: mock
        await pilot.pause(_SETTLE_S + 1)

        assert isinstance(app.screen, ComparisonScreen)

        pairs = app.screen.query_one("#pairs", DataTable)
        first = pairs.get_row_at(0)

        assert pairs.row_count == 6
        assert "DIFFERENT" in str(first[0])  # differences are listed first
        assert "differences" in _plain(app.screen.query_one("#verdict", Static))

        # The inspector names the exact field that changed.
        assert "rows[0].state" in _plain(app.screen.query_one("#detail", Static))


async def test_refused_replay_is_a_toast_not_a_crash(api_server):
    buggy = await _seeded(api_server)

    app = InfraReplayApp(api=Api(api_server.url))

    async with app.run_test(size=_SIZE) as pilot:
        app.push_screen(RecordingScreen(app.api, buggy["recording_id"]))
        await pilot.pause(_SETTLE_S)

        await pilot.press("r")
        await pilot.pause(0.5)

        target = app.screen.query_one("#target")
        target.value = "https://api.production.example.com"
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
