"""Tests for structured logging and per-request correlation ids.

These exercise :mod:`robotsix_browser.logging_config` and the request-logging /
correlation-id middleware wired into :func:`robotsix_browser.app.create_app`.
"""

from __future__ import annotations

import json

import pytest
import structlog
from fastapi.testclient import TestClient

from robotsix_browser.app import create_app
from robotsix_browser.config import Settings
from robotsix_browser.logging_config import configure_logging


def _json_log_lines(captured: str) -> list[dict[str, object]]:
    """Parse the JSON log lines out of captured stdout."""
    records: list[dict[str, object]] = []
    for line in captured.splitlines():
        stripped = line.strip()
        if stripped.startswith("{"):
            try:
                records.append(json.loads(stripped))
            except json.JSONDecodeError:
                continue
    return records


def test_request_ids_absent_outside_request_scope(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Events logged outside a request carry no correlation / request ids.

    ``bound_request_context`` only binds the ids while an HTTP request is being
    handled, so a bare ``structlog`` log line emitted outside that scope must not
    leak them.
    """
    monkeypatch.setenv("ROBOTSIX_LOG_FORMAT", "json")
    monkeypatch.setenv("ROBOTSIX_LOG_LEVEL", "INFO")
    configure_logging()

    logger = structlog.get_logger("test.scope")
    logger.info("outside-request-event")

    matching = [
        r
        for r in _json_log_lines(capsys.readouterr().out)
        if r.get("event") == "outside-request-event"
    ]
    assert matching, "expected the out-of-request event to be logged"
    assert all("correlation_id" not in r and "request_id" not in r for r in matching)


def test_requests_are_logged_as_json_with_correlation_id(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Each HTTP request emits machine-parseable JSON logs carrying the same
    correlation id that is echoed back on the response."""
    monkeypatch.setenv("ROBOTSIX_LOG_FORMAT", "json")
    monkeypatch.setenv("ROBOTSIX_LOG_LEVEL", "INFO")

    app = create_app(Settings(headless=True))
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    correlation_id = response.headers.get("X-Correlation-ID")
    assert correlation_id

    records = _json_log_lines(capsys.readouterr().out)
    starts = [r for r in records if r.get("event") == "request.start"]
    assert starts, "expected a JSON request.start log line"
    assert any(
        r.get("correlation_id") == correlation_id
        and r.get("method") == "GET"
        and r.get("path") == "/health"
        for r in starts
    )
    assert any(
        r.get("event") == "request.finished"
        and r.get("status_code") == 200
        and r.get("correlation_id") == correlation_id
        for r in records
    )


def test_log_level_env_filters_events_below_threshold(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``ROBOTSIX_LOG_LEVEL`` suppresses events below the configured level."""
    monkeypatch.setenv("ROBOTSIX_LOG_FORMAT", "json")
    monkeypatch.setenv("ROBOTSIX_LOG_LEVEL", "WARNING")
    configure_logging()

    logger = structlog.get_logger("test.threshold")
    logger.info("suppressed-event")
    logger.warning("emitted-event")

    events = {r.get("event") for r in _json_log_lines(capsys.readouterr().out)}
    assert "emitted-event" in events
    assert "suppressed-event" not in events
