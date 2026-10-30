"""Playwright page operations.

All direct Playwright ``Page`` usage is isolated here so the HTTP layer stays
thin and the browser interactions are easy to reason about.
"""

from __future__ import annotations

import base64
from typing import Any, cast
from urllib.parse import urlparse

from playwright.async_api import FilePayload, Locator, Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from robotsix_browser.filehub import FileHubClient
from robotsix_browser.models import (
    ClickRequest,
    NavigateRequest,
    SelectRequest,
    StateResponse,
    SubmitRequest,
    TypeRequest,
    UploadRequest,
    WaitRequest,
)

#: Schemes the service is willing to navigate to.  ``file:`` is deliberately
#: excluded to avoid turning the service into a local-file reader.
_ALLOWED_SCHEMES = frozenset({"http", "https", "data", "about"})


class UnsupportedUrlError(ValueError):
    """Raised when a navigation target uses a disallowed scheme."""


class SelectorNotFoundError(LookupError):
    """Raised when a click / value target matches no element on the page.

    Lets the HTTP layer return a clean 404 instead of letting a Playwright
    :class:`TimeoutError` bubble up as a 500 / stack trace when the target
    (CSS selector or ARIA role + name) never resolves to an element.
    """


#: Bounded timeout (ms) for locating an element when reading its value.
#: Far shorter than the global 30s action default so a missing selector fails
#: fast with a clean 404 instead of hanging the request.
_READ_VALUE_TIMEOUT_MS = 5_000

#: Bounded timeout (ms) for locating an element to click.
#: Far shorter than the global 30s action default so a missing selector or
#: role+name target fails fast with a clean 404 instead of hanging the request.
_CLICK_TIMEOUT_MS = 5_000


def _validate_url(url: str) -> str:
    scheme = urlparse(url).scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise UnsupportedUrlError(
            f"scheme {scheme!r} is not allowed (allowed: {sorted(_ALLOWED_SCHEMES)})"
        )
    return url


def _target_locator(
    page: Page, *, selector: str | None, role: str | None, name: str | None
) -> Locator:
    if selector:
        return page.locator(selector)
    # cast: Playwright types ``role`` as a large AriaRole literal; the value is
    # validated by the caller / browser at click time.
    aria_role = cast(Any, role)
    if name:
        return page.get_by_role(aria_role, name=name)
    return page.get_by_role(aria_role)


def _describe_target(
    *, selector: str | None, role: str | None, name: str | None
) -> str:
    """Human-readable description of a click target for error messages."""
    if selector:
        return f"selector {selector!r}"
    if name:
        return f"role {role!r} with name {name!r}"
    return f"role {role!r}"


async def navigate(page: Page, request: NavigateRequest) -> str:
    """Navigate the page to ``request.url`` and return the resulting URL.

    Raises :class:`UnsupportedUrlError` (mapped to a 400) when the URL scheme
    is not in the allowed set (``http``/``https``/``data``/``about``).
    """
    await page.goto(_validate_url(request.url), wait_until=request.wait_until)
    return page.url


async def get_state(page: Page) -> StateResponse:
    """Capture the page URL, title, ARIA tree and a full-page screenshot.

    Read-only: the page is not modified.
    """
    tree = await page.locator("body").aria_snapshot()
    screenshot = await page.screenshot(full_page=True)
    return StateResponse(
        url=page.url,
        title=await page.title(),
        accessibility_tree=tree,
        screenshot_base64=base64.b64encode(screenshot).decode("ascii"),
    )


async def click(page: Page, request: ClickRequest) -> str:
    """Click the CSS-selector or ARIA role/name target and return the page URL.

    Uses a bounded timeout and raises :class:`SelectorNotFoundError` (mapped to
    a clean 404) when the target matches no element.  Never use this for the
    final consequential submit — that is :func:`submit`.
    """
    locator = _target_locator(
        page, selector=request.selector, role=request.role, name=request.name
    )
    try:
        await locator.click(timeout=_CLICK_TIMEOUT_MS)
    except PlaywrightTimeoutError as exc:
        target = _describe_target(
            selector=request.selector, role=request.role, name=request.name
        )
        raise SelectorNotFoundError(f"{target} matched no element") from exc
    return page.url


async def type_text(page: Page, request: TypeRequest) -> str:
    """Fill the field matched by ``request.selector`` with ``request.text``.

    Replaces the field's current value; does not submit the form.
    """
    await page.fill(request.selector, request.text)
    return page.url


async def select_option(page: Page, request: SelectRequest) -> str:
    """Choose a ``<select>`` option by ``value`` (preferred) or by ``label``.

    Returns the page URL; does not submit the form.
    """
    if request.value is not None:
        await page.select_option(request.selector, value=request.value)
    else:
        await page.select_option(request.selector, label=request.label)
    return page.url


async def upload(page: Page, request: UploadRequest, filehub: FileHubClient) -> str:
    """Fetch ``request.file_id`` from file-hub and attach it to a file input.

    File-hub failures propagate as :class:`~robotsix_browser.filehub.FileHubError`
    (502) or :class:`~robotsix_browser.filehub.InvalidFileIdError` (400).
    """
    file = await filehub.fetch(request.file_id)
    payload: FilePayload = {
        "name": file.name,
        "mimeType": file.content_type,
        "buffer": file.content,
    }
    await page.set_input_files(request.selector, files=[payload])
    return page.url


async def wait(page: Page, request: WaitRequest) -> str:
    """Wait for ``request.selector`` to appear and/or for a page load state.

    The selector wait honours ``request.timeout_ms`` (session default when
    unset); a timeout propagates as a Playwright ``TimeoutError``.
    """
    if request.selector:
        await page.wait_for_selector(request.selector, timeout=request.timeout_ms)
    if request.state is not None:
        await page.wait_for_load_state(request.state)
    return page.url


async def read_value(page: Page, selector: str) -> str:
    """Read the current value of the field matched by ``selector``.

    Uses a bounded timeout so a selector that matches no element fails fast and
    raises :class:`SelectorNotFoundError` (mapped to a clean 404) instead of
    letting the Playwright :class:`TimeoutError` bubble up as a 500 after the
    30s global default.
    """
    try:
        return await page.input_value(selector, timeout=_READ_VALUE_TIMEOUT_MS)
    except PlaywrightTimeoutError as exc:
        raise SelectorNotFoundError(
            f"selector '{selector}' matched no element"
        ) from exc


async def submit(page: Page, request: SubmitRequest) -> str:
    """Perform the human-gated final submit / confirm click.

    This is the ONLY action that submits a form and is intentionally kept in a
    dedicated endpoint so the operator can gate it in-conversation.
    """
    locator = _target_locator(
        page, selector=request.selector, role=request.role, name=request.name
    )
    await locator.click()
    if request.wait_for_navigation:
        await page.wait_for_load_state("load")
    return page.url
