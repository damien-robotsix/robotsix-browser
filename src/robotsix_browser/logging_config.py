"""Structured logging configuration for the robotsix-browser service.

This module is a thin wrapper over :func:`robotsix_llmio.setup_structlog`, the
fleet-shared structlog setup.  ``setup_structlog`` wires a single
:class:`structlog.stdlib.ProcessorFormatter` bridge onto the root logger so that
both structlog-native calls and foreign stdlib records (uvicorn, etc.) render
through one processor chain and one renderer (JSON in production, a plain
console renderer for local development).  It is idempotent and, with
``correlation_id=True``, includes :func:`structlog.contextvars.merge_contextvars`
so any ids bound on the current context appear on every event.

Per-request correlation / request ids are no longer merged by a custom
processor (``setup_structlog`` exposes no hook to inject one).  Instead the HTTP
middleware binds them into structlog contextvars for the duration of the
request via :func:`bound_request_context`, which reads the ids maintained by
``starlette-context``.  This lets a single request be traced from the calling
agent through robotsix-browser and on to its Vaultwarden / file-hub
dependencies.

Two environment variables control the configuration (browser keeps its own env
contract; the explicit ``level``/``fmt`` arguments passed below deliberately
bypass llmio's differently-named ``LOG_LEVEL``/``LOG_FORMAT`` fallbacks):

``ROBOTSIX_LOG_LEVEL``
    Minimum level to emit (default ``INFO``).

``ROBOTSIX_LOG_FORMAT``
    ``json`` (the default, production choice) or ``console`` (development).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

import structlog
from robotsix_llmio import setup_structlog
from starlette_context import context

__all__ = ["bound_request_context", "configure_logging"]

#: starlette-context header keys populated by the correlation / request id
#: plugins, mapped to the structlog contextvars field they are bound under.
_CONTEXT_FIELDS: tuple[tuple[str, str], ...] = (
    ("X-Correlation-ID", "correlation_id"),
    ("X-Request-ID", "request_id"),
)


def _resolve_level(level: str | None = None) -> str:
    """Resolve the textual log level.

    ``level`` overrides the environment; otherwise ``ROBOTSIX_LOG_LEVEL`` is
    consulted, falling back to ``INFO``.  The name is passed through to
    ``setup_structlog`` explicitly so llmio's ``LOG_LEVEL`` fallback is never
    consulted.
    """
    return (level or os.environ.get("ROBOTSIX_LOG_LEVEL", "INFO")).upper()


def _resolve_fmt(json_logs: bool | None = None) -> str:
    """Resolve the renderer name (``"json"`` or ``"console"``).

    ``json_logs`` overrides the environment; otherwise ``ROBOTSIX_LOG_FORMAT``
    selects ``console`` output, defaulting to JSON (the production choice).  The
    resolved value is passed to ``setup_structlog`` explicitly so llmio's
    ``LOG_FORMAT`` fallback (which defaults to ``console``) is never consulted.
    """
    if json_logs is None:
        json_logs = os.environ.get("ROBOTSIX_LOG_FORMAT", "json").lower() != "console"
    return "json" if json_logs else "console"


@contextmanager
def bound_request_context() -> Iterator[None]:
    """Bind the per-request correlation / request ids for the block's duration.

    Reads the ids maintained by ``starlette-context`` (populated by the
    ``CorrelationIdPlugin`` / ``RequestIdPlugin`` middleware) and binds them into
    structlog's contextvars, so every event emitted inside the ``with`` block —
    including those from downstream route handlers — carries them.  Keys whose
    context value is absent or falsy are skipped, and outside a request scope
    nothing is bound.  All bindings are removed again on exit.
    """
    bindings: dict[str, str] = {}
    if context.exists():
        for header, field in _CONTEXT_FIELDS:
            value = context.get(header)
            if value:
                bindings[field] = value
    with structlog.contextvars.bound_contextvars(**bindings):
        yield


def configure_logging(
    *, level: str | None = None, json_logs: bool | None = None
) -> None:
    """Configure the shared structlog + stdlib logging pipeline.

    Delegates all pipeline wiring to :func:`robotsix_llmio.setup_structlog`,
    passing the level and renderer resolved from browser's own environment
    contract explicitly (so llmio's differently-named env fallbacks are never
    used).  ``correlation_id=True`` ensures ids bound via
    :func:`bound_request_context` are merged onto each event.  Idempotent: safe
    to call from the application factory on every app build — ``setup_structlog``
    reuses its marked root handler.

    Args:
        level: Optional explicit log level name (e.g. ``"DEBUG"``).  When
            ``None`` the ``ROBOTSIX_LOG_LEVEL`` environment variable is used
            (default ``INFO``).
        json_logs: Optional explicit renderer choice.  When ``None`` the
            ``ROBOTSIX_LOG_FORMAT`` environment variable selects ``console``
            output, defaulting to JSON.
    """
    setup_structlog(
        level=_resolve_level(level),
        fmt=_resolve_fmt(json_logs),
        loggers=("robotsix_browser", "robotsix_llmio"),
        correlation_id=True,
    )
