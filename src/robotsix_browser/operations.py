"""Playwright page operations.

All direct Playwright ``Page`` usage is isolated here so the HTTP layer stays
thin and the browser interactions are easy to reason about.
"""

from __future__ import annotations

import base64
import re
from collections.abc import Callable
from typing import Any, cast
from urllib.parse import urlparse

from playwright._impl._api_structures import SetCookieParam
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


class SelectorNotFoundError(LookupError):
    """Raised when a click / value target matches no element on the page.

    Lets the HTTP layer return a clean 404 instead of letting a Playwright
    :class:`TimeoutError` bubble up as a 500 / stack trace when the target
    (CSS selector or ARIA role + name) never resolves to an element.
    """


#: Default bounded timeout (ms) for locating a login field during credential
#: fill.  Deliberately far shorter than the global 30s action default so a
#: missing login form fails fast instead of blocking the request.
_CREDENTIAL_FILL_TIMEOUT_MS = 5_000

#: Bounded timeout (ms) for the best-effort consent-wall dismissal click.
_CONSENT_DISMISS_TIMEOUT_MS = 2_000

#: Bounded timeout (ms) for locating an element when reading its value.
#: Far shorter than the global 30s action default so a missing selector fails
#: fast with a clean 404 instead of hanging the request.
_READ_VALUE_TIMEOUT_MS = 5_000

#: Bounded timeout (ms) for locating an element to click.
#: Far shorter than the global 30s action default so a missing selector or
#: role+name target fails fast with a clean 404 instead of hanging the request.
_CLICK_TIMEOUT_MS = 5_000

#: ARIA accessible-name pattern for common cookie-consent / "accept & continue"
#: interstitial buttons dismissed best-effort before filling the login form.
_CONSENT_BUTTON_NAME = re.compile(
    r"accept|agree|allow|continue|got it|i understand|dismiss|close|ok",
    re.IGNORECASE,
)

#: ARIA accessible-name pattern for a username / email / login textbox.
#: Matched anywhere in the accessible name (re.search semantics).
_USERNAME_NAME = re.compile(
    r"\busername\b|\buser\b|\bemail\b|\be-?mail\b|\blogin\b|\bsign.?in\b|\baccount\b|\bidentifier\b",
    re.IGNORECASE,
)

#: ARIA accessible-name pattern for a password textbox.
#: Matched anywhere in the accessible name (re.search semantics).
_PASSWORD_NAME = re.compile(
    r"password|passwd|pass phrase|current password", re.IGNORECASE
)

#: Hostname / path signals that identify a cookie-consent, legal or privacy
#: wall iframe.  Such frames are never the login form, so they are excluded
#: from login-field detection to avoid typing credentials into an unintended
#: (possibly cross-origin) frame.
_CONSENT_FRAME_PATTERN = re.compile(
    r"cookie|consent|legal|privacy|policy|cmp|trustarc|onetrust|didomi|"
    r"usercentrics|cookielaw",
    re.IGNORECASE,
)

#: URL path signals for a *full-page* consent redirect: the top-level page was
#: hard-redirected away from a login to a cookie-policy / consent wall.  This
#: is distinct from :data:`_CONSENT_FRAME_PATTERN`, which flags consent
#: *iframes* nested under the main page.  It targets the LinkedIn whole-page
#: legal redirects — live-validated against the real wall, a fresh US/guest
#: session hard-redirects to ``/legal/user-agreement`` while the EU one goes to
#: ``/legal/cookie-policy`` — plus equivalents such as ``/cookie-policy`` /
#: ``/consent``, so fill-credentials can recover before attempting to bind the
#: (now absent) login form.
_CONSENT_REDIRECT_PATTERN = re.compile(
    r"/legal/(?:cookie(?:-|_)policy|user(?:-|_)agreement)(?=[/?#]|$)|"
    r"/cookie(?:-|_)policy(?=[/?#]|$)|/consent(?=[/?#]|$)",
    re.IGNORECASE,
)

#: LinkedIn consent / session cookies that must be present in the browser
#: context up front so LinkedIn does not hard-redirect the whole page to the
#: cookie-policy wall (``fr.linkedin.com/legal/cookie-policy``) on every
#: ``/login`` load.  ``li_gc`` is LinkedIn's GDPR consent decision; the others
#: are the standard session / language cookies LinkedIn sets once consent is
#: granted.  Values may need live tuning against the current LinkedIn consent
#: format, but the names/domains are what gate the redirect.
_LINKEDIN_CONSENT_COOKIES: tuple[SetCookieParam, ...] = (
    {
        "name": "li_gc",
        "value": (
            "MTI3NjI2ODU5NzpBV1NGTkVvcmcycE1zdE9rWmN6MGpFR2hYYnJyd2RtV05p"
            ":QUNQRVJRTFRmQnV2SnRGRmNxRTFP"
        ),
        "domain": ".linkedin.com",
        "path": "/",
    },
    {
        "name": "OptanonConsent",
        "value": (
            "isIABGlobal=false&datestamp=Fri+Jan+01+2026+00%3A00%3A00+GMT%2B0000"
            "+%28Coordinated+Universal+Time%29&version=6.32.0"
        ),
        "domain": ".linkedin.com",
        "path": "/",
    },
    {
        "name": "lang",
        "value": "v=2&lang=en-us",
        "domain": ".linkedin.com",
        "path": "/",
    },
    {
        "name": "UserMatchHistory",
        "value": "AQIA-consent-granted",
        "domain": ".linkedin.com",
        "path": "/",
    },
    {
        "name": "bcookie",
        "value": "v=2&consent-granted",
        "domain": ".linkedin.com",
        "path": "/",
    },
    {
        "name": "lidc",
        "value": "b=consent-granted",
        "domain": ".linkedin.com",
        "path": "/",
    },
)


def _is_consent_redirect(url: str) -> bool:
    """Whether ``url`` is a full-page consent / cookie-policy redirect target.

    LinkedIn hard-redirects the whole page to a legal wall — a fresh US/guest
    session to ``www.linkedin.com/legal/user-agreement``, the EU one to
    ``fr.linkedin.com/legal/cookie-policy`` — when the session carries no
    consent cookie; detecting this (after navigation, before credential fill)
    lets fill-credentials establish consent and re-navigate to the login form
    instead of clean-failing against the wrong page.
    """
    return bool(_CONSENT_REDIRECT_PATTERN.search(url or ""))


def _validate_url(url: str) -> str:
    scheme = urlparse(url).scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise UnsupportedUrlError(
            f"scheme {scheme!r} is not allowed (allowed: {sorted(_ALLOWED_SCHEMES)})"
        )
    return url


def _is_auth_frame(frame: Any, main_url: str) -> bool:
    """Whether a child frame is safe to bind into for login-field detection.

    A frame is eligible only when it is same-origin with the top-level page
    and is not an obvious cookie-consent / legal / privacy wall.  Cross-origin
    frames (e.g. ``fr.linkedin.com/legal/cookie-policy`` nested under the main
    LinkedIn page) and consent-management iframes are excluded so credentials
    are never typed into an unintended frame.
    """
    url = frame.url or ""
    if not url or url == "about:blank":
        return False
    parsed = urlparse(url)
    main = urlparse(main_url)
    same_origin = (
        (parsed.scheme or "").lower() == (main.scheme or "").lower()
        and (parsed.hostname or "").lower() == (main.hostname or "").lower()
        and parsed.port == main.port
    )
    if not same_origin:
        return False
    return not (
        _CONSENT_FRAME_PATTERN.search(parsed.hostname or "")
        or _CONSENT_FRAME_PATTERN.search(parsed.path)
    )


def _login_frames(page: Page) -> list[Any]:
    """Frames searched for login fields, top-level frame first.

    The main frame always leads; child frames are consulted only when they are
    same-origin auth content (consent/cookie/legal walls and cross-origin
    frames are excluded).
    """
    main = page.main_frame
    frames = [main]
    frames.extend(
        frame for frame in page.frames[1:] if _is_auth_frame(frame, main.url or "")
    )
    return frames


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
    try:
        await locator.click(timeout=_CLICK_TIMEOUT_MS)
    except PlaywrightTimeoutError as exc:
        target = _describe_target(
            selector=request.selector, role=request.role, name=request.name
        )
        raise SelectorNotFoundError(f"{target} matched no element") from exc
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

    Never runs against a *full-page* consent redirect: an accept-click there
    lands on yet another consent page (a fresh ``lipi`` token) rather than the
    login form.  fill-credentials recovers from that top-level redirect before
    calling this, so the click is only ever aimed at an in-page interstitial
    layered over the real login form.
    """
    if _is_consent_redirect(page.main_frame.url or ""):
        return
    for role in ("button", "link"):
        locator = page.get_by_role(cast(Any, role), name=_CONSENT_BUTTON_NAME)
        try:
            if await locator.count() == 0:
                continue
            await locator.first.click(timeout=_CONSENT_DISMISS_TIMEOUT_MS)
            return
        except Exception:
            continue


async def _establish_consent(page: Page) -> None:
    """Persist the LinkedIn consent cookies in the browser context.

    Cookies are set at the *context* level so they survive the subsequent
    re-navigation to the login form within the same session (Playwright keeps
    cookies on the :class:`BrowserContext`, not on the page).
    """
    await page.context.add_cookies(list(_LINKEDIN_CONSENT_COOKIES))


async def _recover_consent_redirect(page: Page, *, login_url: str | None) -> None:
    """Recover from a full-page consent redirect before attempting a fill.

    When the top-level frame has been redirected to a consent / cookie-policy
    wall (LinkedIn's ``fr.linkedin.com/legal/cookie-policy``), the login form
    is gone.  Establish consent in the context and re-navigate to the intended
    login URL; the now-present consent cookies keep LinkedIn from re-firing the
    redirect, so the login form renders in the main frame.  A best-effort
    no-op when the page is not on a consent redirect or no login URL is known.
    """
    if not _is_consent_redirect(page.main_frame.url or ""):
        return
    await _establish_consent(page)
    if login_url:
        await page.goto(_validate_url(login_url), wait_until="load")


async def _fill_login_field(
    page: Page, locator: Locator, value: str, *, timeout_ms: int
) -> None:
    """Fill a single login field within a bounded timeout.

    Raises :class:`LoginFieldNotFoundError` (mapped to a clean 4xx) instead of
    letting the Playwright :class:`TimeoutError` bubble up as a 500 when the
    locator never appears.
    """
    try:
        await locator.fill(value, timeout=timeout_ms)
    except PlaywrightTimeoutError as exc:
        raise LoginFieldNotFoundError(
            f"login field {locator} not found on current page"
        ) from exc


def _username_candidates(scope: Any, client_selector: str) -> list[Locator]:
    """Prioritized candidate locators for the username / email input.

    Order: the caller-supplied CSS selector, the classic LinkedIn member-login
    and guest sign-in ids, ``type=email``, then structural attributes
    (``name`` / ``autocomplete``), and finally ARIA role + accessible-name
    matching — so detection does not rely on hard-coded CSS ids alone.
    ``scope`` is the frame (top-level or child) being searched.
    """
    aria_role = cast(Any, "textbox")
    return [
        scope.locator(client_selector),
        scope.locator("#username"),
        scope.locator("#session_key"),
        scope.locator("#email"),
        scope.locator("#login"),
        scope.locator("input[type='email']"),
        scope.locator("input[autocomplete='username']"),
        scope.locator("input[autocomplete='email']"),
        scope.locator("input[name*='user' i]"),
        scope.locator("input[name*='mail' i]"),
        scope.locator("input[name*='login' i]"),
        scope.get_by_role(aria_role, name=_USERNAME_NAME),
    ]


def _password_candidates(scope: Any, client_selector: str) -> list[Locator]:
    """Prioritized candidate locators for the password input.

    Order: the caller-supplied CSS selector, the classic LinkedIn member-login
    and guest sign-in ids, ``type=password``, then structural attributes
    (``name`` / ``autocomplete``), and finally ARIA role + accessible-name
    matching — so detection does not rely on hard-coded CSS ids alone.
    ``scope`` is the frame (top-level or child) being searched.
    """
    aria_role = cast(Any, "textbox")
    return [
        scope.locator(client_selector),
        scope.locator("#password"),
        scope.locator("#session_password"),
        scope.locator("#passwd"),
        scope.locator("input[type='password']"),
        scope.locator("input[autocomplete='current-password']"),
        scope.locator("input[name*='pass' i]"),
        scope.get_by_role(aria_role, name=_PASSWORD_NAME),
    ]


async def _fill_first_existing(
    page: Page,
    candidates: list[Locator],
    value: str,
    *,
    field_label: str,
    timeout_ms: int,
) -> None:
    """Fill the first candidate that exists on the current page.

    Falls through the prioritized candidate list so field location uses
    ARIA/accessible attributes plus the known-id fallbacks rather than one
    hard-coded selector.  Raises :class:`LoginFieldNotFoundError` (clean 4xx)
    when no candidate matches, keeping the previous fill-credentials clean
    failure behavior.
    """
    first_selector = candidates[0]
    for candidate in candidates:
        if await candidate.count():
            await _fill_login_field(page, candidate, value, timeout_ms=timeout_ms)
            return
    raise LoginFieldNotFoundError(
        f"login {field_label} field {first_selector} not found on current page"
    )


async def _fill_across_frames(
    page: Page,
    candidates: Callable[[Any], list[Locator]],
    value: str,
    *,
    field_label: str,
    timeout_ms: int,
) -> None:
    """Fill a login field, scoping detection to the top-level frame.

    The main frame is always searched first.  A child frame is consulted only
    when the main frame holds no candidate login field AND the child frame is
    same-origin auth content (consent/cookie/legal walls and cross-origin
    frames are excluded via :func:`_login_frames`).  This keeps credentials
    bound to the visible top-level login form and never typed into an
    unintended cookie-policy iframe.  Raises :class:`LoginFieldNotFoundError`
    (clean 4xx) when no eligible frame contains the field.
    """
    first_desc: str | None = None
    for frame in _login_frames(page):
        for candidate in candidates(frame):
            if first_desc is None:
                first_desc = str(candidate)
            if await candidate.count():
                await _fill_login_field(frame, candidate, value, timeout_ms=timeout_ms)
                return
    raise LoginFieldNotFoundError(
        f"login {field_label} field {first_desc or ''} not found on current page"
    )


async def fill_credentials(
    page: Page,
    request: FillCredentialsRequest,
    vault: VaultClient,
    *,
    login_url: str | None = None,
    timeout_ms: int = _CREDENTIAL_FILL_TIMEOUT_MS,
) -> str:
    """Fetch a scoped vault entry and fill the username / password fields.

    The secret is typed directly into the browser field and is never returned.
    This only fills the form — it does NOT submit, preserving the human
    submit-gate (``/submit`` remains the sole submit path).

    Before filling, LinkedIn-style *full-page* consent redirects are detected
    and recovered: when the top-level frame has been hard-redirected to a
    cookie-policy wall, the required consent cookies are set in the browser
    context and the page is re-navigated to the intended ``login_url`` so the
    real login form renders.  A best-effort dismissal of in-page consent
    interstitials follows (gated so it never runs against the top-level
    consent redirect itself).

    Each field is located via a prioritized candidate list — the caller's CSS
    selector first, then known login ids, input types, name/autocomplete
    attributes and ARIA role + accessible-name matching — rather than a single
    hard-coded id, so classic member-login, guest sign-in and locale variants
    all bind when their fields are present.  Detection is scoped to the
    top-level frame by default: nested cookie-consent / legal iframes are
    excluded, and a same-origin child frame is only consulted when the main
    frame holds no candidate login field.  Each candidate is located within a
    short, bounded ``timeout_ms`` (not the 30s global default); a still-absent
    field raises :class:`LoginFieldNotFoundError` for a clean 4xx.
    """
    credential = await vault.get_credential(request.entry)
    # Recover if LinkedIn redirected the whole page to the consent wall before
    # the login form can be bound (otherwise we would clean-fail on the wall).
    await _recover_consent_redirect(page, login_url=login_url)
    await _dismiss_consent_walls(page)
    await _fill_across_frames(
        page,
        lambda scope: _username_candidates(scope, request.username_selector),
        credential.username,
        field_label="username",
        timeout_ms=timeout_ms,
    )
    await _fill_across_frames(
        page,
        lambda scope: _password_candidates(scope, request.password_selector),
        credential.password,
        field_label="password",
        timeout_ms=timeout_ms,
    )
    return page.url


async def wait(page: Page, request: WaitRequest) -> str:
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
