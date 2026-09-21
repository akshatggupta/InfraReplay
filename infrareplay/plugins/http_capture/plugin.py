"""HttpCapturePlugin — the v1 pipeline's first real data source.

Nothing above the capture layer changed to accept it: the recording service
still only sees `start() / events() / stop()`.
"""

import asyncio
from collections.abc import AsyncIterator

from infrareplay.contracts import CapturePlugin
from infrareplay.plugins.http_capture.proxy import ReverseProxy
from infrareplay.schema import Event, EventType

_DEFAULT_HOST = "0.0.0.0"


class HttpCapturePlugin(CapturePlugin):
    name = "http_capture"
    event_types = [EventType.HTTP_REQUEST.value, EventType.HTTP_RESPONSE.value]

    def __init__(self) -> None:
        self._queue: asyncio.Queue[Event | None] = asyncio.Queue()
        self._proxy: ReverseProxy | None = None
        self.port = 0

    async def start(self, config: dict) -> None:
        """config: {recording_id, upstream, listen_port, listen_host?}."""

        self._proxy = ReverseProxy(
            recording_id=config["recording_id"],
            upstream=config["upstream"],
            emit=self._queue.put,
        )

        self.port = await self._proxy.serve(
            config.get("listen_host", _DEFAULT_HOST),
            int(config.get("listen_port", 0)),
        )

    async def stop(self) -> None:
        if self._proxy is not None:
            await self._proxy.shutdown()
            self._proxy = None

        await self._queue.put(None)  # unblocks events()

    async def events(self) -> AsyncIterator[Event]:
        """Yield until stop() drops the sentinel in."""

        while True:
            event = await self._queue.get()

            if event is None:
                return

            yield event
