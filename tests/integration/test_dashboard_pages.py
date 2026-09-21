"""Presentation-day smoke test: every dashboard page renders.

Skipped unless a stack is already running (API on :8000 with at least one
capture and one completed replay), because that is what it checks — the
pages a demo actually clicks through, against real data:

    uvicorn infrareplay.api.app:app --port 8000
    infractl demo seed && infractl replay <id> --target mock
"""

import httpx
import pytest
from nicegui.testing import User

import infrareplay.dashboard.app as dash

_API = "http://127.0.0.1:8000"
_RETRIES = 50  # each page makes two or three API calls before it paints


def _recordings() -> tuple[str, str]:
    try:
        captures = httpx.get(f"{_API}/api/recordings", params={"kind": "capture"}, timeout=2).json()
        replays = httpx.get(f"{_API}/api/recordings", params={"kind": "replay"}, timeout=2).json()
    except httpx.HTTPError:
        pytest.skip("no live stack on :8000")

    completed = [r for r in replays if r["status"] == "completed"]

    if not captures or not completed:
        pytest.skip("live stack has no capture + completed replay to render")

    return captures[0]["recording_id"], completed[0]["recording_id"]


@pytest.mark.nicegui_main_file(dash.__file__)
async def test_every_page_renders(user: User):
    capture_id, replay_id = _recordings()

    await user.open("/")
    await user.should_see("Recordings", retries=_RETRIES)
    await user.should_see("capture·http_capture", retries=_RETRIES)

    await user.open("/captures")
    await user.should_see("Live capture", retries=_RETRIES)
    await user.should_see("Start capture", retries=_RETRIES)

    await user.open(f"/recording/{capture_id}")
    await user.should_see("Request timeline", retries=_RETRIES)
    await user.should_see("Replay", retries=_RETRIES)
    await user.should_see("Export", retries=_RETRIES)

    await user.open(f"/replay/{replay_id}")
    await user.should_see("MATCH", retries=_RETRIES)
    await user.should_see("Captured", retries=_RETRIES)
