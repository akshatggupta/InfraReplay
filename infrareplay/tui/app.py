"""InfraReplay TUI — `infractl tui`.

    ┌ overview ─ recordings ─ captures ─ audit ─ plugins ────────────┐
    │  live tiles and sparklines from /metrics, recent audit entries │
    │  enter on a recording ─> timeline ─> r replay ─> comparison    │
    └────────────────────────────────────────────────────────────────┘

A thin client over the same REST API the CLI and dashboard use: it polls,
renders and posts, and owns no logic of its own.
"""

from collections import deque

from rich.console import Group
from rich.table import Table
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import (
    Button,
    DataTable,
    Digits,
    Footer,
    Header,
    Input,
    Label,
    Sparkline,
    Static,
    TabbedContent,
    TabPane,
)

from infrareplay.tui import metrics, render
from infrareplay.tui.client import Api, ApiError
from infrareplay.tui.screens import ComparisonScreen, RecordingScreen

_POLL_S = 2.0
_SPARK_POINTS = 60
_MIX_CELLS = 64
_RECENT = 25

_LIVE_TABS = frozenset({"captures", "audit"})

# What gets keyboard focus when a tab opens, so Enter and x work straight away
# instead of landing on the tab bar.
_FOCUS = {
    "recordings": "#recordings-table",
    "captures": "#captures-table",
    "audit": "#audit-table",
}

_DEFAULT_UPSTREAM = "http://127.0.0.1:3000"
_DEFAULT_PORT = "8081"


class InfraReplayApp(App):
    CSS_PATH = "app.tcss"
    TITLE = "InfraReplay"

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("1", "tab('overview')", "Overview", show=False),
        Binding("2", "tab('recordings')", "Recordings", show=False),
        Binding("3", "tab('captures')", "Captures", show=False),
        Binding("4", "tab('audit')", "Audit", show=False),
        Binding("5", "tab('plugins')", "Plugins", show=False),
        Binding("6", "tab('benchmarks')", "Benchmarks", show=False),
        Binding("r", "refresh", "Refresh"),
        Binding("t", "traffic", "Record live traffic"),
        Binding("o", "race", "Record oversell race"),
        Binding("x", "stop_capture", "Stop capture"),
    ]

    def __init__(self, api: Api | None = None) -> None:
        super().__init__()
        self.api = api or Api()

        self._kinds: dict[str, str] = {}
        self._polling = False
        self._events_seen: float | None = None
        self._requests_seen: float | None = None
        self._event_rate: deque[float] = deque([0.0] * _SPARK_POINTS, maxlen=_SPARK_POINTS)
        self._request_rate: deque[float] = deque([0.0] * _SPARK_POINTS, maxlen=_SPARK_POINTS)

    # ----------------------------------------------------------------- layout

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(id="status")

        with TabbedContent(initial="overview"):
            with TabPane("① Overview", id="overview"):
                yield from self._overview()

            with TabPane("② Recordings", id="recordings"):
                yield Label(
                    "enter open  ·  t record live checkout traffic  ·  o record the oversell race  ·  r refresh",
                    classes="hint",
                )
                yield _table("recordings-table", "recordings", "kind", "status", "title", "id", "target", "age")

            with TabPane("③ Captures", id="captures"):
                yield from self._captures()

            with TabPane("④ Audit", id="audit"):
                yield Label("every capture, replay and bundle action — and every refusal", classes="hint")
                yield _table("audit-table", "audit log", "when", "outcome", "action", "actor", "recording", "target")

            with TabPane("⑤ Plugins", id="plugins"):
                with VerticalScroll():
                    yield Static(id="plugins-view")

            with TabPane("⑥ Benchmarks", id="benchmarks"):
                with VerticalScroll():
                    yield Static(id="benchmarks-view")

        yield Footer()

    def _overview(self) -> ComposeResult:
        with Vertical():
            with Horizontal(classes="tiles"):
                for label, _, _ in metrics.TILES:
                    tile = Vertical(Digits("0", id=_tile_id(label)), classes="tile")
                    tile.border_title = label
                    yield tile

            with Horizontal(classes="sparks"):
                events = Vertical(Sparkline(list(self._event_rate), id="events-spark"), classes="spark")
                events.border_title = "events captured / 2s"
                yield events

                requests = Vertical(Sparkline(list(self._request_rate), id="requests-spark"), classes="spark")
                requests.border_title = "API requests / 2s"
                yield requests

            mix = Static(id="mix")
            mix.border_title = "comparison results"
            yield mix

            yield _table("recent-table", "recent activity", "when", "outcome", "action", "actor", "recording")

    def _captures(self) -> ComposeResult:
        yield Label(
            "start a proxy in front of your app; traffic sent through it is recorded",
            classes="hint",
        )

        with Horizontal(id="capture-form"):
            yield Input(value=_DEFAULT_UPSTREAM, placeholder="upstream", id="upstream")
            yield Input(value=_DEFAULT_PORT, placeholder="port", id="port", type="integer")
            yield Input(placeholder="title", id="capture-title")
            yield Button("● Start capture", id="start-capture", variant="success")

        yield _table("captures-table", "recording now  ·  x to stop the selected one", "recording", "send traffic to", "forwards to", "plugin")

    # -------------------------------------------------------------- lifecycle

    def on_mount(self) -> None:
        self.sub_title = "capture → correlate → store → replay → compare"
        self.poll()
        self.refresh_recordings()
        self.refresh_plugins()
        self.set_interval(_POLL_S, self._tick)

    async def on_unmount(self) -> None:
        # Stop in-flight polls first: a worker outliving the client would
        # crash the exit instead of ending quietly.
        self.workers.cancel_all()
        await self.api.close()

    @on(TabbedContent.TabActivated)
    def tab_opened(self, message: TabbedContent.TabActivated) -> None:
        tab = message.pane.id

        if tab in _FOCUS:
            self.query_one(_FOCUS[tab]).focus()

        self._refresh_tab(tab)

    # ---------------------------------------------------------------- polling

    def _tick(self) -> None:
        """Skip a beat rather than cancel a poll still in flight.

        An exclusive worker restarted every tick would cancel itself forever
        against an API slower than the tick — and never report it was down.
        """

        if not self._polling:
            self.poll()

    @work(group="poll")
    async def poll(self) -> None:
        self._polling = True

        try:
            await self._poll_once()
        finally:
            self._polling = False

    async def _poll_once(self) -> None:
        try:
            health = await self.api.health()
            samples = metrics.parse(await self.api.metrics_text())
            recent = await self.api.audit(limit=_RECENT)
        except ApiError as exc:
            self._show_status(None, str(exc))
            return

        self._show_status(health, None)
        self._show_tiles(samples)
        self._show_sparks(samples)
        self._show_mix(samples)
        _fill_audit(self.query_one("#recent-table", DataTable), recent, with_target=False)

        active = self.query_one(TabbedContent).active

        if active in _LIVE_TABS:
            self._refresh_tab(active)

    def _show_status(self, health: dict | None, error: str | None) -> None:
        bar = self.query_one("#status", Static)

        if error:
            bar.update(Text(f" ✖ {error} — retrying every {_POLL_S:.0f}s", style="bold #f87171"))
            return

        auth = health.get("auth", "?")
        line = Text(" ● connected ", style="bold #34d399")
        line.append(f" {self.api.base_url}", style="bold")
        line.append("  ·  auth ", style=render.MUTED)
        line.append(auth, style="#34d399" if auth == "api-keys" else "#fbbf24")
        line.append("  ·  storage ", style=render.MUTED)
        line.append(health.get("storage", "?"))
        bar.update(line)

    def _show_tiles(self, samples) -> None:
        for label, name, where in metrics.TILES:
            value = metrics.total(samples, name, **where)
            self.query_one(f"#{_tile_id(label)}", Digits).update(f"{value:.0f}")

    def _show_sparks(self, samples) -> None:
        events = metrics.total(samples, "infrareplay_events_captured_total")
        requests = metrics.total(samples, "infrareplay_http_request_duration_seconds_count")

        if self._events_seen is not None:
            self._event_rate.append(max(events - self._events_seen, 0))
            self._request_rate.append(max(requests - self._requests_seen, 0))

        self._events_seen, self._requests_seen = events, requests

        self._spark("#events-spark", self._event_rate, "events captured / 2s")
        self._spark("#requests-spark", self._request_rate, "API requests / 2s")

    def _spark(self, selector: str, series: deque[float], title: str) -> None:
        spark = self.query_one(selector, Sparkline)
        spark.data = list(series)

        # The frame's title carries the numbers, so a flat line still says something.
        spark.parent.border_title = f"{title}  ·  now {series[-1]:.0f}  ·  peak {max(series):.0f}"

    def _show_mix(self, samples) -> None:
        counts = metrics.by_label(samples, "infrareplay_comparison_results_total", "category")
        self.query_one("#mix", Static).update(_mix_bar(counts))

    # ------------------------------------------------------------------- tabs

    def _refresh_tab(self, tab: str | None) -> None:
        if tab == "recordings":
            self.refresh_recordings()
        elif tab == "captures":
            self.refresh_captures()
        elif tab == "audit":
            self.refresh_audit()
        elif tab == "plugins":
            self.refresh_plugins()
        elif tab == "benchmarks":
            self.refresh_benchmarks()

    @work(exclusive=True, group="recordings")
    async def refresh_recordings(self) -> None:
        try:
            recordings = await self.api.recordings()
        except ApiError as exc:
            self.notify(str(exc), severity="error")
            return

        self._kinds = {r["recording_id"]: r["kind"] for r in recordings}

        _refill(
            self.query_one("#recordings-table", DataTable),
            [
                (
                    r["recording_id"],
                    render.kind(r["kind"]),
                    render.status(r["status"]),
                    r["title"] or "—",
                    Text(r["recording_id"], style=render.MUTED),
                    Text(r.get("target") or "", style=render.MUTED),
                    Text(render.fmt_age(r.get("created_at")), style=render.MUTED),
                )
                for r in recordings
            ],
        )

    @work(exclusive=True, group="captures")
    async def refresh_captures(self) -> None:
        try:
            sessions = await self.api.captures()
        except ApiError as exc:
            self.notify(str(exc), severity="error")
            return

        _refill(
            self.query_one("#captures-table", DataTable),
            [
                (
                    s["recording_id"],
                    Text(f"● {s['recording_id']}", style="bold #f87171"),
                    Text(f"localhost:{s['listen_port']}", style="bold"),
                    s["target"],
                    Text(s["plugin"], style=render.MUTED),
                )
                for s in sessions
            ],
        )

    @work(exclusive=True, group="audit")
    async def refresh_audit(self) -> None:
        try:
            entries = await self.api.audit()
        except ApiError as exc:
            self.notify(str(exc), severity="error")
            return

        _fill_audit(self.query_one("#audit-table", DataTable), entries, with_target=True)

    @work(exclusive=True, group="benchmarks")
    async def refresh_benchmarks(self) -> None:
        view = self.query_one("#benchmarks-view", Static)

        try:
            report = await self.api.benchmark()
        except ApiError as exc:
            view.update(Text(f"{exc}\n\nRun benchmark/run.sh to produce one.", style="#fbbf24"))
            return

        view.update(_benchmark_view(report))

    @work(exclusive=True, group="plugins")
    async def refresh_plugins(self) -> None:
        try:
            plugins = await self.api.plugins()
        except ApiError as exc:
            self.query_one("#plugins-view", Static).update(Text(str(exc), style="#f87171"))
            return

        self.query_one("#plugins-view", Static).update(_plugins_table(plugins))

    # ---------------------------------------------------------------- actions

    def action_tab(self, tab: str) -> None:
        self.query_one(TabbedContent).active = tab

    def action_refresh(self) -> None:
        self._refresh_tab(self.query_one(TabbedContent).active)
        self._tick()

    def action_traffic(self) -> None:
        self.record(
            {"checkouts": 3, "declined": 1, "title": "Checkout — live traffic"},
            "Three real checkouts, one declined card",
        )

    def action_race(self) -> None:
        self.record(
            {"checkouts": 2, "declined": 0, "concurrent": True, "stock": 1,
             "title": "Checkout — oversell race"},
            "Two shoppers, one unit, at the same moment",
        )

    @work(exclusive=True, group="traffic")
    async def record(self, options: dict, what: str) -> None:
        """Real traffic through a live capture of the shop — nothing synthetic."""

        self.notify(f"{what}…", title="Recording")

        try:
            recording = await self.api.traffic(**options)
        except ApiError as exc:
            self.notify(str(exc), title="Could not record", severity="error", timeout=8)
            return

        statuses = [
            str(e["payload"].get("status"))
            for e in recording["events"]
            if e["event_type"] == "http.response" and e["payload"].get("direction") != render.OUTBOUND
        ]
        self.notify(
            f"{len(recording['events'])} events · responses {', '.join(statuses)}",
            title=f"Recorded {recording['recording_id']}",
            timeout=8,
        )
        self.action_tab("recordings")
        self.refresh_recordings()

    @on(DataTable.RowSelected, "#recordings-table")
    def open_recording(self, message: DataTable.RowSelected) -> None:
        recording_id = message.row_key.value

        if self._kinds.get(recording_id) == "replay":
            self.push_screen(ComparisonScreen(self.api, recording_id))
            return

        self.push_screen(RecordingScreen(self.api, recording_id))

    @on(Button.Pressed, "#start-capture")
    @work(exclusive=True, group="capture")
    async def start_capture(self) -> None:
        port = self.query_one("#port", Input).value or "0"

        try:
            started = await self.api.start_capture(
                self.query_one("#upstream", Input).value.strip(),
                int(port),
                self.query_one("#capture-title", Input).value.strip(),
            )
        except ApiError as exc:
            self.notify(str(exc), title="Capture not started", severity="error", timeout=8)
            return

        self.notify(
            f"Send traffic to localhost:{started['listen_port']} — it forwards to {started['target']}.",
            title=f"Recording {started['recording_id']}",
            timeout=8,
        )
        self.refresh_captures()

    @work(exclusive=True, group="capture")
    async def action_stop_capture(self) -> None:
        table = self.query_one("#captures-table", DataTable)

        if self.query_one(TabbedContent).active != "captures" or not table.row_count:
            self.notify("Open Captures and select a running session first.", severity="warning")
            return

        recording_id = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value

        try:
            stopped = await self.api.stop_capture(recording_id)
        except ApiError as exc:
            self.notify(str(exc), severity="error")
            return

        self.notify(f"{len(stopped['events'])} events recorded.", title=f"Stopped {recording_id}")
        self.refresh_captures()
        self.push_screen(RecordingScreen(self.api, recording_id))


# --------------------------------------------------------------------- helpers


def _tile_id(label: str) -> str:
    return "tile-" + label.replace(" ", "-")


def _table(table_id: str, title: str, *columns: str) -> DataTable:
    table = DataTable(id=table_id, cursor_type="row", zebra_stripes=True)
    table.border_title = title
    table.add_columns(*columns)

    return table


def _refill(table: DataTable, rows: list[tuple]) -> None:
    """Replace the rows, keeping the cursor where it was."""

    cursor = table.cursor_row
    table.clear()

    for key, *cells in rows:
        table.add_row(*cells, key=key)

    if rows:
        table.move_cursor(row=min(cursor, len(rows) - 1))


def _fill_audit(table: DataTable, entries: list[dict], *, with_target: bool) -> None:
    rows = []

    for e in entries:
        cells = [
            Text(render.fmt_age(e["created_at"]), style=render.MUTED),
            render.outcome(e["outcome"]),
            e["action"],
            e["actor"],
            Text(e.get("recording_id") or "—", style=render.MUTED),
        ]

        if with_target:
            cells.append(Text(e.get("target") or "", style=render.MUTED))

        rows.append((str(e["id"]), *cells))

    _refill(table, rows)


def _mix_bar(counts: dict[str, float]) -> Text:
    """Every comparison so far, as one bar split by category."""

    total = sum(counts.values())

    if not total:
        return Text("no comparisons yet — replay a recording to see how runs differ", style=render.MUTED)

    bar = Text()
    legend = Text()

    for name in ("MATCH", "DIFFERENT", "MISSING", "NEW", "ERROR"):
        n = counts.get(name, 0)

        if not n:
            continue

        color = render.CATEGORY_STYLE[name]
        bar.append("█" * max(1, round(n / total * _MIX_CELLS)), style=color)
        legend.append(f"■ {name.lower()} {n:.0f}    ", style=color)

    return Text("\n").join([bar, legend])


def _benchmark_view(report: dict) -> Group:
    """What capture costs under load, from the latest benchmark/run.sh."""

    m = report["machine"]
    cost = Table(
        title="what capture costs, per load level",
        title_style="bold",
        expand=True,
        border_style=render.MUTED,
    )

    for column in ("load", "users", "throughput", "checkout p50", "p95", "complete", "dropped"):
        cost.add_column(column, justify="left" if column == "load" else "right")

    raw = Table(title="raw results", title_style="bold", expand=True, border_style=render.MUTED)

    for column in ("load", "mode", "requests", "failures", "req/s", "checkout p50", "p95", "p99"):
        raw.add_column(column, justify="left" if column in ("load", "mode") else "right")

    for row in report["levels"]:
        o, cap = row["overhead"], row["captured"]["capture"]
        cost.add_row(
            row["level"],
            str(row["users"]),
            _signed(o["throughput_change_pct"], "%"),
            _signed(o["checkout_p50_added_ms"], " ms"),
            _signed(o["checkout_p95_added_ms"], " ms"),
            Text(f"{cap.get('completeness_pct') or 0:.1f}%", style="#34d399"),
            Text(str(cap.get("events_dropped", "—")), style="#34d399" if not cap.get("events_dropped") else "#f87171"),
        )

        for mode in ("direct", "captured"):
            agg = row[mode]["stats"].get("Aggregated", {})
            chk = row[mode]["stats"].get("POST /api/v1/checkout", {})
            raw.add_row(
                row["level"], mode, f"{agg.get('requests', 0):,}", str(agg.get("failures", 0)),
                str(agg.get("rps", 0)), _ms(chk.get("p50_ms")), _ms(chk.get("p95_ms")), _ms(chk.get("p99_ms")),
            )

    header = Text.assemble(
        ("Latest run ", render.MUTED), (report["started_at"], "bold"),
        ("  ·  ", render.MUTED), (m["cpu"], ""),
        ("  ·  ", render.MUTED), (f"{m['logical_cores']} cores, {m['ram_gb']} GB", ""),
        ("  ·  databases: ", render.MUTED),
        (f"API {report['backend']['api_db']}, shop {report['backend']['shop_db']}", ""),
    )

    return Group(header, Text(), cost, Text(), raw)


def _signed(value, unit: str) -> Text:
    if value is None:
        return Text("—", style=render.MUTED)

    return Text(f"{value:+,.1f}{unit}")


def _ms(value) -> str:
    return "—" if value is None else f"{value:,.0f} ms"


def _plugins_table(plugins: list[dict]) -> Table:
    table = Table(title="registered plugins", title_style="bold", expand=True, border_style=render.MUTED)
    table.add_column("slot", style=render.CAPTURE)
    table.add_column("name", style="bold")
    table.add_column("implementation", style=render.MUTED)

    for p in plugins:
        table.add_row(p["slot"], p["name"], p["impl"])

    return table


def main() -> None:
    InfraReplayApp().run()


if __name__ == "__main__":
    main()
