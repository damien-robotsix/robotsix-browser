"""Live end-to-end verification for the LinkedIn vault credential fill.

This module provides *repeatable evidence* for the epic-level acceptance
criterion that ``POST /sessions/{id}/fill-credentials`` for the ``linkedin.com``
entry succeeds end-to-end: the scoped Vaultwarden credential is retrieved and
typed into a login form **without submitting**.

It is deliberately isolated from the rest of the browser-automation suite:

* It runs only when a *live* Vaultwarden configuration is present in the
  service ``config.json`` (loaded via :func:`get_settings`).  When the config
  is absent, incomplete, or scoped to a different collection than the target,
  the test is **skipped** rather than failed, so the normal (offline) suite is
  unaffected.
* It builds its own FastAPI app / :class:`TestClient` from the live settings —
  it does not touch the shared, fully-mocked ``client`` fixture — so nothing
  here can bleed into other tests.

Security: no password, secret custom-field value, client secret, or
``Authorization`` header is ever read back, printed, logged, or asserted on.
Only the (non-secret) username field is read back to prove injection ran; the
password is filled in the same call but is never surfaced over HTTP.
"""

from __future__ import annotations

from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from robotsix_browser.app import create_app
from robotsix_browser.config import Settings, get_settings
from robotsix_browser.vault import VaultClient

#: The Vaultwarden collection the LinkedIn entry must live in (see ticket).
_TARGET_COLLECTION_ID = "8cd27f39-60dc-4dae-b9ef-00bba3f75f2d"
#: The vault entry to retrieve and inject.
_TARGET_ENTRY = "linkedin.com"

#: A minimal login form the credential is injected into.  Using a ``data:`` URL
#: keeps the test hermetic (no external network for the *page*, only for vault).
_LOGIN_HTML = "<form><input id='user'><input id='pass' type='password'></form>"


def _data_url(html: str) -> str:
    return "data:text/html," + quote(html)


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


def test_linkedin_fill_credentials_end_to_end(browser_available: None) -> None:
    """Retrieve the live ``linkedin.com`` credential and fill it into a form.

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

        login_url = _data_url(_LOGIN_HTML)
        nav = client.post(
            f"/sessions/{session_id}/navigate", json={"url": login_url}
        )
        assert nav.status_code == 200

        response = client.post(
            f"/sessions/{session_id}/fill-credentials",
            json={
                "entry": _TARGET_ENTRY,
                "username_selector": "#user",
                "password_selector": "#pass",
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

        # The fill call returns the current page URL; it must be unchanged,
        # proving the form was filled WITHOUT being submitted (no navigation).
        assert response.json()["url"] == login_url

        # The (non-secret) username must be present in the live form, proving
        # the credential was retrieved and injected.  The password is filled in
        # the same call but is deliberately never read back, so the secret is
        # never surfaced over HTTP.
        user = client.get(
            f"/sessions/{session_id}/value", params={"selector": "#user"}
        )
        assert user.status_code == 200
        assert user.json()["value"], "username field was not populated"
