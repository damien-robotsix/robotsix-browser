"""Unit tests for browser-independent bits of the operations module."""

from __future__ import annotations

import re
from typing import Any

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from robotsix_browser.models import ClickRequest
from robotsix_browser.operations import (
    _CLICK_TIMEOUT_MS,
    _READ_VALUE_TIMEOUT_MS,
    LoginFieldNotFoundError,
    SelectorNotFoundError,
    UnsupportedUrlError,
    _fill_across_frames,
    _fill_first_existing,
    _is_auth_frame,
    _login_frames,
    _password_candidates,
    _username_candidates,
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
        self.click_timeout: float | None = None

    async def click(self, timeout: float | None = None) -> None:
        self.seen_timeout = timeout
        self.click_timeout = timeout
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
        self.last_locator: _FakeLocator | None = None

    def locator(self, selector: str) -> _FakeLocator:
        self.locator_seen = _FakeLocator(missing=True)
        self.last_locator = self.locator_seen
        return self.locator_seen

    def get_by_role(self, role: Any, name: str | None = None) -> _FakeLocator:
        self.role_locator_seen = _FakeLocator(missing=True)
        self.last_locator = self.role_locator_seen
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
    # The miss fails fast via the bounded click timeout (same style as the
    # fill-credentials fix) rather than the 30s global Playwright default.
    assert page.last_locator is not None
    assert page.last_locator.click_timeout == _CLICK_TIMEOUT_MS


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


class _DetectionFakePage:
    """Fake page mirroring which login fields exist on the form.

    ``locator(css)`` is present when the CSS selector is in ``present``;
    ``get_by_role`` is present when the accessible-name pattern matches one of
    the ``aria_names``.  Successful fills are recorded on ``filled`` in call
    order so tests can assert which detection tier bound the field.
    """

    def __init__(
        self,
        present: set[str] | None = None,
        aria_names: set[str] | None = None,
    ) -> None:
        self._present = set() if present is None else present
        self._aria_names = set() if aria_names is None else aria_names
        self.filled: list[tuple[str, str]] = []

    def locator(self, selector: str) -> _DetectionFakeLocator:
        return _DetectionFakeLocator(self, selector, selector in self._present)

    def get_by_role(self, role: Any, name: Any = None) -> _DetectionFakeLocator:
        if isinstance(name, re.Pattern):
            present = any(name.search(aria) for aria in self._aria_names)
            selector = f"get_by_role({role}, name={name.pattern})"
        else:
            present = name in self._aria_names
            selector = f"get_by_role({role}, name={name})"
        return _DetectionFakeLocator(self, selector, present)


class _DetectionFakeLocator:
    """Minimal locator mirroring a present or absent login-field candidate."""

    def __init__(self, page: _DetectionFakePage, selector: str, present: bool) -> None:
        self._page = page
        self._selector = selector
        self._present = present
        self.seen_timeout: int | None = None

    async def count(self) -> int:
        return 1 if self._present else 0

    async def fill(self, value: str, *, timeout: int | None = None) -> None:
        self.seen_timeout = timeout
        self._page.filled.append((self._selector, value))


async def _fill_guest_credentials(page: Any) -> list[tuple[str, str]]:
    """Drive the two-step detection fill the way ``fill_credentials`` does.

    The caller-supplied literals ``#user`` / ``#pass`` exist on none of the
    fixture forms, so binding must come from the candidate fallback tiers.
    """
    await _fill_first_existing(
        page,
        _username_candidates(page, "#user"),
        "alice@example.com",
        field_label="username",
        timeout_ms=500,
    )
    await _fill_first_existing(
        page,
        _password_candidates(page, "#pass"),
        "s3cr3t",
        field_label="password",
        timeout_ms=500,
    )
    return page.filled


async def test_fill_detection_falls_through_to_known_login_ids() -> None:
    """The guest form's ``#session_key`` / ``#session_password`` still bind via
    the fallback tier when the caller-supplied selectors match nothing —
    detection is not tied to the literal CSS passed by the caller."""
    page: Any = _DetectionFakePage(present={"#session_key", "#session_password"})
    filled = await _fill_guest_credentials(page)
    assert ("#session_key", "alice@example.com") in filled
    assert ("#session_password", "s3cr3t") in filled
    # The absent literal caller selectors must not have driven the fill.
    assert not any(selector in {"#user", "#pass"} for selector, _ in filled)


async def test_fill_detection_binds_aria_role_locator() -> None:
    """A form discoverable only through ARIA accessible names binds via the
    role+name fallback tier — no id/name/type attribute matches anything."""
    page: Any = _DetectionFakePage(aria_names={"Username", "Password"})
    filled = await _fill_guest_credentials(page)
    assert any(
        selector.startswith("get_by_role(textbox") and value == "alice@example.com"
        for selector, value in filled
    )
    assert any(
        selector.startswith("get_by_role(textbox") and value == "s3cr3t"
        for selector, value in filled
    )


async def test_fill_detection_raises_clean_error_when_no_field_present() -> None:
    """No candidate matching means the clean not-found error (mapped to a 4xx),
    never a raw timeout."""
    page: Any = _DetectionFakePage()
    with pytest.raises(LoginFieldNotFoundError) as excinfo:
        await _fill_guest_credentials(page)
    message = str(excinfo.value)
    assert "username" in message
    assert "not found" in message


class _FakeFrame:
    """Minimal fake frame mirroring Playwright's ``Frame`` surface used by
    frame scoping: ``url``, ``locator`` and ``get_by_role``.

    ``present`` lists the CSS selectors that resolve, ``aria_names`` the
    accessible names that match the ARIA role locator.  Successful fills are
    recorded on ``fill_log`` as ``(frame_url, selector, value)`` triples so
    tests can assert which frame the fill landed in.
    """

    def __init__(
        self,
        url: str,
        present: set[str] | None = None,
        aria_names: set[str] | None = None,
        fill_log: list[tuple[str, str, str]] | None = None,
    ) -> None:
        self.url = url
        self._present = set() if present is None else present
        self._aria_names = set() if aria_names is None else aria_names
        self.fill_log = [] if fill_log is None else fill_log

    def locator(self, selector: str) -> _FakeFrameLocator:
        return _FakeFrameLocator(self, selector, selector in self._present)

    def get_by_role(self, role: Any, name: Any = None) -> _FakeFrameLocator:
        if isinstance(name, re.Pattern):
            present = any(name.search(aria) for aria in self._aria_names)
            selector = f"get_by_role({role}, name={name.pattern})"
        else:
            present = name in self._aria_names
            selector = f"get_by_role({role}, name={name})"
        return _FakeFrameLocator(self, selector, present)


class _FakeFrameLocator:
    """Minimal locator mirroring a present/absent candidate inside a frame."""

    def __init__(self, frame: _FakeFrame, selector: str, present: bool) -> None:
        self._frame = frame
        self._selector = selector
        self._present = present

    async def count(self) -> int:
        return 1 if self._present else 0

    async def fill(self, value: str, *, timeout: int | None = None) -> None:
        self._frame.fill_log.append((self._frame.url, self._selector, value))


class _FramesFakePage:
    """Fake page modelling a main frame plus nested child iframes.

    Exposes the ``main_frame`` / ``frames`` surface used by frame scoping and
    shares one ``filled`` log across all frames for easy assertions.
    """

    def __init__(
        self,
        main_frame: _FakeFrame,
        child_frames: list[_FakeFrame] | None = None,
    ) -> None:
        self.filled: list[tuple[str, str, str]] = []
        self.main_frame = main_frame
        self.frames = [main_frame] + (list(child_frames) if child_frames else [])
        for frame in self.frames:
            frame.fill_log = self.filled


async def _fill_guest_across_frames(page: Any) -> list[tuple[str, str, str]]:
    """Drive the two-step detection fill across frames the way ``fill_credentials``
    does, returning the frame-tagged fill log."""
    await _fill_across_frames(
        page,
        lambda scope: _username_candidates(scope, "#user"),
        "alice@example.com",
        field_label="username",
        timeout_ms=500,
    )
    await _fill_across_frames(
        page,
        lambda scope: _password_candidates(scope, "#pass"),
        "s3cr3t",
        field_label="password",
        timeout_ms=500,
    )
    return page.filled


async def test_fill_scopes_to_main_frame_when_cookie_iframe_present() -> None:
    """The top-level login form wins over a nested cookie-policy iframe: the
    fill binds the visible form, never the consent iframe's fields."""
    main = _FakeFrame(
        "https://www.linkedin.com/login",
        present={"#session_key", "#session_password"},
    )
    cookie = _FakeFrame(
        "https://fr.linkedin.com/legal/cookie-policy",
        present={"#email", "input[type='password']"},
    )
    page = _FramesFakePage(main, [cookie])
    filled = await _fill_guest_across_frames(page)
    assert (
        "https://www.linkedin.com/login",
        "#session_key",
        "alice@example.com",
    ) in filled
    assert (
        "https://www.linkedin.com/login",
        "#session_password",
        "s3cr3t",
    ) in filled
    assert not any(
        url == "https://fr.linkedin.com/legal/cookie-policy"
        for url, _selector, _value in filled
    )


async def test_fill_binds_login_form_only() -> None:
    """A page with just the login form binds it in the main frame."""
    main = _FakeFrame("https://example.com/login", present={"#email", "#password"})
    page = _FramesFakePage(main)
    filled = await _fill_guest_across_frames(page)
    assert ("https://example.com/login", "#email", "alice@example.com") in filled
    assert ("https://example.com/login", "#password", "s3cr3t") in filled


async def test_fill_descends_to_same_origin_auth_frame_only() -> None:
    """When the main frame holds no login form, detection descends into a
    same-origin auth child frame but still excludes a cross-origin consent
    iframe even when that iframe contains login-like fields."""
    main = _FakeFrame("https://example.com/login")
    auth = _FakeFrame("https://example.com/guest", present={"#email", "#password"})
    cookie = _FakeFrame(
        "https://fr.example.com/legal/cookie-policy",
        present={"#email", "input[type='password']"},
    )
    page = _FramesFakePage(main, [auth, cookie])
    filled = await _fill_guest_across_frames(page)
    assert ("https://example.com/guest", "#email", "alice@example.com") in filled
    assert ("https://example.com/guest", "#password", "s3cr3t") in filled
    assert not any(
        url == "https://fr.example.com/legal/cookie-policy"
        for url, _selector, _value in filled
    )


async def test_fill_across_frames_raises_clean_error_on_miss() -> None:
    """A genuine miss across all eligible frames still fails cleanly (4xx),
    never a raw timeout."""
    main = _FakeFrame("https://example.com/login")
    page = _FramesFakePage(main)
    with pytest.raises(LoginFieldNotFoundError) as excinfo:
        await _fill_guest_across_frames(page)
    message = str(excinfo.value)
    assert "username" in message
    assert "not found" in message


def test_auth_frame_excludes_cross_origin_and_consent_frames() -> None:
    """Only same-origin, non-consent frames are eligible for login detection."""
    main_url = "https://www.linkedin.com/login"
    assert (
        _is_auth_frame(_FakeFrame("https://www.linkedin.com/guest"), main_url) is True
    )
    # Cross-origin cookie-policy iframe is excluded.
    assert (
        _is_auth_frame(
            _FakeFrame("https://fr.linkedin.com/legal/cookie-policy"), main_url
        )
        is False
    )
    # Same-origin but legal/consent path is still excluded.
    assert (
        _is_auth_frame(
            _FakeFrame("https://www.linkedin.com/legal/cookie-policy"), main_url
        )
        is False
    )
    # Blank frames are never auth content.
    assert _is_auth_frame(_FakeFrame("about:blank"), main_url) is False


def test_login_frames_orders_main_first_and_filters_children() -> None:
    """The top-level frame always leads; only eligible child frames follow."""
    main = _FakeFrame("https://www.linkedin.com/login")
    auth = _FakeFrame("https://www.linkedin.com/guest")
    cookie = _FakeFrame("https://fr.linkedin.com/legal/cookie-policy")
    page = _FramesFakePage(main, [auth, cookie])
    frames = _login_frames(page)
    assert frames[0] is main
    assert auth in frames
    assert cookie not in frames
