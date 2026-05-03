"""structlog setup helpers shared across the DeathRoll bots.

Both bots emit structured JSON logs in production and pretty
console logs in local dev. ``setup_logging`` configures the standard
library ``logging`` module + ``structlog`` so that calls like
``log = structlog.get_logger()`` produce consistent output for the
configured format.

The configuration is intentionally minimal: structlog renders to
JSON via ``structlog.processors.JSONRenderer`` for production, or
to a coloured key=value format via ``structlog.dev.ConsoleRenderer``
for local development. Both attach an ISO-8601 timestamp and the
log level.
"""

from __future__ import annotations

import logging
from typing import Literal

import structlog


def setup_logging(
    level: str = "INFO",
    *,
    format: Literal["json", "console"] = "json",
) -> None:
    """Configure the standard logging + structlog pipeline.

    ``level`` is the threshold (``"INFO"`` / ``"DEBUG"`` / etc.) for
    BOTH the stdlib logger and structlog. ``format`` selects the
    final renderer.

    Calling this more than once is safe — structlog's ``configure``
    replaces previous configuration so reloads in tests do not stack
    duplicate handlers.
    """
    log_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(level=log_level, format="%(message)s")

    processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
    ]
    if format == "json":
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


__all__ = ["setup_logging"]
