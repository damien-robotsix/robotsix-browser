"""Live end-to-end verification for the LinkedIn vault credential fill.

This module provides *repeatable evidence* for the epic-level acceptance
criterion that ``POST /sessions/{id}/fill-credentials`` for the ``linkedin.com``
entry succeeds end-to-end: the scoped Vaultwarden credential is retrieved and
typed into the REAL LinkedIn login form **without submitting**.

Unlike the rest of the browser-automation suite, the tests here deliberately
drive the actual ``https://www.linkedin.com/login`` page (never a synthetic
``data:`` form).  They are therefore the only tests that can confirm the
consent-redirect recovery: the consent cookies in
``operations._LINKEDIN_CONSENT_COOKIES`` must keep LinkedIn from hard-redirecting
a fresh session to ``fr.linkedin.com/legal/cookie-policy`` once established, so
fill-credentials lands back on the real login form.  These tests are live: they
need a headless Chromium AND a network route to ``linkedin.com``.

Two tests, two gates:

* :func:`test_linkedin_fill_credentials_end_to_end` requires a live Vaultwarden
  configuration (loaded via :func:`get_settings`) scoped to the target
  collection.  When the config is absent, incomplete, or mis-scoped the test is
  **skipped** rather than failed, so the normal (offline) suite is unaffected.
* :func:`test_linkedin_consent_recovery_on_real_site` is the fast tuning loop
  for the consent cookies: it needs no live credential at all (a fake vault is
  injected solely to drive the recovery path) and is skipped when Chromium or
  the network route to ``linkedin.com`` is unavailable.

Each test builds its own FastAPI app / :class:`TestClient` — it does not touch
the shared, fully-mocked ``client`` fixture — so nothing here can bleed into
other tests.

Security: no password, secret custom-field value, client secret, or
``Authorization`` header is ever read back, printed, logged, or asserted on.
Only the (non-secret) username field is read back to prove injection ran; the
password is filled in the same call but is never surfaced over HTTP.

Live-validation caveat: this suite can only *prove* the recovery holds when run
on a network-enabled host.  The current ``li_gc`` / ``OptanonConsent`` values in
``operations._LINKEDIN_CONSENT_COOKIES`` are structurally plausible but were not
verifiable from a network-isolated sandbox (no Chromium, no route to
``linkedin.com``).  A human must run these tests against the live site and tune
the cookie values until :func:`test_linkedin_consent_recovery_on_real_site`
passes — a failing URL here means LinkedIn still hard-redirects to the consent
wall, i.e. the values need live tuning.
"""

from __future__ import annotations

import socket
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient

from robotsix_browser.app import create_app, get_vault
from robotsix_browser.config import Settings, get_settings
from robotsix_browser.operations import _is_consent_redirect
from robotsix_browser.vault import VaultClient, VaultCredential

#: The Vaultwarden collection the LinkedIn entry must live in (see ticket).
_TARGET_COLLECTION_ID = "8cd27f39-60dc-4dae-b9ef-00bba3f75f2d"
#: The vault entry to retrieve and inject.
_TARGET_ENTRY = "linkedin.com"
#: The REAL login form the credential is injected into.  A fresh session may be
#: hard-redirected to the cookie-policy wall; fill-credentials must recover and
#: land back here.
_LINKEDIN_LOGIN_URL = "https://www.linkedin.com/login"
#: Selectors for the classic LinkedIn member-login form (also the first
#: candidates in operations' prioritized locator list).
_USERNAME_SELECTOR = "#username"
_PASSWORD_SELECTOR = "#password"


class _FakeLinkedInVault:
    """In-memory vault used only to drive fill-credentials' recovery path.

    The consent-recovery test needs no real credential — it only needs
    ``fill-credentials`` to run far enough to trigger
    ``operations._recover_consent_redirect`` and re-navigate to the real login
    form after consent is established.  The fake username is injected exactly
    the way a real credential would be; the fake password is never read back.
    """

    async def get_credential(self, entry: str) -> VaultCredential:
        return VaultCredential(
            username="consent-check@example.com", password="consent-check-pw"
        )


def _live_settings() -> Settings | None:
    """Return live :class:`Settings` iff a usable Vaultwarden config is present.

    Returns ``None`` (→ skip) when the config file is missing/invalid, the
    Vaultwarden client is not fully configured, or the scoped key is not
    provisioned for the target collection.
    """
    try:
        settings = get_settings()
    except Exception:  # pragma: no cover - depends on deployment config
        return None
    if not VaultClient.from_settings(settings).is_configured:
        return None
    if settings.bw_collection_id != _TARGET_COLLECTION_ID:
        return None
    return settings


def _linkedin_reachable() -> bool:
    """Whether the live LinkedIn host resolves from this environment.

    Keeps the vault-free live test from failing the offline suite when the
    sandbox / CI has no network route to ``linkedin.com`` — same philosophy as
    the ``browser_available`` fixture (skip, never fail, when the environment
    cannot host the live check).
    """
    try:
        socket.getaddrinfo("www.linkedin.com", 443)
    except OSError:
        return False
    return True


def _assert_back_on_login_form(url: str) -> None:
    """Fail unless ``url`` is back on a LinkedIn login page, not the consent wall.

    This is the core anti-regression the synthetic ``data:`` page could not
    exercise: after ``_recover_consent_redirect`` established the consent
    cookies and re-navigated, LinkedIn must NOT hard-redirect back to the
    cookie-policy wall.  A failure here means the ``li_gc`` / ``OptanonConsent``
    values in ``operations._LINKEDIN_CONSENT_COOKIES`` need live tuning against
    the current LinkedIn consent format.
    """
    assert not _is_consent_redirect(url), (
        "LinkedIn still hard-redirects to the consent wall after the consent "
        "cookies were set — the li_gc/OptanonConsent values in "
        f"operations._LINKEDIN_CONSENT_COOKIES need live tuning (page on {url!r})"
    )
    host = urlparse(url).hostname or ""
    assert host.endswith("linkedin.com"), (
        "fill-credentials did not return to a LinkedIn page after consent "
        f"recovery (landed on {url!r})"
    )


def test_linkedin_fill_credentials_end_to_end(browser_available: None) -> None:
    """Retrieve the live ``linkedin.com`` credential and fill the REAL login form.

    Navigates to the actual ``https://www.linkedin.com/login`` page (never a
    synthetic ``data:`` form).  A fresh session may be hard-redirected to
    ``fr.linkedin.com/legal/cookie-policy``; fill-credentials must recover by
    establishing the consent cookies and re-navigating to the login form.
    Asserts the two things the previous synthetic-form test could not prove:

    * the final page is NOT the consent wall (the recovery held), and
    * the username field of the real login form was populated.

    Verifies the epic's final acceptance criterion: the browser path types the
    credential into the login form *without submitting*.  On a vault-retrieval
    failure the test fails with the upstream status code and safe reason text
    surfaced by the endpoint (never a secret).
    """
    settings = _live_settings()
    if settings is None:
        pytest.skip(
            "live Vaultwarden config for collection "
            f"{_TARGET_COLLECTION_ID} not available"
        )

    app = create_app(settings)
    with TestClient(app) as client:
        session_id = client.post("/sessions", json={}).json()["session_id"]

        nav = client.post(
            f"/sessions/{session_id}/navigate",
            json={"url": _LINKEDIN_LOGIN_URL},
        )
        assert nav.status_code == 200
        # The session may now sit on the consent wall — that is exactly what
        # fill-credentials must recover from before binding the login form.

        response = client.post(
            f"/sessions/{session_id}/fill-credentials",
            json={
                "entry": _TARGET_ENTRY,
                "username_selector": _USERNAME_SELECTOR,
                "password_selector": _PASSWORD_SELECTOR,
            },
        )

        # On vault failure the endpoint returns the upstream status + a safe,
        # secret-free reason.  Surface exactly that; never a raw credential.
        if response.status_code != 200:
            detail = response.json().get("detail", "<no detail>")
            pytest.fail(
                "vault retrieval for "
                f"{_TARGET_ENTRY!r} failed: HTTP {response.status_code}: {detail}"
            )

        # The fill call returns the current page URL.  It must be back on the
        # real login form — NOT the consent wall — proving the consent-redirect
        # recovery held and the form was filled WITHOUT being submitted.
        _assert_back_on_login_form(response.json()["url"])

        # The (non-secret) username must be present in the live form, proving
        # the credential was retrieved and injected.  The password is filled in
        # the same call but is deliberately never read back, so the secret is
        # never surfaced over HTTP.
        user = client.get(
            f"/sessions/{session_id}/value",
            params={"selector": _USERNAME_SELECTOR},
        )
        assert user.status_code == 200
        assert user.json()["value"], (
            "username field was not populated on the real login form"
        )


def test_linkedin_consent_recovery_on_real_site(browser_available: None) -> None:
    """Drive the consent-redirect recovery against the real LinkedIn login.

    A *vault-free* live check that exercises the actual recovery path the
    synthetic ``data:`` page could not: navigate a fresh (cookie-less) context
    to ``https://www.linkedin.com/login``, then call fill-credentials so
    ``_recover_consent_redirect`` establishes the consent cookies and
    re-navigates to the real login form.  Fails if the final page is still the
    consent wall or the login form did not render.

    No live Vaultwarden credential is needed (a fake vault is injected just to
    drive the path), so this is the fast loop for tuning the ``li_gc`` /
    ``OptanonConsent`` values against the live wall.  Skipped when Chromium or
    the network route to ``linkedin.com`` is unavailable.
    """
    if not _linkedin_reachable():
        pytest.skip("linkedin.com unreachable from this environment (no network?)")

    app = create_app(Settings(headless=True))
    app.dependency_overrides[get_vault] = lambda: _FakeLinkedInVault()
    with TestClient(app) as client:
        session_id = client.post("/sessions", json={}).json()["session_id"]

        nav = client.post(
            f"/sessions/{session_id}/navigate",
            json={"url": _LINKEDIN_LOGIN_URL},
        )
        assert nav.status_code == 200

        response = client.post(
            f"/sessions/{session_id}/fill-credentials",
            json={
                "entry": _TARGET_ENTRY,
                "username_selector": _USERNAME_SELECTOR,
                "password_selector": _PASSWORD_SELECTOR,
            },
        )
        assert response.status_code == 200, response.json()

        # The recovery must have held: back on the real login form, not the wall.
        _assert_back_on_login_form(response.json()["url"])

        user = client.get(
            f"/sessions/{session_id}/value",
            params={"selector": _USERNAME_SELECTOR},
        )
        assert user.status_code == 200
        assert user.json()["value"], "login form did not render after consent recovery"
