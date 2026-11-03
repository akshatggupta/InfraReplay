"""Async API client for the TUI. Formats nothing, decides nothing."""

import httpx

from infrareplay import config
from infrareplay.security import API_KEY_HEADER

_TIMEOUT_S = 30

# A down API should read as down within seconds, not after the full request
# timeout: some networks silently drop a connect instead of refusing it.
_CONNECT_TIMEOUT_S = 3.0


class ApiError(RuntimeError):
    """A refusal or an outage, with the API's own words when it gave any."""


class Api:
    def __init__(self, base_url: str | None = None, api_key: str | None = None) -> None:
        key = api_key if api_key is not None else config.API_KEY
        headers = {API_KEY_HEADER: key} if key else {}

        self.base_url = (base_url or config.API_URL).rstrip("/")
        self._http = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(_TIMEOUT_S, connect=_CONNECT_TIMEOUT_S),
            headers=headers,
        )
        self._closed = False

    async def close(self) -> None:
        self._closed = True
        await self._http.aclose()

    # --------------------------------------------------------------- transport

    async def _call(self, method: str, path: str, **kwargs):
        if self._closed:
            raise ApiError("client closed")

        try:
            response = await self._http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise ApiError(f"API unreachable at {self.base_url}: {type(exc).__name__}") from exc

        if response.is_error:
            raise ApiError(_detail(response))

        return response

    async def _json(self, method: str, path: str, **kwargs):
        return (await self._call(method, path, **kwargs)).json()

    # ------------------------------------------------------------------ reads

    async def health(self) -> dict:
        return await self._json("GET", "/health")

    async def metrics_text(self) -> str:
        return (await self._call("GET", "/metrics")).text

    async def plugins(self) -> list[dict]:
        return await self._json("GET", "/api/plugins")

    async def recordings(self) -> list[dict]:
        return await self._json("GET", "/api/recordings")

    async def recording(self, recording_id: str) -> dict:
        return await self._json("GET", f"/api/recordings/{recording_id}")

    async def comparison(self, replay_id: str) -> list[dict]:
        return await self._json("GET", f"/api/replays/{replay_id}/comparison")

    async def captures(self) -> list[dict]:
        return await self._json("GET", "/api/captures")

    async def audit(self, limit: int = 200) -> list[dict]:
        return await self._json("GET", "/api/audit", params={"limit": limit})

    async def export(self, recording_id: str) -> dict:
        return await self._json("GET", f"/api/recordings/{recording_id}/export")

    # ----------------------------------------------------------------- writes

    async def traffic(self, **options) -> dict:
        """Record real checkouts through a live capture of the shop."""

        return await self._json("POST", "/api/demo/traffic", json=options)

    async def benchmark(self) -> dict:
        return await self._json("GET", "/api/benchmarks/latest")

    async def replay(
        self, recording_id: str, target: str, concurrency: int, unsafe: bool, externals: str
    ) -> dict:
        body = {
            "recording_id": recording_id,
            "target": target,
            "concurrency": concurrency,
            "unsafe": unsafe,
            "externals": externals,
        }

        return await self._json("POST", "/api/replays", json=body)

    async def start_capture(self, upstream: str, listen_port: int, title: str) -> dict:
        body = {
            "plugin": "http_capture",
            "title": title,
            "config": {"upstream": upstream, "listen_port": listen_port},
        }

        return await self._json("POST", "/api/captures", json=body)

    async def stop_capture(self, recording_id: str) -> dict:
        return await self._json("POST", f"/api/captures/{recording_id}/stop")


def _detail(response: httpx.Response) -> str:
    try:
        detail = response.json().get("detail")
    except ValueError:
        detail = None

    return f"{response.status_code}: {detail or response.reason_phrase}"
