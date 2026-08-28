"""Three views: recordings list -> timeline detail -> replay/comparison."""

import os

import httpx
from nicegui import ui

_API = os.environ.get("INFRAREPLAY_API_URL", "http://127.0.0.1:8000")
_TIMEOUT_S = 30

_CATEGORY_COLOR = {
    "MATCH": "positive",
    "DIFFERENT": "warning",
    "MISSING": "negative",
    "NEW": "info",
    "ERROR": "negative",
}


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


def _header(title: str) -> None:
    with ui.header().classes("items-center"):
        ui.link("InfraReplay", "/").classes("text-white text-lg no-underline")
        ui.label("/").classes("text-white")
        ui.label(title).classes("text-white")


# ------------------------------------------------------------ recordings list


@ui.page("/")
async def index() -> None:
    _header("recordings")

    async def seed() -> None:
        await _post("/api/demo/seed")
        ui.navigate.reload()

    ui.button("Seed demo data", on_click=seed).props("outline")

    rows = await _get("/api/recordings")

    if not rows:
        ui.label("No recordings yet — click “Seed demo data”.").classes("text-gray-500")
        return

    with ui.column().classes("w-full gap-2"):
        for rec in rows:
            with ui.card().classes("w-full"):
                with ui.row().classes("items-center justify-between w-full"):
                    ui.link(
                        f"{rec['title'] or rec['recording_id']}",
                        f"/recording/{rec['recording_id']}",
                    ).classes("text-base")
                    ui.badge(f"{rec['kind']} · {rec['status']}")


# ---------------------------------------------------------------- timeline


def _nest(events: list[dict]) -> list[dict]:
    """parent_event_id -> nested {id,label,children} for ui.tree."""

    node = {
        e["event_id"]: {
            "id": e["event_id"],
            "label": _event_label(e),
            "children": [],
        }
        for e in events
    }

    roots: list[dict] = []

    for e in events:
        parent = e.get("parent_event_id")

        if parent and parent in node:
            node[parent]["children"].append(node[e["event_id"]])
        else:
            roots.append(node[e["event_id"]])

    return roots


def _event_label(e: dict) -> str:
    p = e["payload"]
    kind = e["event_type"]

    if kind == "http.request":
        return f"{p.get('method')} {p.get('path')}"

    if kind == "http.response":
        return f"→ HTTP {p.get('status')}"

    if kind == "postgres.query":
        return f"SQL  {p.get('sql', '')[:60]}"

    if kind == "postgres.result":
        return f"→ rows_affected={p.get('rows_affected')}"

    return kind


@ui.page("/recording/{recording_id}")
async def recording_detail(recording_id: str) -> None:
    rec = await _get(f"/api/recordings/{recording_id}")
    _header(rec["title"] or recording_id)

    with ui.row().classes("items-center gap-4"):
        ui.badge(f"{rec['kind']} · {rec['status']}")
        ui.label(f"correlation_id: {rec['events'][0]['correlation_id']}" if rec["events"] else "")

    async def replay() -> None:
        out = await _post("/api/replays", {"recording_id": recording_id, "target": "mock"})
        ui.navigate.to(f"/replay/{out['replay_recording_id']}")

    if rec["kind"] == "capture":
        ui.button("Replay against mock", on_click=replay).props("color=primary")

    ui.separator()
    ui.label("Timeline").classes("text-lg")
    ui.tree(_nest(rec["events"]), label_key="label").expand()

    if rec["kind"] == "replay":
        ui.link("View comparison", f"/replay/{recording_id}")


# -------------------------------------------------------------- comparison


@ui.page("/replay/{replay_recording_id}")
async def replay_comparison(replay_recording_id: str) -> None:
    _header(f"comparison · {replay_recording_id}")

    try:
        results = await _get(f"/api/replays/{replay_recording_id}/comparison")
    except httpx.HTTPStatusError:
        ui.label("No comparison found for that replay.").classes("text-gray-500")
        return

    counts: dict[str, int] = {}

    for r in results:
        counts[r["category"]] = counts.get(r["category"], 0) + 1

    with ui.row().classes("gap-2"):
        for cat, n in counts.items():
            ui.badge(f"{cat}: {n}").props(f"color={_CATEGORY_COLOR.get(cat, 'grey')}")

    with ui.column().classes("w-full gap-2"):
        for r in results:
            with ui.card().classes("w-full"):
                with ui.row().classes("items-center gap-3"):
                    ui.badge(r["category"]).props(
                        f"color={_CATEGORY_COLOR.get(r['category'], 'grey')}"
                    )
                    ui.label(r["event_type"]).classes("font-mono")

                if r["diff"]:
                    ui.json_editor({"content": {"json": r["diff"]}}).props(
                        "readonly"
                    ).classes("w-full")


def main() -> None:
    ui.run(
        title="InfraReplay",
        port=int(os.environ.get("INFRAREPLAY_DASHBOARD_PORT", "8080")),
        reload=False,
        show=False,
    )


# NiceGUI needs the page decorators imported and ui.run() at module top level
# when launched via `python -m infrareplay.dashboard.app`.
if __name__ in {"__main__", "__mp_main__"}:
    main()
