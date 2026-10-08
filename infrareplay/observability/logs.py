"""Structured logs: one JSON object per line, ready for any log shipper.

    {"event": "replay.finished", "level": "info", "timestamp": "2026-…Z",
     "request_id": "a1b2…", "replay_recording_id": "rpl_…", "seconds": 0.21}

`request_id` is bound once per API request (from `x-request-id`, or
generated) and rides along on every line that request produces, so one
request's story can be pulled out of a busy log with a single filter.

INFRAREPLAY_LOG_FORMAT=console swaps JSON for coloured, human output.
Headers and payloads are never logged — only ids, counts and timings.
"""

import logging
import sys
from enum import Enum
from typing import TextIO

import structlog

from infrareplay import config


class LogFormat(str, Enum):
    JSON = "json"
    CONSOLE = "console"


def configure(
    *,
    stream: TextIO | None = None,
    fmt: LogFormat | None = None,
    force: bool = False,
) -> None:
    """Idempotent unless forced, so tests can redirect output after startup."""

    if structlog.is_configured() and not force:
        return

    chosen = fmt or LogFormat(config.LOG_FORMAT)
    renderer = (
        structlog.dev.ConsoleRenderer()
        if chosen is LogFormat.CONSOLE
        else structlog.processors.JSONRenderer()
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(config.LOG_LEVEL)
        ),
        logger_factory=structlog.PrintLoggerFactory(file=stream or sys.stdout),
        # Off so a re-configure (tests) reaches loggers already handed out.
        cache_logger_on_first_use=False,
    )


def get_logger(name: str):
    return structlog.get_logger(name)


bind_request = structlog.contextvars.bind_contextvars
clear_request = structlog.contextvars.clear_contextvars
