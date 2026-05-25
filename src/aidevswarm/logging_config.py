"""Structlog setup: JSON renderer in containers, key-value in TTYs.

Call :func:`configure_logging` once at process start. Never use ``print``.
"""

from __future__ import annotations

import logging
import sys

import structlog


def configure_logging(*, json_logs: bool = True, level: int = logging.INFO) -> None:
    """Initialise structlog and the stdlib logger together.

    Args:
        json_logs: When True (default), emit machine-parseable JSON. When
            False, emit a human-readable key=value renderer (useful in a
            local TTY).
        level: Standard library logging level (defaults to INFO).
    """
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    shared_processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    renderer: structlog.typing.Processor = (
        structlog.processors.JSONRenderer()
        if json_logs
        else structlog.dev.ConsoleRenderer(colors=False)
    )

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a structlog logger; use the module name when in doubt."""
    return structlog.get_logger(name)
