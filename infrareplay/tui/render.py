"""Rich renderables for the TUI: titles, waterfall bars, payloads, diffs.

Pure functions over the API's JSON — no I/O, no widgets — so the screens
stay about layout and the look lives in one place.
"""

import json
from datetime import datetime, timezone

from rich.console import Group
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

# One colour per meaning, used everywhere it appears.
CAPTURE = "#2dd4bf"
REPLAY = "#818cf8"
MUTED = "#64748b"

CATEGORY_STYLE = {
    "MATCH": "#34d399",
    "DIFFERENT": "#fbbf24",
    "MISSING": "#f87171",
    "NEW": "#60a5fa",
    "ERROR": "#f87171",
}

OUTCOME_STYLE = {"ok": "#34d399", "failed": "#fbbf24", "denied": "#f87171"}

# event_type -> (glyph, colour)
EVENT_STYLE = {
    "http.request": ("⇄", "#818cf8"),
    "http.response": ("↩", "#34d399"),
    "postgres.query": ("◆", "#94a3b8"),
    "postgres.result": ("▤", "#2dd4bf"),
}

_TRACK = "·"
_BAR = "█"
_SYNTAX_THEME = "monokai"

# Code blocks sit on the app's own ground, not the theme's olive one.
_SYNTAX_BG = "default"

_TITLE_CELLS = 56
_ABSENT = "(absent)"


# -------------------------------------------------------------------- format


def fmt_duration(ns: int) -> str:
    us = ns / 1_000

    if us < 1_000:
        return f"{us:.0f} µs"

    ms = us / 1_000

    return f"{ms:.1f} ms" if ms < 100 else f"{ms:.0f} ms"


def fmt_age(iso: str | None) -> str:
    if not iso:
        return ""

    try:
        when = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso

    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)

    secs = int((datetime.now(timezone.utc) - when).total_seconds())

    if secs < 60:
        return "just now"

    if secs < 3_600:
        return f"{secs // 60}m ago"

    if secs < 86_400:
        return f"{secs // 3_600}h ago"

    return f"{secs // 86_400}d ago"


def category(name: str) -> Text:
    return Text(f" {name} ", style=f"bold black on {CATEGORY_STYLE.get(name, MUTED)}")


def outcome(name: str) -> Text:
    return Text(name, style=f"bold {OUTCOME_STYLE.get(name, MUTED)}")


def kind(name: str) -> Text:
    color = REPLAY if name == "replay" else CAPTURE
    glyph = "↻" if name == "replay" else "●"

    return Text(f"{glyph} {name}", style=color)


def status(name: str) -> Text:
    color = {"completed": "#34d399", "recording": "#fbbf24", "failed": "#f87171"}.get(name, MUTED)

    return Text(name, style=color)


# --------------------------------------------------------------------- events


def short_title(event: dict, cells: int = _TITLE_CELLS) -> str:
    """event_title, cut to fit a table column."""

    title = event_title(event)

    return title if len(title) <= cells else title[: cells - 1] + "…"


def event_title(event: dict) -> str:
    p = event.get("payload", {})
    kind_ = event.get("event_type", "")

    if kind_ == "http.request":
        return f"{p.get('method', '')} {p.get('path', '')}"

    if kind_ == "http.response":
        return f"HTTP {p.get('status', p.get('error', '?'))}"

    if kind_ == "postgres.query":
        sql = (p.get("sql") or "").strip().splitlines()
        return sql[0] if sql else "(empty)"

    if kind_ == "postgres.result":
        rows = p.get("rows_affected")
        return "rows returned" if rows is None else f"{rows} row{'' if rows == 1 else 's'}"

    return kind_


def event_glyph(event: dict) -> Text:
    glyph, color = EVENT_STYLE.get(event.get("event_type", ""), ("•", MUTED))

    return Text(glyph, style=f"bold {color}")


def waterfall_bar(event: dict, t0: int, span: int, width: int) -> Text:
    """Where this event sits in the request, drawn to `width` cells."""

    _, color = EVENT_STYLE.get(event.get("event_type", ""), ("•", MUTED))

    start = int((event["timestamp_ns"] - t0) / span * width)
    length = max(1, round(event["duration_ns"] / span * width))
    start = min(start, width - 1)
    length = min(length, width - start)

    bar = Text(_TRACK * start, style=MUTED)
    bar.append(_BAR * length, style=color)
    bar.append(_TRACK * (width - start - length), style=MUTED)

    return bar


def timeline_span(events: list[dict]) -> tuple[int, int]:
    t0 = min(e["timestamp_ns"] for e in events)
    t1 = max(e["timestamp_ns"] + e["duration_ns"] for e in events)

    return t0, max(t1 - t0, 1)


def depth(event: dict, by_id: dict[str, dict]) -> int:
    level = 0
    parent = event.get("parent_event_id")
    seen: set[str] = set()

    while parent and parent in by_id and parent not in seen:
        seen.add(parent)
        level += 1
        parent = by_id[parent].get("parent_event_id")

    return min(level, 3)


def event_detail(event: dict):
    """The inspector pane for one event."""

    p = event.get("payload", {})
    kind_ = event.get("event_type", "")

    header = Text.assemble(
        event_glyph(event),
        " ",
        (kind_, "bold"),
        ("  ·  ", MUTED),
        (f"seq {event.get('sequence')}", MUTED),
        ("  ·  ", MUTED),
        (fmt_duration(event.get("duration_ns", 0)), MUTED),
    )
    meta = _kv(
        [
            ("event", event.get("event_id", "")),
            ("correlation", event.get("correlation_id", "")),
            ("service", event.get("service", "")),
        ]
    )

    if kind_ == "postgres.query":
        body = [Syntax(p.get("sql", ""), "sql", theme=_SYNTAX_THEME, word_wrap=True, background_color=_SYNTAX_BG)]

        if p.get("params"):
            body += [Text("params", style=f"bold {MUTED}"), _json(p["params"])]

        return Group(header, meta, Text(), *body)

    if kind_ == "http.request":
        return Group(
            header,
            meta,
            Text(),
            Text(f"{p.get('method', '')} {p.get('path', '')}", style="bold"),
            Text("headers", style=f"bold {MUTED}"),
            _kv(sorted((p.get("headers") or {}).items())),
            Text("body", style=f"bold {MUTED}"),
            _json(p.get("body")),
        )

    return Group(header, meta, Text(), _json(p))


# ---------------------------------------------------------------- comparison


def verdict(counts: dict[str, int], target: str) -> Text:
    diffs = counts.get("DIFFERENT", 0) + counts.get("ERROR", 0) + counts.get("MISSING", 0)

    if not diffs:
        return Text(f"✔ Replay against {target} matched the captured run.", style="bold #34d399")

    noun = "difference" if diffs == 1 else "differences"

    return Text(f"▲ {diffs} {noun} from the captured run, replayed against {target}.", style="bold #fbbf24")


def comparison_detail(row: dict, original: dict | None, replayed: dict | None, target: str):
    parts = [
        Text.assemble(category(row["category"]), "  ", (row["event_type"], "bold")),
        Text(),
    ]

    diff = row.get("diff") or {}

    if diff:
        parts.append(Text("what changed", style=f"bold {MUTED}"))
        parts.append(_diff_table(diff))
        parts.append(Text())

    # Stacked, not side by side: each gets the pane's full width.
    parts += [
        Text("captured", style=f"bold {MUTED}"),
        _json(_slice(original)),
        Text(f"replayed · {target}", style=f"bold {MUTED}"),
        _json(_slice(replayed)),
    ]

    return Group(*parts)


def _diff_table(diff: dict) -> Table:
    """One line per field that differs, down to the leaf.

        rows[0].state         "declined"  →  "authorized"
        rows[0].decline_code  "insufficient_funds"  →  (absent)
    """

    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold", no_wrap=True)
    table.add_column(overflow="fold")

    for key, change in diff.items():
        if not (isinstance(change, dict) and "original" in change):
            table.add_row(key, Text(_compact(change)))
            continue

        for path, before, after in leaf_diff(change["original"], change.get("replayed"), key):
            table.add_row(
                path,
                Text.assemble((_shown(before), "#f87171"), ("  →  ", MUTED), (_shown(after), "#34d399")),
            )

    return table


def leaf_diff(before, after, path: str = "") -> list[tuple[str, object, object]]:
    """The paths at which two JSON values differ, and the values there."""

    if isinstance(before, dict) and isinstance(after, dict):
        out = []

        for key in list(before) + [k for k in after if k not in before]:
            out += leaf_diff(before.get(key, _ABSENT), after.get(key, _ABSENT), f"{path}.{key}")

        return out

    if isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
        out = []

        for index, (b, a) in enumerate(zip(before, after)):
            out += leaf_diff(b, a, f"{path}[{index}]")

        return out

    return [] if before == after else [(path, before, after)]


def _shown(value) -> str:
    return value if value == _ABSENT else _compact(value, limit=60)


def _slice(event: dict | None) -> dict:
    if not event:
        return {}

    p = event.get("payload", {})

    if event.get("event_type") == "http.response":
        return {"status": p.get("status"), "body": p.get("body")}

    return {"rows_affected": p.get("rows_affected"), "rows": p.get("rows")}


# ------------------------------------------------------------------- helpers


def _kv(pairs) -> Table:
    table = Table.grid(padding=(0, 2))
    table.add_column(style=MUTED, no_wrap=True)
    table.add_column(overflow="fold")

    for key, value in pairs:
        if value in ("", None):
            continue

        table.add_row(str(key), str(value))

    return table


def _json(value) -> Syntax:
    return Syntax(
        json.dumps(value, indent=2, default=str),
        "json",
        theme=_SYNTAX_THEME,
        word_wrap=True,
        background_color=_SYNTAX_BG,
    )


def _compact(value, limit: int = 120) -> str:
    text = json.dumps(value, default=str)

    return text if len(text) <= limit else text[: limit - 1] + "…"
