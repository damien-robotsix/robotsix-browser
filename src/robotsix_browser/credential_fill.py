"""Site-specific credential-fill machinery.

This module isolates the LinkedIn / cookie-consent / login-form heuristics that
drive :func:`fill_credentials` — the bounded-timeout field detection, the
cookie-consent interstitial dismissal, and the LinkedIn full-page consent
redirect recovery.  Keeping them here, away from the generic thin Playwright
wrappers in :mod:`robotsix_browser.operations`, means the stable operations
layer stays small and these site-specific patterns (which need periodic live
tuning per the inline comments) stay easy to find and adjust.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any, cast
from urllib.parse import urlparse

from playwright._impl._api_structures import SetCookieParam
from playwright.async_api import Locator, Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from robotsix_browser.models import FillCredentialsRequest
from robotsix_browser.operations import _validate_url
from robotsix_browser.vault import VaultClient


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
#: *iframes* nested under the main page.  It targets the LinkedIn
#: ``/legal/cookie-policy`` whole-page redirect (and equivalents such as
#: ``/cookie-policy`` / ``/consent``) so fill-credentials can recover before
#: attempting to bind the (now absent) login form.
_CONSENT_REDIRECT_PATTERN = re.compile(
    r"/legal/cookie(?:-|_)policy(?=[/?#]|$)|/cookie(?:-|_)policy(?=[/?#]|$)|"
    r"/consent(?=[/?#]|$)",
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

    LinkedIn hard-redirects the whole page to
    ``fr.linkedin.com/legal/cookie-policy`` when the session carries no consent
    cookie; detecting this (after navigation, before credential fill) lets
    fill-credentials establish consent and re-navigate to the login form
    instead of clean-failing against the wrong page.
    """
    return bool(_CONSENT_REDIRECT_PATTERN.search(url or ""))


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
