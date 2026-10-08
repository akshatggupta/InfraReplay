"""Drill-down screens: a recording's timeline, a replay's comparison, the
replay dialog. Each loads its own data through the shared Api."""

import json
from pathlib import Path

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Checkbox, DataTable, Footer, Header, Input, Label, Static

from infrareplay.tui import render
from infrareplay.tui.client import Api, ApiError

_BAR_CELLS = 26
_MOCK = "mock"

# Cells the other columns take (#, glyph, took, cell padding), so the event
# title can have whatever is left and the bars never scroll off-screen.
_FIXED_CELLS = 22
_MIN_TITLE_CELLS = 24


class ReplayDialog(ModalScreen[dict | None]):
    """Where to replay, how hard to push, and whether production is allowed."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, recording_id: str) -> None:
        super().__init__()
        self._recording_id = recording_id

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(f"Replay [b]{self._recording_id}[/b]", id="dialog-title")
            yield Label("target — mock, or a live URL like http://127.0.0.1:3000", classes="hint")
            yield Input(value=_MOCK, id="target")
            yield Label("concurrency — requests in flight at once; >1 reproduces races", classes="hint")
            yield Input(value="1", id="concurrency", type="integer")
            yield Checkbox("allow production-looking targets", id="unsafe")

            with Horizontal(id="dialog-buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Run replay", id="run", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#target", Input).focus()

    @on(Button.Pressed, "#run")
    @on(Input.Submitted)
    def submit(self) -> None:
        concurrency = self.query_one("#concurrency", Input).value or "1"

        self.dismiss(
            {
                "target": self.query_one("#target", Input).value.strip() or _MOCK,
                "concurrency": max(int(concurrency), 1),
                "unsafe": self.query_one("#unsafe", Checkbox).value,
            }
        )

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)


class RecordingScreen(Screen):
    """A recording's request waterfall, with the selected event inspected."""

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("r", "replay", "Replay"),
        Binding("c", "comparison", "Comparison"),
        Binding("e", "export", "Export"),
    ]

    def __init__(self, api: Api, recording_id: str) -> None:
        super().__init__()
        self._api = api
        self._recording_id = recording_id
        self._recording: dict = {}
        self._events: dict[str, dict] = {}

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(id="summary")

        with Horizontal(classes="split"):
            table = DataTable(id="events", cursor_type="row", zebra_stripes=True)
            table.border_title = "request timeline"
            yield table

            with VerticalScroll(id="inspector") as inspector:
                inspector.border_title = "inspector"
                yield Static(id="detail")

        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#events", DataTable)
        table.add_columns("#", "", "event", "timeline", "took")
        self.load()

    @work(exclusive=True)
    async def load(self) -> None:
        try:
            self._recording = await self._api.recording(self._recording_id)
        except ApiError as exc:
            self.notify(str(exc), severity="error")
            return

        events = self._recording["events"]
        self._events = {e["event_id"]: e for e in events}

        self._show_summary(events)
        self._fill(events)

    def _show_summary(self, events: list[dict]) -> None:
        rec = self._recording
        correlations = len({e["correlation_id"] for e in events})

        line = Text.assemble(
            (rec.get("title") or self._recording_id, "bold"),
            "   ",
            render.kind(rec["kind"]),
            ("  ·  ", render.MUTED),
            render.status(rec["status"]),
            ("  ·  ", render.MUTED),
            (f"{len(events)} events", ""),
            ("  ·  ", render.MUTED),
            (f"{correlations} request{'s' if correlations != 1 else ''}", ""),
        )

        if rec.get("target"):
            line.append(f"  ·  → {rec['target']}", style=render.MUTED)

        self.query_one("#summary", Static).update(line)

    def _fill(self, events: list[dict]) -> None:
        table = self.query_one("#events", DataTable)
        table.clear()

        if not events:
            self.query_one("#detail", Static).update("This recording has no events.")
            return

        t0, span = render.timeline_span(events)
        title_cells = max(_MIN_TITLE_CELLS, table.size.width - _BAR_CELLS - _FIXED_CELLS)

        for event in events:
            indent = "  " * render.depth(event, self._events)

            table.add_row(
                Text(str(event["sequence"]), style=render.MUTED),
                render.event_glyph(event),
                indent + render.short_title(event, title_cells - len(indent)),
                render.waterfall_bar(event, t0, span, _BAR_CELLS),
                Text(render.fmt_duration(event["duration_ns"]), style=render.MUTED, justify="right"),
                key=event["event_id"],
            )

        table.focus()

    @on(DataTable.RowHighlighted, "#events")
    def inspect(self, message: DataTable.RowHighlighted) -> None:
        event = self._events.get(message.row_key.value)

        if event is not None:
            self.query_one("#detail", Static).update(render.event_detail(event))

    # ---------------------------------------------------------------- actions

    def action_replay(self) -> None:
        if self._recording.get("kind") != "capture":
            self.notify("Only captures can be replayed.", severity="warning")
            return

        self.app.push_screen(ReplayDialog(self._recording_id), self._run_replay)

    def _run_replay(self, choice: dict | None) -> None:
        if choice is not None:
            self.replay(choice)

    @work(exclusive=True, group="replay")
    async def replay(self, choice: dict) -> None:
        self.notify(f"Replaying against {choice['target']}…")

        try:
            run = await self._api.replay(
                self._recording_id, choice["target"], choice["concurrency"], choice["unsafe"]
            )
        except ApiError as exc:
            self.notify(str(exc), title="Replay refused", severity="error", timeout=8)
            return

        self.app.push_screen(ComparisonScreen(self._api, run["replay_recording_id"]))

    def action_comparison(self) -> None:
        if self._recording.get("kind") != "replay":
            self.notify("This is a capture — press r to replay it.", severity="warning")
            return

        self.app.push_screen(ComparisonScreen(self._api, self._recording_id))

    @work(exclusive=True, group="export")
    async def action_export(self) -> None:
        try:
            bundle = await self._api.export(self._recording_id)
        except ApiError as exc:
            self.notify(str(exc), severity="error")
            return

        path = Path(f"{self._recording_id}.json")
        path.write_text(json.dumps(bundle, indent=2))

        self.notify(f"Wrote {path.resolve()}", title="Exported")


class ComparisonScreen(Screen):
    """Captured vs replayed: the verdict, then every compared pair."""

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("o", "original", "Original recording"),
    ]

    def __init__(self, api: Api, replay_id: str) -> None:
        super().__init__()
        self._api = api
        self._replay_id = replay_id
        self._rows: dict[str, dict] = {}
        self._original: dict[str, dict] = {}
        self._replayed: dict[str, dict] = {}
        self._source_id = ""
        self._target = ""

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(id="verdict")
        yield Static(id="counts")

        with Horizontal(classes="split"):
            table = DataTable(id="pairs", cursor_type="row", zebra_stripes=True)
            table.border_title = "compared pairs"
            yield table

            with VerticalScroll(id="inspector") as inspector:
                inspector.border_title = "difference"
                yield Static(id="detail")

        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#pairs", DataTable).add_columns("result", "type", "step")
        self.load()

    @work(exclusive=True)
    async def load(self) -> None:
        try:
            results = await self._api.comparison(self._replay_id)
            replay = await self._api.recording(self._replay_id)
            source = await self._api.recording(replay["source_recording_id"])
        except ApiError as exc:
            self.query_one("#verdict", Static).update(Text(str(exc), style="bold #f87171"))
            return

        self._source_id = source["recording_id"]
        self._target = replay.get("target") or "target"
        self._original = {e["event_id"]: e for e in source["events"]}
        self._replayed = {e["event_id"]: e for e in replay["events"]}

        counts: dict[str, int] = {}

        for row in results:
            counts[row["category"]] = counts.get(row["category"], 0) + 1

        self.query_one("#verdict", Static).update(render.verdict(counts, self._target))
        self.query_one("#counts", Static).update(_counts_line(counts))
        self._fill(results)

    def _fill(self, results: list[dict]) -> None:
        table = self.query_one("#pairs", DataTable)
        table.clear()

        # Differences first: they are why anyone opens this screen.
        ordered = sorted(results, key=lambda r: r["category"] == "MATCH")

        for index, row in enumerate(ordered):
            key = str(index)
            self._rows[key] = row
            event = self._original.get(row.get("original_event_id")) or self._replayed.get(
                row.get("replayed_event_id")
            ) or {"event_type": row["event_type"], "payload": {}}

            table.add_row(
                render.category(row["category"]),
                Text(row["event_type"], style=render.MUTED),
                render.short_title(event),
                key=key,
            )

        table.focus()

    @on(DataTable.RowHighlighted, "#pairs")
    def inspect(self, message: DataTable.RowHighlighted) -> None:
        row = self._rows.get(message.row_key.value)

        if row is None:
            return

        self.query_one("#detail", Static).update(
            render.comparison_detail(
                row,
                self._original.get(row.get("original_event_id")),
                self._replayed.get(row.get("replayed_event_id")),
                self._target,
            )
        )

    def action_original(self) -> None:
        if self._source_id:
            self.app.push_screen(RecordingScreen(self._api, self._source_id))


def _counts_line(counts: dict[str, int]) -> Text:
    line = Text()

    for name in ("MATCH", "DIFFERENT", "MISSING", "NEW", "ERROR"):
        if not counts.get(name):
            continue

        line.append_text(render.category(name))
        line.append(f" {counts[name]}   ", style="bold")

    return line
