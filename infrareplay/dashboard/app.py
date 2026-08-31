"""InfraReplay dashboard.

Three views, all thin clients over the REST API:

    /                          recordings + replays index
    /recording/{id}            request-scoped event waterfall
    /replay/{id}               original vs replayed comparison
"""

import json
import os
from datetime import datetime, timezone

import httpx
from nicegui import ui

_API = os.environ.get("INFRAREPLAY_API_URL", "http://127.0.0.1:8000")
_TIMEOUT_S = 30

_CATEGORY_COLOR = {
    "MATCH": "#34d399",
    "DIFFERENT": "#fbbf24",
    "MISSING": "#f87171",
    "NEW": "#60a5fa",
    "ERROR": "#f87171",
}

_TYPE_STYLE = {
    "http.request": ("swap_horiz", "#818cf8"),
    "http.response": ("reply", "#34d399"),
    "postgres.query": ("storage", "#94a3b8"),
    "postgres.result": ("table_rows", "#2dd4bf"),
}

_CSS = """
<style>
  body { background: #0b0f19; }
  .ir-shell { max-width: 1080px; margin: 0 auto; padding: 0 20px 64px; }
  .ir-card {
    background: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 12px;
  }
  .ir-mono {
    font-family: ui-monospace, "SF Mono", "JetBrains Mono", Menlo, monospace;
  }
  .ir-track {
    position: relative; height: 12px; border-radius: 6px;
    background: rgba(255,255,255,0.05); flex: 1;
  }
  .ir-bar { position: absolute; top: 0; height: 100%; border-radius: 6px; min-width: 3px; }
  .ir-pill {
    font-size: 11px; letter-spacing: .04em; text-transform: uppercase;
    padding: 2px 8px; border-radius: 999px; font-weight: 600;
  }
  .ir-kv { display: grid; grid-template-columns: 160px 1fr; gap: 4px 16px; }
  .ir-kv > div:nth-child(odd) { color: #94a3b8; }
</style>
"""

ui.add_head_html(_CSS, shared=True)


# ------------------------------------------------------------------ transport


async def _get(path: str, **params):
    async with httpx.AsyncClient(base_url=_API, timeout=_TIMEOUT_S) as c:
        resp = await c.get(path, params=params)
        resp.raise_for_status()
        return resp.json()


async def _post(path: str, payload: dict | None = None):
    async with httpx.AsyncClient(base_url=_API, timeout=_TIMEOUT_S) as c:
        resp = await c.post(path, json=payload)
        resp.raise_for_status()
        return resp.json()


# --------------------------------------------------------------------- format


def _fmt_dur(ns: int) -> str:
    us = ns / 1_000

    if us < 1_000:
        return f"{us:.0f} µs"

    ms = us / 1_000

    if ms < 100:
        return f"{ms:.1f} ms"

    return f"{ms:.0f} ms"


def _fmt_age(iso: str | None) -> str:
    if not iso:
        return ""

    try:
        when = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso

    delta = datetime.now(timezone.utc) - when.astimezone(timezone.utc)
    secs = int(delta.total_seconds())

    if secs < 60:
        return "just now"

    if secs < 3_600:
        return f"{secs // 60}m ago"

    if secs < 86_400:
        return f"{secs // 3_600}h ago"

    return f"{secs // 86_400}d ago"


def _event_title(e: dict) -> str:
    p = e["payload"]
    kind = e["event_type"]

    if kind == "http.request":
        return f"{p.get('method')} {p.get('path')}"

    if kind == "http.response":
        return f"HTTP {p.get('status')}"

    if kind == "postgres.query":
        return p.get("sql", "").splitlines()[0].strip()

    if kind == "postgres.result":
        rows = p.get("rows_affected")
        return f"{rows} row{'' if rows == 1 else 's'} affected"

    return kind


# --------------------------------------------------------------------- layout


def _shell_open(title: str):
    with ui.header().classes("bg-transparent border-b border-white/10"):
        with ui.row().classes("ir-shell items-center w-full py-1"):
            ui.link("InfraReplay", "/").classes(
                "text-lg font-semibold no-underline text-white"
            )
            ui.label("capture → correlate → store → replay → compare").classes(
                "text-xs text-slate-500 hidden sm:block"
            )
            ui.space()
            ui.label(title).classes("ir-mono text-xs text-slate-400")

    return ui.column().classes("ir-shell w-full gap-5 pt-6")


def _api_down(exc: Exception) -> None:
    with ui.element("div").classes("ir-card p-6 w-full"):
        ui.label(f"API unreachable at {_API}").classes("text-red-400 text-lg")
        ui.label(str(exc)).classes("ir-mono text-xs text-slate-500")


def _pill(text: str, color: str) -> None:
    ui.html(
        f'<span class="ir-pill" style="background:{color}22;color:{color};'
        f'border:1px solid {color}55">{text}</span>'
    )


def _kv(pairs: list[tuple[str, str]]) -> None:
    cells = "".join(
        f"<div>{k}</div><div class='ir-mono text-slate-200'>{v}</div>"
        for k, v in pairs
        if v
    )
    ui.html(f"<div class='ir-kv text-sm'>{cells}</div>")


# ---------------------------------------------------------------------- index


@ui.page("/")
async def index() -> None:
    await ui.context.client.connected()

    with _shell_open("recordings"):
        with ui.row().classes("items-center w-full"):
            ui.label("Recordings").classes("text-2xl font-semibold text-white")
            ui.space()

            async def seed() -> None:
                await _post("/api/demo/seed")
                ui.navigate.reload()

            ui.button("Seed demo data", on_click=seed, icon="add").props(
                "unelevated color=teal-6"
            )

        try:
            recs = await _get("/api/recordings")
            plugins = await _get("/api/plugins")
        except httpx.HTTPError as exc:
            _api_down(exc)
            return

        _plugin_strip(plugins)

        if not recs:
            ui.label("No recordings yet — click “Seed demo data”.").classes(
                "text-slate-500"
            )
            return

        captures = [r for r in recs if r["kind"] == "capture"]
        replays = [r for r in recs if r["kind"] == "replay"]

        _section("Captures", captures)

        if replays:
            _section("Replays", replays)


def _plugin_strip(plugins: list[dict]) -> None:
    with ui.row().classes("gap-2 flex-wrap"):
        for p in plugins:
            ui.html(
                f'<span class="ir-pill ir-mono" style="background:rgba(255,255,255,0.05);'
                f'color:#94a3b8">{p["slot"]}·{p["name"]}</span>'
            )


def _section(heading: str, rows: list[dict]) -> None:
    ui.label(heading).classes("text-xs uppercase tracking-wider text-slate-500 mt-2")

    with ui.column().classes("w-full gap-3"):
        for rec in rows:
            _recording_card(rec)


def _recording_card(rec: dict) -> None:
    rid = rec["recording_id"]
    is_replay = rec["kind"] == "replay"
    accent = "#818cf8" if is_replay else "#2dd4bf"

    with ui.element("div").classes("ir-card p-4 w-full"):
        with ui.row().classes("items-center w-full gap-3"):
            ui.icon("videocam" if not is_replay else "restart_alt").style(
                f"color:{accent}"
            )
            ui.link(
                rec["title"] or rid, f"/recording/{rid}"
            ).classes("text-base font-medium no-underline text-white")
            ui.space()
            _pill(rec["status"], _CATEGORY_COLOR["MATCH"] if rec["status"] == "completed" else "#94a3b8")

        with ui.row().classes("items-center gap-4 mt-2 text-xs text-slate-500"):
            ui.label(rid).classes("ir-mono")

            if rec.get("target"):
                ui.label(f"→ {rec['target']}").classes("ir-mono")

            ui.label(_fmt_age(rec.get("created_at")))

            if is_replay:
                ui.link("comparison →", f"/replay/{rid}").classes(
                    "no-underline text-indigo-300"
                )


# ------------------------------------------------------------------- timeline


def _parent_map(events: list[dict]) -> dict[str, dict]:
    return {e["event_id"]: e for e in events}


def _depth(e: dict, by_id: dict[str, dict]) -> int:
    depth = 0
    parent = e.get("parent_event_id")
    seen: set[str] = set()

    while parent and parent in by_id and parent not in seen:
        seen.add(parent)
        depth += 1
        parent = by_id[parent].get("parent_event_id")

    return min(depth, 3)


@ui.page("/recording/{recording_id}")
async def recording_detail(recording_id: str) -> None:
    await ui.context.client.connected()

    with _shell_open(recording_id):
        try:
            rec = await _get(f"/api/recordings/{recording_id}")
        except httpx.HTTPError as exc:
            _api_down(exc)
            return

        events = rec["events"]
        is_capture = rec["kind"] == "capture"

        with ui.row().classes("items-center w-full gap-3"):
            ui.label(rec["title"] or recording_id).classes(
                "text-2xl font-semibold text-white"
            )
            ui.space()

            if is_capture:

                async def replay() -> None:
                    out = await _post(
                        "/api/replays",
                        {"recording_id": recording_id, "target": "mock"},
                    )
                    ui.navigate.to(f"/replay/{out['replay_recording_id']}")

                ui.button("Replay against mock", on_click=replay, icon="restart_alt").props(
                    "unelevated color=indigo-5"
                )

        if not events:
            ui.label("This recording has no events.").classes("text-slate-500")
            return

        head = events[0]

        with ui.element("div").classes("ir-card p-4 w-full"):
            _kv(
                [
                    ("service", head["service"]),
                    ("correlation_id", head["correlation_id"]),
                    ("trace_id", head.get("trace_id") or ""),
                    ("kind", f"{rec['kind']} · {rec['status']}"),
                    ("target", rec.get("target") or ""),
                    ("events", str(len(events))),
                ]
            )

        _waterfall(events)

        if rec["kind"] == "replay":
            ui.link("View comparison →", f"/replay/{recording_id}").classes(
                "no-underline text-indigo-300"
            )


def _waterfall(events: list[dict]) -> None:
    by_id = _parent_map(events)
    t0 = min(e["timestamp_ns"] for e in events)
    span = max(e["timestamp_ns"] + e["duration_ns"] for e in events) - t0 or 1

    ui.label("Request timeline").classes(
        "text-xs uppercase tracking-wider text-slate-500 mt-2"
    )

    with ui.column().classes("w-full gap-1"):
        for e in events:
            _event_row(e, t0, span, _depth(e, by_id))


def _event_row(e: dict, t0: int, span: int, depth: int) -> None:
    icon, color = _TYPE_STYLE.get(e["event_type"], ("circle", "#94a3b8"))
    offset = (e["timestamp_ns"] - t0) / span * 100
    width = max(e["duration_ns"] / span * 100, 0.6)

    with ui.expansion().classes("ir-card w-full").style(
        f"margin-left:{depth * 18}px"
    ) as exp:
        with exp.add_slot("header"):
            with ui.row().classes("items-center w-full gap-3 no-wrap py-1"):
                ui.label(str(e["sequence"])).classes(
                    "ir-mono text-xs text-slate-600 w-5 text-right"
                )
                ui.icon(icon).style(f"color:{color}")
                ui.label(_event_title(e)).classes(
                    "ir-mono text-sm text-slate-200 truncate max-w-xs"
                )
                ui.space()
                ui.html(
                    f'<div class="ir-track"><div class="ir-bar" '
                    f'style="left:{offset:.2f}%;width:{width:.2f}%;background:{color}"></div></div>'
                )
                ui.label(_fmt_dur(e["duration_ns"])).classes(
                    "ir-mono text-xs text-slate-500 w-16 text-right"
                )

        _event_detail(e)


def _event_detail(e: dict) -> None:
    p = e["payload"]
    kind = e["event_type"]

    with ui.column().classes("w-full gap-2 p-3"):
        if kind == "postgres.query":
            ui.code(p.get("sql", ""), language="sql").classes("w-full")

            if p.get("params"):
                ui.label("params").classes("text-xs text-slate-500")
                ui.code(json.dumps(p["params"], indent=2), language="json").classes(
                    "w-full"
                )
            return

        if kind == "http.request":
            _kv([("method", p.get("method", "")), ("path", p.get("path", "")),
                 ("client_ip", p.get("client_ip", ""))])
            ui.label("headers").classes("text-xs text-slate-500")
            _kv(list(p.get("headers", {}).items()))
            ui.label("body").classes("text-xs text-slate-500")
            ui.code(json.dumps(p.get("body", {}), indent=2), language="json").classes(
                "w-full"
            )
            return

        ui.code(json.dumps(p, indent=2), language="json").classes("w-full")


# ----------------------------------------------------------------- comparison


@ui.page("/replay/{replay_recording_id}")
async def replay_comparison(replay_recording_id: str) -> None:
    await ui.context.client.connected()

    with _shell_open(f"replay {replay_recording_id}"):
        try:
            results = await _get(
                f"/api/replays/{replay_recording_id}/comparison"
            )
            replay_rec = await _get(f"/api/recordings/{replay_recording_id}")
            source_rec = await _get(
                f"/api/recordings/{replay_rec['source_recording_id']}"
            )
        except httpx.HTTPStatusError:
            ui.label("No comparison found for that replay.").classes("text-slate-500")
            return
        except httpx.HTTPError as exc:
            _api_down(exc)
            return

        orig_by_id = {e["event_id"]: e for e in source_rec["events"]}
        replay_by_id = {e["event_id"]: e for e in replay_rec["events"]}

        counts: dict[str, int] = {}
        for r in results:
            counts[r["category"]] = counts.get(r["category"], 0) + 1

        _verdict(counts, source_rec, replay_rec)

        with ui.row().classes("gap-3 flex-wrap"):
            for cat, n in counts.items():
                color = _CATEGORY_COLOR.get(cat, "#94a3b8")
                with ui.element("div").classes("ir-card px-4 py-2"):
                    ui.html(
                        f'<div style="color:{color}" class="text-xl font-bold">{n}</div>'
                        f'<div class="text-xs text-slate-500">{cat}</div>'
                    )

        with ui.column().classes("w-full gap-3 mt-2"):
            for r in results:
                _comparison_card(r, orig_by_id, replay_by_id)


def _verdict(counts: dict[str, int], source: dict, replay: dict) -> None:
    diffs = counts.get("DIFFERENT", 0) + counts.get("ERROR", 0)
    color = "#fbbf24" if diffs else "#34d399"
    line = (
        f"{diffs} difference{'' if diffs == 1 else 's'} between the captured run and "
        f"the replay against “{replay.get('target')}”."
        if diffs
        else "Replay matched the captured run exactly."
    )

    with ui.element("div").classes("ir-card p-4 w-full"):
        ui.label(source["title"] or source["recording_id"]).classes(
            "text-2xl font-semibold text-white"
        )
        ui.html(f'<div style="color:{color}" class="text-sm mt-1">{line}</div>')


def _slice(event: dict | None) -> dict:
    if not event:
        return {}

    p = event["payload"]

    if event["event_type"] == "http.response":
        return {"status": p.get("status"), "body": p.get("body")}

    return {"rows_affected": p.get("rows_affected"), "rows": p.get("rows")}


def _comparison_card(
    r: dict, orig_by_id: dict[str, dict], replay_by_id: dict[str, dict]
) -> None:
    color = _CATEGORY_COLOR.get(r["category"], "#94a3b8")
    orig = orig_by_id.get(r.get("original_event_id"))
    repl = replay_by_id.get(r.get("replayed_event_id"))
    label = _event_title(orig or repl or {"event_type": r["event_type"], "payload": {}})
    is_match = r["category"] == "MATCH"

    with ui.expansion(value=not is_match).classes("ir-card w-full") as exp:
        with exp.add_slot("header"):
            with ui.row().classes("items-center w-full gap-3 py-1"):
                _pill(r["category"], color)
                ui.label(r["event_type"]).classes("ir-mono text-xs text-slate-500")
                ui.label(label).classes("ir-mono text-sm text-slate-200 truncate")

        with ui.column().classes("w-full gap-3 p-3"):
            if r.get("diff"):
                _diff_rows(r["diff"])

            with ui.row().classes("w-full gap-3 no-wrap items-stretch"):
                _pane("Captured", _slice(orig), "#94a3b8")
                _pane("Replayed · mock", _slice(repl), color)


def _diff_rows(diff: dict) -> None:
    with ui.column().classes("w-full gap-1"):
        for key, change in diff.items():
            if isinstance(change, dict) and "original" in change:
                ui.html(
                    f'<div class="ir-mono text-xs">'
                    f'<span class="text-slate-400">{key}</span> &nbsp;'
                    f'<span style="color:#f87171">{json.dumps(change["original"])}</span>'
                    f' <span class="text-slate-600">→</span> '
                    f'<span style="color:#34d399">{json.dumps(change["replayed"])}</span>'
                    f"</div>"
                )
            else:
                ui.html(
                    f'<div class="ir-mono text-xs text-slate-400">{key}: '
                    f"{json.dumps(change)}</div>"
                )


def _pane(title: str, body: dict, color: str) -> None:
    with ui.element("div").classes("ir-card p-3 flex-1").style(
        f"border-color:{color}44"
    ):
        ui.label(title).classes("text-xs uppercase tracking-wider text-slate-500")
        ui.code(json.dumps(body, indent=2), language="json").classes("w-full")


def main() -> None:
    ui.run(
        title="InfraReplay",
        dark=True,
        port=int(os.environ.get("INFRAREPLAY_DASHBOARD_PORT", "8080")),
        reload=False,
        show=False,
    )


# NiceGUI needs the page decorators imported and ui.run() at module top level
# when launched via `python -m infrareplay.dashboard.app`.
if __name__ in {"__main__", "__mp_main__"}:
    main()
