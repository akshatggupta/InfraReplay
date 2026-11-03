"""Which request is this SQL part of?

The HTTP proxy tags every forwarded request with a recording id and a
correlation id. An instrumented app binds them to the request's context, and
the SQL listener reads them back — that is the whole of correlation.

Nothing bound means nothing is being captured, which is the normal path for
uninstrumented traffic and costs one contextvar read.

During a replay the context also says which original request this one
reproduces (`replay_of` + `source_correlation`), so the app's outbound calls
can be answered from that request's recording instead of going out (v11).
"""

from contextvars import ContextVar, Token
from dataclasses import dataclass
from enum import Enum


class ExternalsMode(str, Enum):
    """What a replay does when the app calls an external service."""

    RECORDED = "recorded"  # answer from the recording; never call out (default)
    LIVE = "live"  # really call the service — needs an explicit ask


@dataclass(frozen=True)
class CaptureContext:
    recording_id: str
    correlation_id: str
    replay_of: str | None = None
    source_correlation: str | None = None
    externals: ExternalsMode = ExternalsMode.RECORDED


_CURRENT: ContextVar[CaptureContext | None] = ContextVar("infrareplay_capture", default=None)


def bind(
    recording_id: str,
    correlation_id: str,
    *,
    replay_of: str | None = None,
    source_correlation: str | None = None,
    externals: ExternalsMode = ExternalsMode.RECORDED,
) -> Token:
    return _CURRENT.set(
        CaptureContext(recording_id, correlation_id, replay_of, source_correlation, externals)
    )


def reset(token: Token) -> None:
    _CURRENT.reset(token)


def current() -> CaptureContext | None:
    return _CURRENT.get()
