"""Playwright page operations.

All direct Playwright ``Page`` usage is isolated here so the HTTP layer stays
thin and the browser interactions are easy to reason about.
"""

from __future__ import annotations

import base64
import re
from typing import Any, cast
from urllib.parse import urlparse

from playwright.async_api import FilePayload, Locator, Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from robotsix_browser.filehub import FileHubClient
from robotsix_browser.models import (
    ClickRequest,
    FillCredentialsRequest,
    NavigateRequest,
    SelectRequest,
    StateResponse,
    SubmitRequest,
    TypeRequest,
    UploadRequest,
    WaitRequest,
)
from robotsix_browser.vault import VaultClient

#: Schemes the service is willing to navigate to.  ``file:`` is deliberately
#: excluded to avoid turning the service into a local-file reader.
_ALLOWED_SCHEMES = frozenset({"http", "https", "data", "about"})


class UnsupportedUrlError(ValueError):
    """Raised when a navigation target uses a disallowed scheme."""


class LoginFieldNotFoundError(LookupError):
    """Raised when a login field selector is absent on the current page.

    Signals that the expected form was not rendered (e.g. a cookie-consent
    interstitial / anti-automation variant was served) rather than a
    vault/service failure, so the HTTP layer can return a clean 4xx instead of
    letting a Playwright timeout surface as a 500.
    """


#: Default bounded timeout (ms) for locating a login field during credential
#: fill.  Deliberately far shorter than the global 30s action default so a
#: missing login form fails fast instead of blocking the request.
_CREDENTIAL_FILL_TIMEOUT_MS = 5_000

#: Bounded timeout (ms) for the best-effort consent-wall dismissal click.
_CONSENT_DISMISS_TIMEOUT_MS = 2_000

#: ARIA accessible-name pattern for common cookie-consent / "accept & continue"
#: interstitial buttons dismissed best-effort before filling the login form.
_CONSENT_BUTTON_NAME = re.compile(
    r"accept|agree|allow|continue|got it|i understand|dismiss|close|ok",
    re.IGNORECASE,
)


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


async def navigate(page: Page, request: NavigateRequest) -> str:
    await page.goto(_validate_url(request.url), wait_until=request.wait_until)
    return page.url


async def get_state(page: Page) -> StateResponse:
    tree = await page.locator("body").aria_snapshot()
    screenshot = await page.screenshot(full_page=True)
    return StateResponse(
        url=page.url,
        title=await page.title(),
        accessibility_tree=tree,
        screenshot_base64=base64.b64encode(screenshot).decode("ascii"),
    )


async def click(page: Page, request: ClickRequest) -> str:
    locator = _target_locator(
        page, selector=request.selector, role=request.role, name=request.name
    )
    await locator.click()
    return page.url


async def type_text(page: Page, request: TypeRequest) -> str:
    await page.fill(request.selector, request.text)
    return page.url


async def select_option(page: Page, request: SelectRequest) -> str:
    if request.value is not None:
        await page.select_option(request.selector, value=request.value)
    else:
        await page.select_option(request.selector, label=request.label)
    return page.url


async def upload(page: Page, request: UploadRequest, filehub: FileHubClient) -> str:
    file = await filehub.fetch(request.file_id)
    payload: FilePayload = {
        "name": file.name,
        "mimeType": file.content_type,
        "buffer": file.content,
    }
    await page.set_input_files(request.selector, files=[payload])
    return page.url


async def _dismiss_consent_walls(page: Page) -> None:
    """Best-effort dismissal of cookie-consent / interstitial walls.

    Clicks the first visible "accept & continue"-style button (matched by ARIA
    role + accessible name) so an underlying login form can render.  Any
    failure — no such button, click race, navigation — is swallowed: this is
    purely a best-effort attempt and must never turn into a request error.
    """
    for role in ("button", "link"):
        locator = page.get_by_role(cast(Any, role), name=_CONSENT_BUTTON_NAME)
        try:
            if await locator.count() == 0:
                continue
            await locator.first.click(timeout=_CONSENT_DISMISS_TIMEOUT_MS)
            return
        except Exception:
            continue


async def _fill_login_field(
    page: Page, selector: str, value: str, *, timeout_ms: int
) -> None:
    """Fill a single login field within a bounded timeout.

    Raises :class:`LoginFieldNotFoundError` (mapped to a clean 4xx) instead of
    letting the Playwright :class:`TimeoutError` bubble up as a 500 when the
    selector never appears.
    """
    try:
        await page.fill(selector, value, timeout=timeout_ms)
    except PlaywrightTimeoutError as exc:
        raise LoginFieldNotFoundError(
            f"login field {selector!r} not found on current page"
        ) from exc


async def fill_credentials(
    page: Page,
    request: FillCredentialsRequest,
    vault: VaultClient,
    *,
    timeout_ms: int = _CREDENTIAL_FILL_TIMEOUT_MS,
) -> str:
    """Fetch a scoped vault entry and fill the username / password fields.

    The secret is typed directly into the browser field and is never returned.
    This only fills the form — it does NOT submit, preserving the human
    submit-gate (``/submit`` remains the sole submit path).

    Before filling, common cookie-consent / interstitial walls are dismissed
    best-effort so the login form renders.  Each field is located within a
    short, bounded ``timeout_ms`` (not the 30s global default); a still-absent
    selector raises :class:`LoginFieldNotFoundError` for a clean 4xx.
    """
    credential = await vault.get_credential(request.entry)
    await _dismiss_consent_walls(page)
    await _fill_login_field(
        page, request.username_selector, credential.username, timeout_ms=timeout_ms
    )
    await _fill_login_field(
        page, request.password_selector, credential.password, timeout_ms=timeout_ms
    )
    return page.url


async def wait(page: Page, request: WaitRequest) -> str:
    if request.selector:
        await page.wait_for_selector(request.selector, timeout=request.timeout_ms)
    if request.state is not None:
        await page.wait_for_load_state(request.state)
    return page.url


async def read_value(page: Page, selector: str) -> str:
    return await page.input_value(selector)


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
