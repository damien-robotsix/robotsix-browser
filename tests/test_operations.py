"""Unit tests for browser-independent bits of the operations module."""

from __future__ import annotations

from typing import Any

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from robotsix_browser.models import ClickRequest
from robotsix_browser.operations import (
    _CLICK_TIMEOUT_MS,
    _READ_VALUE_TIMEOUT_MS,
    SelectorNotFoundError,
    UnsupportedUrlError,
    _validate_url,
    click,
    read_value,
)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "https://example.com/form",
        "data:text/html,x",
        "about:blank",
    ],
)
def test_validate_url_allows_supported_schemes(url: str) -> None:
    assert _validate_url(url) == url


@pytest.mark.parametrize(
    "url", ["file:///etc/passwd", "ftp://host/x", "javascript:alert(1)"]
)
def test_validate_url_rejects_disallowed_schemes(url: str) -> None:
    with pytest.raises(UnsupportedUrlError):
        _validate_url(url)


class _MissingElementPage:
    """Minimal fake page whose ``input_value`` times out like a real miss."""

    def __init__(self) -> None:
        self.seen_timeout: float | None = None

    async def input_value(self, selector: str, timeout: float | None = None) -> str:
        self.seen_timeout = timeout
        raise PlaywrightTimeoutError("Timeout exceeded")


class _PresentElementPage:
    """Minimal fake page whose ``input_value`` returns a stored value."""

    async def input_value(self, selector: str, timeout: float | None = None) -> str:
        return "Ada"


class _FakeLocator:
    """Minimal locator whose ``click`` mirrors a present or missing target."""

    def __init__(self, *, missing: bool = False) -> None:
        self._missing = missing
        self.seen_timeout: float | None = None

    async def click(self, timeout: float | None = None) -> None:
        self.seen_timeout = timeout
        if self._missing:
            raise PlaywrightTimeoutError("Timeout exceeded")


class _PresentTargetPage:
    """Minimal fake page whose click targets exist and are clickable."""

    def __init__(self) -> None:
        self.url = "https://example.com/after-click"

    def locator(self, selector: str) -> _FakeLocator:
        return _FakeLocator(missing=False)

    def get_by_role(self, role: Any, name: str | None = None) -> _FakeLocator:
        return _FakeLocator(missing=False)


class _MissingTargetPage:
    """Minimal fake page whose click targets are absent (click times out)."""

    def __init__(self) -> None:
        self.locator_seen: _FakeLocator | None = None
        self.role_locator_seen: _FakeLocator | None = None

    def locator(self, selector: str) -> _FakeLocator:
        self.locator_seen = _FakeLocator(missing=True)
        return self.locator_seen

    def get_by_role(self, role: Any, name: str | None = None) -> _FakeLocator:
        self.role_locator_seen = _FakeLocator(missing=True)
        return self.role_locator_seen


async def test_read_value_missing_selector_raises_clean_error() -> None:
    page: Any = _MissingElementPage()
    with pytest.raises(SelectorNotFoundError) as excinfo:
        await read_value(page, "#does-not-exist")
    assert "#does-not-exist" in str(excinfo.value)
    assert "matched no element" in str(excinfo.value)
    # The miss is probed within the bounded locator timeout, not the global
    # 30s action default — this is what makes the clean 404 arrive fast.
    assert page.seen_timeout == _READ_VALUE_TIMEOUT_MS


async def test_read_value_returns_field_value() -> None:
    page: Any = _PresentElementPage()
    assert await read_value(page, "#name") == "Ada"


async def test_click_missing_selector_raises_clean_error() -> None:
    page: Any = _MissingTargetPage()
    with pytest.raises(SelectorNotFoundError) as excinfo:
        await click(page, ClickRequest(selector="#does-not-exist"))
    assert "#does-not-exist" in str(excinfo.value)
    assert "matched no element" in str(excinfo.value)
    # The miss is probed within the bounded click timeout, not the global
    # 30s action default — this is what makes the clean 404 arrive fast.
    assert page.locator_seen is not None
    assert page.locator_seen.seen_timeout == _CLICK_TIMEOUT_MS


async def test_click_missing_role_name_raises_clean_error() -> None:
    page: Any = _MissingTargetPage()
    with pytest.raises(SelectorNotFoundError) as excinfo:
        await click(page, ClickRequest(role="button", name="Sign in"))
    assert "button" in str(excinfo.value)
    assert "Sign in" in str(excinfo.value)
    assert "matched no element" in str(excinfo.value)
    # The role+name miss is likewise probed within the bounded click timeout.
    assert page.role_locator_seen is not None
    assert page.role_locator_seen.seen_timeout == _CLICK_TIMEOUT_MS


async def test_click_selector_on_present_target_returns_url() -> None:
    page: Any = _PresentTargetPage()
    assert (
        await click(page, ClickRequest(selector="#go"))
        == "https://example.com/after-click"
    )


async def test_click_role_name_on_present_target_returns_url() -> None:
    page: Any = _PresentTargetPage()
    assert (
        await click(page, ClickRequest(role="button", name="Sign in"))
        == "https://example.com/after-click"
    )
