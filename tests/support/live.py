"""Run an ASGI app on a real port, inside the test's own event loop."""

import asyncio

import uvicorn

_POLL_S = 0.01
_TIMEOUT_S = 10
_HOST = "127.0.0.1"


class LiveServer:
    def __init__(self, app) -> None:
        self._app = app
        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task | None = None
        self.port = 0

    @property
    def url(self) -> str:
        return f"http://{_HOST}:{self.port}"

    async def __aenter__(self) -> "LiveServer":
        config = uvicorn.Config(self._app, host=_HOST, port=0, log_level="warning")
        self._server = uvicorn.Server(config)
        self._task = asyncio.create_task(self._server.serve())

        waited = 0.0

        while not self._server.started:
            if waited > _TIMEOUT_S:
                raise RuntimeError("server did not start")

            await asyncio.sleep(_POLL_S)
            waited += _POLL_S

        self.port = self._server.servers[0].sockets[0].getsockname()[1]

        return self

    async def __aexit__(self, *_) -> None:
        if self._server is None or self._task is None:
            return

        self._server.should_exit = True
        await self._task
