"""Which request is this SQL part of?

The HTTP proxy tags every forwarded request with a recording id and a
correlation id. An instrumented app binds them to the request's context, and
the SQL listener reads them back — that is the whole of correlation.

Nothing bound means nothing is being captured, which is the normal path for
uninstrumented traffic and costs one contextvar read.
"""

from contextvars import ContextVar, Token
from dataclasses import dataclass


@dataclass(frozen=True)
class CaptureContext:
    recording_id: str
    correlation_id: str


_CURRENT: ContextVar[CaptureContext | None] = ContextVar("infrareplay_capture", default=None)


def bind(recording_id: str, correlation_id: str) -> Token:
    return _CURRENT.set(CaptureContext(recording_id, correlation_id))


def reset(token: Token) -> None:
    _CURRENT.reset(token)


def current() -> CaptureContext | None:
    return _CURRENT.get()
