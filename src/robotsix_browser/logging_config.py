"""Structured logging configuration for the robotsix-browser service.

This module wires a single :mod:`structlog` processor pipeline shared by the
whole service.  Two renderers are supported:

* a coloured, human-readable **console** renderer for local development, and
* a machine-parseable **JSON** renderer for production, so log lines can be
  shipped and queried by the fleet's log tooling.

A dedicated processor (:func:`merge_request_context`) copies the per-request
correlation / request ids maintained by ``starlette-context`` into every log
event, which lets a single request be traced from the calling agent through
robotsix-browser and on to its Vaultwarden / file-hub dependencies.

Two environment variables control the configuration:

``ROBOTSIX_LOG_LEVEL``
    Minimum level to emit (default ``INFO``).

``ROBOTSIX_LOG_FORMAT``
    ``json`` (the default, production choice) or ``console`` (development).
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

import structlog
from starlette_context import context
from structlog.typing import EventDict, Processor

__all__ = ["configure_logging", "merge_request_context"]

#: starlette-context header keys populated by the correlation / request id
#: plugins, mapped to the structlog event-dict field they are exposed under.
_CONTEXT_FIELDS: tuple[tuple[str, str], ...] = (
    ("X-Correlation-ID", "correlation_id"),
    ("X-Request-ID", "request_id"),
)


def _resolve_level(level: str | None = None) -> int:
    """Resolve a textual log level to its numeric value.

    ``level`` overrides the environment; otherwise ``ROBOTSIX_LOG_LEVEL`` is
    consulted, falling back to ``INFO`` for unknown / unset values.
    """
    name = (level or os.environ.get("ROBOTSIX_LOG_LEVEL", "INFO")).upper()
    return logging.getLevelNamesMapping().get(name, logging.INFO)


def _use_json(json_logs: bool | None = None) -> bool:
    """Decide whether the JSON renderer should be used.

    ``json_logs`` overrides the environment; otherwise ``ROBOTSIX_LOG_FORMAT``
    selects ``console`` output, defaulting to JSON (the production choice).
    """
    if json_logs is not None:
        return json_logs
    return os.environ.get("ROBOTSIX_LOG_FORMAT", "json").lower() != "console"


def merge_request_context(
    _logger: Any, _method_name: str, event_dict: EventDict
) -> EventDict:
    """structlog processor adding starlette-context request ids to each event.

    When invoked while handling an HTTP request (the ``starlette-context``
    middleware has established a context), the correlation and request ids are
    copied into the event under the ``correlation_id`` / ``request_id`` keys so
    every log line emitted during the request can be traced back to it.  Outside
    a request scope the event dict is returned unchanged.
    """
    if context.exists():
        for header, field in _CONTEXT_FIELDS:
            value = context.get(header)
            if value:
                event_dict[field] = value
    return event_dict


def configure_logging(
    *, level: str | None = None, json_logs: bool | None = None
) -> None:
    """Configure the shared structlog + stdlib logging pipeline.

    Idempotent: safe to call from the application factory on every app build.
    The chosen renderer (JSON in production, coloured console in development)
    becomes the final processor; structlog renders each event to a string and
    hands it to the stdlib root logger, whose single
    :class:`~logging.StreamHandler` writes it to stdout.

    Args:
        level: Optional explicit log level name (e.g. ``"DEBUG"``).  When
            ``None`` the ``ROBOTSIX_LOG_LEVEL`` environment variable is used
            (default ``INFO``).
        json_logs: Optional explicit renderer choice.  When ``None`` the
            ``ROBOTSIX_LOG_FORMAT`` environment variable selects ``console``
            output, defaulting to JSON.
    """
    log_level = _resolve_level(level)

    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        merge_request_context,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    renderer: Processor
    if _use_json(json_logs):
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(log_level)
