"""Session manager: one isolated browser context + page per session."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)


class SessionNotFoundError(KeyError):
    """Raised when a session id is not known to the manager."""


@dataclass(slots=True)
class Session:
    """A single browser session: an isolated context and its page."""

    id: str
    context: BrowserContext
    page: Page


class SessionManager:
    """Owns the Playwright/Chromium lifecycle and per-session contexts.

    Chromium is launched lazily on the first :meth:`open_session` call so that
    importing / constructing the manager (and non-browser unit tests) never
    require a browser binary to be installed.
    """

    def __init__(
        self, *, headless: bool = True, default_timeout_ms: int | None = None
    ) -> None:
        self._headless = headless
        self._default_timeout_ms = default_timeout_ms
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._sessions: dict[str, Session] = {}
        self._lock = asyncio.Lock()

    async def _ensure_browser(self) -> Browser:
        if self._browser is None:
            self._playwright = await async_playwright().start()
            # --no-sandbox is required to run Chromium as root inside a
            # container image; harmless on non-root CI runners.
            self._browser = await self._playwright.chromium.launch(
                headless=self._headless, args=["--no-sandbox"]
            )
        return self._browser

    async def open_session(self, session_id: str | None = None) -> Session:
        """Open a new session, or return the existing one for ``session_id``."""
        async with self._lock:
            if session_id is not None and session_id in self._sessions:
                return self._sessions[session_id]
            browser = await self._ensure_browser()
            new_id = session_id or uuid.uuid4().hex
            context = await browser.new_context()
            page = await context.new_page()
            if self._default_timeout_ms is not None:
                # Wire the configured "Default action timeout" into Playwright's
                # page-level defaults so click / fill / select / wait operations
                # and navigations pick it up unless a request overrides it.
                await page.set_default_timeout(self._default_timeout_ms)
                await page.set_default_navigation_timeout(self._default_timeout_ms)
            session = Session(id=new_id, context=context, page=page)
            self._sessions[new_id] = session
            return session

    def get(self, session_id: str) -> Session:
        """Return an open session or raise :class:`SessionNotFoundError`."""
        try:
            return self._sessions[session_id]
        except KeyError:
            raise SessionNotFoundError(session_id) from None

    async def close_session(self, session_id: str) -> None:
        """Close and forget a session (no-op if unknown)."""
        async with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is not None:
            await session.context.close()

    async def stop(self) -> None:
        """Close every session and shut down Chromium and Playwright."""
        async with self._lock:
            for session in list(self._sessions.values()):
                await session.context.close()
            self._sessions.clear()
            if self._browser is not None:
                await self._browser.close()
                self._browser = None
            if self._playwright is not None:
                await self._playwright.stop()
                self._playwright = None
