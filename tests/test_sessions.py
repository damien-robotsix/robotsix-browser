"""Unit tests for :mod:`robotsix_browser.sessions`.

``SessionManager``'s Playwright/Chromium I/O is faked so the lifecycle logic is
exercised in CI without a browser binary.  The HTTP-level tests in
``tests/test_api.py`` that drive real headless Chromium are skipped when no
browser is installed, leaving the manager's state transitions untested there.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest

from robotsix_browser.sessions import Session, SessionManager, SessionNotFoundError


@pytest.fixture
def fakes() -> dict[str, Mock]:
    """Fake Playwright/Chromium objects backing a :class:`SessionManager`."""
    page = Mock(name="page")
    context = Mock(name="context")
    context.new_page = AsyncMock(return_value=page)
    context.close = AsyncMock()
    browser = Mock(name="browser")
    browser.new_context = AsyncMock(return_value=context)
    browser.close = AsyncMock()
    playwright = Mock(name="playwright")
    playwright.chromium.launch = AsyncMock(return_value=browser)
    playwright.stop = AsyncMock()
    playwright_cm = Mock(name="async_playwright()")
    playwright_cm.start = AsyncMock(return_value=playwright)
    return {
        "async_playwright": Mock(name="async_playwright", return_value=playwright_cm),
        "playwright_cm": playwright_cm,
        "playwright": playwright,
        "browser": browser,
        "context": context,
        "page": page,
    }


@pytest.fixture
def manager(fakes: dict[str, Mock]) -> tuple[SessionManager, dict[str, Mock]]:
    """A :class:`SessionManager` with ``async_playwright`` patched out."""
    manager_obj = SessionManager(headless=True)
    with patch("robotsix_browser.sessions.async_playwright", fakes["async_playwright"]):
        yield manager_obj, fakes


async def test_open_session_launches_browser_lazily_once(
    manager: tuple[SessionManager, dict[str, Mock]],
) -> None:
    m, fakes = manager
    session = await m.open_session()

    assert isinstance(session, Session)
    assert session.id
    fakes["async_playwright"].assert_called_once_with()
    fakes["playwright"].chromium.launch.assert_awaited_once_with(
        headless=True, args=["--no-sandbox"]
    )
    fakes["browser"].new_context.assert_awaited_once_with()

    # A second open shares the already-launched browser but gets a new context.
    second = await m.open_session()
    assert second.id != session.id
    assert fakes["playwright"].chromium.launch.await_count == 1
    assert fakes["browser"].new_context.await_count == 2


async def test_open_session_reopens_existing(
    manager: tuple[SessionManager, dict[str, Mock]],
) -> None:
    m, fakes = manager
    first = await m.open_session("s1")
    reopened = await m.open_session("s1")

    assert reopened is first
    assert fakes["browser"].new_context.await_count == 1
    assert fakes["playwright"].chromium.launch.await_count == 1


async def test_concurrent_open_sessions_launch_browser_once(
    manager: tuple[SessionManager, dict[str, Mock]],
) -> None:
    m, fakes = manager
    first, second = await asyncio.gather(m.open_session(), m.open_session())

    assert first.id != second.id
    assert fakes["playwright"].chromium.launch.await_count == 1
    assert fakes["browser"].new_context.await_count == 2


async def test_get_returns_open_session(
    manager: tuple[SessionManager, dict[str, Mock]],
) -> None:
    m, _fakes = manager
    opened = await m.open_session("s1")

    assert m.get("s1") is opened


async def test_get_unknown_session_raises(
    manager: tuple[SessionManager, dict[str, Mock]],
) -> None:
    m, _fakes = manager
    with pytest.raises(SessionNotFoundError):
        m.get("nope")


async def test_close_session_closes_context_and_forgets(
    manager: tuple[SessionManager, dict[str, Mock]],
) -> None:
    m, fakes = manager
    await m.open_session("s1")
    await m.close_session("s1")

    fakes["context"].close.assert_awaited_once_with()
    with pytest.raises(SessionNotFoundError):
        m.get("s1")


async def test_close_session_unknown_is_a_noop(
    manager: tuple[SessionManager, dict[str, Mock]],
) -> None:
    m, fakes = manager
    await m.close_session("nope")

    fakes["context"].close.assert_not_awaited()


async def test_stop_closes_everything_and_is_idempotent(
    manager: tuple[SessionManager, dict[str, Mock]],
) -> None:
    m, fakes = manager
    await m.open_session()
    await m.open_session()

    await m.stop()
    assert fakes["context"].close.await_count == 2
    fakes["browser"].close.assert_awaited_once_with()
    fakes["playwright"].stop.assert_awaited_once_with()

    # A second stop is a safe no-op: nothing is closed or stopped again.
    await m.stop()
    assert fakes["context"].close.await_count == 2
    fakes["browser"].close.assert_awaited_once_with()
    fakes["playwright"].stop.assert_awaited_once_with()


async def test_stop_forgets_sessions_and_allows_relaunch(
    manager: tuple[SessionManager, dict[str, Mock]],
) -> None:
    m, fakes = manager
    await m.open_session("s1")
    await m.stop()

    with pytest.raises(SessionNotFoundError):
        m.get("s1")
    # Chromium is relaunched on the next open after a stop.
    session = await m.open_session()
    assert session.id != "s1"
    assert fakes["playwright"].chromium.launch.await_count == 2
