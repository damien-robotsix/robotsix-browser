"""HTTP-level tests.

The health/404 tests run without a browser.  The smoke and upload tests drive
real headless Chromium and are skipped (via the ``browser_available`` fixture)
when no browser binary is installed.
"""

from __future__ import annotations

import logging
import time
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from robotsix_browser.app import get_manager, get_vault
from robotsix_browser.vault import VaultNotConfiguredError, VaultUpstreamError

#: Upper bound (seconds) for a selector-miss 404 response.  The /value and
#: /click miss probes use a 5s bounded locator timeout (far below the 30s
#: global action default), so a clean miss response must arrive well under
#: this cap — one that regressed to the unbounded/global timeout would blow
#: past it.
_MISS_RESPONSE_MAX_S = 25

_FORM_HTML = (
    "<form><input id='name'>"
    "<select id='color'><option value='r'>Red</option>"
    "<option value='g'>Green</option></select>"
    "<input id='file' type='file'></form>"
)

_LOGIN_HTML = "<form><input id='user'><input id='pass' type='password'></form>"

#: Classic LinkedIn member-login form: ``#username`` / ``#password``.
_CLASSIC_LOGIN_HTML = (
    "<form><input id='username' type='text' name='session_key'>"
    "<input id='password' type='password' name='session_password'></form>"
)

#: LinkedIn guest sign-in form: ``#session_key`` / ``#session_password``.
_GUEST_LOGIN_HTML = (
    "<form><input id='session_key' type='text' name='session_key'>"
    "<input id='session_password' type='password' name='session_password'></form>"
)

#: Locale/alternate login shape: field ids differ from the classic LinkedIn
#: ones, so detection binds through the generic fallback tiers (``name*='mail'``
#: for the username, ``type='password'`` for the password) instead of the
#: hard-coded ``#username`` / ``#session_key`` ids.
_LOCALE_LOGIN_HTML = (
    "<form><input id='benutzername' type='text' name='email'>"
    "<input id='passwort' type='password' name='passwort'></form>"
)

#: Login form whose username field is discoverable only via its ARIA
#: accessible name (``aria-label='Username'``) — no structural attribute
#: matches any fallback tier, so the ARIA role+name locator must bind it.
_ARIA_LOGIN_HTML = (
    "<form><input id='u' type='text' aria-label='Username'>"
    "<input id='p' type='password' aria-label='Password'></form>"
)


def _data_url(html: str) -> str:
    return "data:text/html," + quote(html)


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_unknown_session_returns_404(client: TestClient) -> None:
    response = client.post(
        "/sessions/does-not-exist/navigate", json={"url": "data:text/html,x"}
    )
    assert response.status_code == 404


def test_chat_skill_document(client: TestClient) -> None:
    doc = client.get("/chat-skill").json()
    assert doc["component"] == "robotsix-browser"
    assert doc["base"]["port"] == 8000
    # Every state-mutating action is confirmation-gated; reads are not.
    assert "submit" in doc["safety"]["confirmation_gated"]
    read_only = set(doc["safety"]["read_only"])
    assert {"state", "value"} <= read_only
    # The read-only vault diagnostics are advertised and expose no secrets.
    assert "vault_diagnostics.items" in read_only
    assert doc["vault_diagnostics"]["items"]["path"] == "/vault/items"
    # The programmatic auth path is documented for the chat agent.
    assert doc["auth"]["internal"]["network"] == "central-deploy-proxy"
    bypass = doc["auth"]["public_edge"]["programmatic_bypass"]
    assert bypass["header"].lower().startswith("authorization: bearer")


def test_smoke_fill_and_read_back(browser_available: None, client: TestClient) -> None:
    session_id = client.post("/sessions", json={}).json()["session_id"]

    nav = client.post(
        f"/sessions/{session_id}/navigate", json={"url": _data_url(_FORM_HTML)}
    )
    assert nav.status_code == 200

    typed = client.post(
        f"/sessions/{session_id}/type", json={"selector": "#name", "text": "Ada"}
    )
    assert typed.status_code == 200

    value = client.get(f"/sessions/{session_id}/value", params={"selector": "#name"})
    assert value.json() == {"selector": "#name", "value": "Ada"}


def test_value_missing_selector_returns_404(
    browser_available: None, client: TestClient
) -> None:
    session_id = client.post("/sessions", json={}).json()["session_id"]
    client.post(f"/sessions/{session_id}/navigate", json={"url": _data_url(_FORM_HTML)})

    started = time.monotonic()
    response = client.get(
        f"/sessions/{session_id}/value", params={"selector": "#does-not-exist"}
    )
    elapsed = time.monotonic() - started

    assert response.status_code == 404
    detail = response.json()["detail"]
    # The body names the selector and the no-match reason, no stack trace.
    assert "#does-not-exist" in detail
    assert "matched no element" in detail
    assert "Traceback" not in detail
    # The miss is probed within the bounded locator timeout (not the 30s
    # global action default), so the clean 404 arrives fast instead of
    # hanging the request.
    assert elapsed < _MISS_RESPONSE_MAX_S


def test_click_missing_selector_returns_404(
    browser_available: None, client: TestClient
) -> None:
    session_id = client.post("/sessions", json={}).json()["session_id"]
    client.post(f"/sessions/{session_id}/navigate", json={"url": _data_url(_FORM_HTML)})

    started = time.monotonic()
    response = client.post(
        f"/sessions/{session_id}/click", json={"selector": "#does-not-exist"}
    )
    elapsed = time.monotonic() - started

    assert response.status_code == 404
    detail = response.json()["detail"]
    # The body names the selector and the no-match reason, no stack trace.
    assert "#does-not-exist" in detail
    assert "matched no element" in detail
    assert "Traceback" not in detail
    # The miss is probed within the bounded locator timeout (not the 30s
    # global action default), so the clean 404 arrives fast instead of
    # hanging the request.
    assert elapsed < _MISS_RESPONSE_MAX_S


def test_click_missing_role_name_returns_404(
    browser_available: None, client: TestClient
) -> None:
    session_id = client.post("/sessions", json={}).json()["session_id"]
    client.post(f"/sessions/{session_id}/navigate", json={"url": _data_url(_FORM_HTML)})

    started = time.monotonic()
    response = client.post(
        f"/sessions/{session_id}/click",
        json={"role": "button", "name": "definitely-not-there"},
    )
    elapsed = time.monotonic() - started

    assert response.status_code == 404
    detail = response.json()["detail"]
    # The body names the role+name and the no-match reason, no stack trace.
    assert "button" in detail
    assert "definitely-not-there" in detail
    assert "matched no element" in detail
    assert "Traceback" not in detail
    # The role+name miss is probed within the bounded locator timeout (not
    # the 30s global action default), so the clean 404 arrives fast instead
    # of hanging the request.
    assert elapsed < _MISS_RESPONSE_MAX_S


def test_click_happy_path(browser_available: None, client: TestClient) -> None:
    session_id = client.post("/sessions", json={}).json()["session_id"]
    client.post(f"/sessions/{session_id}/navigate", json={"url": _data_url(_FORM_HTML)})

    response = client.post(f"/sessions/{session_id}/click", json={"selector": "#name"})
    assert response.status_code == 200


def test_state_returns_tree_and_screenshot(
    browser_available: None, client: TestClient
) -> None:
    session_id = client.post("/sessions", json={}).json()["session_id"]
    client.post(f"/sessions/{session_id}/navigate", json={"url": _data_url(_FORM_HTML)})

    state = client.get(f"/sessions/{session_id}/state").json()
    assert "screenshot_base64" in state
    assert state["screenshot_base64"]
    assert "accessibility_tree" in state


def test_select_option(browser_available: None, client: TestClient) -> None:
    session_id = client.post("/sessions", json={}).json()["session_id"]
    client.post(f"/sessions/{session_id}/navigate", json={"url": _data_url(_FORM_HTML)})

    client.post(
        f"/sessions/{session_id}/select", json={"selector": "#color", "value": "g"}
    )
    value = client.get(f"/sessions/{session_id}/value", params={"selector": "#color"})
    assert value.json()["value"] == "g"


def test_upload_from_file_hub(browser_available: None, client: TestClient) -> None:
    session_id = client.post("/sessions", json={}).json()["session_id"]
    client.post(f"/sessions/{session_id}/navigate", json={"url": _data_url(_FORM_HTML)})

    upload = client.post(
        f"/sessions/{session_id}/upload",
        json={"selector": "#file", "file_id": "file-123"},
    )
    assert upload.status_code == 200

    # A populated file input reports the attached filename via its value.
    value = client.get(f"/sessions/{session_id}/value", params={"selector": "#file"})
    assert value.json()["value"].endswith("upload.txt")


def test_fill_credentials_injects_without_leaking(
    browser_available: None, client: TestClient, fake_secret: str
) -> None:
    session_id = client.post("/sessions", json={}).json()["session_id"]
    client.post(
        f"/sessions/{session_id}/navigate", json={"url": _data_url(_LOGIN_HTML)}
    )

    response = client.post(
        f"/sessions/{session_id}/fill-credentials",
        json={
            "entry": "ovh-portal",
            "username_selector": "#user",
            "password_selector": "#pass",
        },
    )
    assert response.status_code == 200
    # The secret must never appear in the response body.
    assert fake_secret not in response.text

    # The username is filled in the live session (proving injection ran; the
    # password is filled in the same call but is deliberately not read back so
    # the secret is never surfaced over HTTP).
    user = client.get(f"/sessions/{session_id}/value", params={"selector": "#user"})
    assert user.json()["value"] == "svc-ovh"


def test_fill_credentials_out_of_scope_fails_cleanly(
    browser_available: None, client: TestClient, fake_secret: str
) -> None:
    session_id = client.post("/sessions", json={}).json()["session_id"]
    client.post(
        f"/sessions/{session_id}/navigate", json={"url": _data_url(_LOGIN_HTML)}
    )

    response = client.post(
        f"/sessions/{session_id}/fill-credentials",
        json={
            "entry": "not-in-my-collection",
            "username_selector": "#user",
            "password_selector": "#pass",
        },
    )
    assert response.status_code == 403
    assert fake_secret not in response.text


_NO_LOGIN_HTML = "<div id='wall'>Please verify you are human before continuing.</div>"

_CONSENT_WALL_HTML = (
    "<button onclick=\"document.getElementById('login').style.display='block'\">"
    "Accept &amp; continue</button>"
    "<form id='login' style='display:none'>"
    "<input id='user'><input id='pass' type='password'></form>"
)


def test_fill_credentials_missing_field_returns_clean_4xx(
    browser_available: None, fast_fill_client: TestClient, fake_secret: str
) -> None:
    """A page without the login field yields a clean 422 — not a 500."""
    session_id = fast_fill_client.post("/sessions", json={}).json()["session_id"]
    fast_fill_client.post(
        f"/sessions/{session_id}/navigate", json={"url": _data_url(_NO_LOGIN_HTML)}
    )

    response = fast_fill_client.post(
        f"/sessions/{session_id}/fill-credentials",
        json={
            "entry": "ovh-portal",
            "username_selector": "#user",
            "password_selector": "#pass",
        },
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "#user" in detail
    assert "not found" in detail
    # The clean-failure path must never surface the secret.
    assert fake_secret not in response.text


def test_fill_credentials_dismisses_consent_wall_then_fills(
    browser_available: None, fast_fill_client: TestClient, fake_secret: str
) -> None:
    """A consent interstitial is dismissed, revealing the form, which is filled."""
    session_id = fast_fill_client.post("/sessions", json={}).json()["session_id"]
    fast_fill_client.post(
        f"/sessions/{session_id}/navigate", json={"url": _data_url(_CONSENT_WALL_HTML)}
    )

    response = fast_fill_client.post(
        f"/sessions/{session_id}/fill-credentials",
        json={
            "entry": "ovh-portal",
            "username_selector": "#user",
            "password_selector": "#pass",
        },
    )
    assert response.status_code == 200
    assert fake_secret not in response.text

    user = fast_fill_client.get(
        f"/sessions/{session_id}/value", params={"selector": "#user"}
    )
    assert user.json()["value"] == "svc-ovh"


@pytest.mark.parametrize(
    ("html", "uid"),
    [
        (_CLASSIC_LOGIN_HTML, "#username"),
        (_GUEST_LOGIN_HTML, "#session_key"),
        (_LOCALE_LOGIN_HTML, "#benutzername"),
    ],
)
def test_fill_credentials_binds_classic_guest_and_locale_forms(
    browser_available: None,
    fast_fill_client: TestClient,
    fake_secret: str,
    html: str,
    uid: str,
) -> None:
    """The username/password fields are located via detection, not the literal
    caller-supplied selectors (which are intentionally absent), and filled.
    Covers the classic LinkedIn member-login, guest sign-in and a
    locale/alternate shape."""
    session_id = fast_fill_client.post("/sessions", json={}).json()["session_id"]
    fast_fill_client.post(
        f"/sessions/{session_id}/navigate", json={"url": _data_url(html)}
    )

    response = fast_fill_client.post(
        f"/sessions/{session_id}/fill-credentials",
        json={
            "entry": "ovh-portal",
            # Literal ids #user/#pass do not exist on these forms; detection
            # must bind the real fields via the fallback list.
            "username_selector": "#user",
            "password_selector": "#pass",
        },
    )
    assert response.status_code == 200
    assert fake_secret not in response.text

    user = fast_fill_client.get(
        f"/sessions/{session_id}/value", params={"selector": uid}
    )
    assert user.json()["value"] == "svc-ovh"


def test_fill_credentials_aria_role_locator_fills(
    browser_available: None, fast_fill_client: TestClient, fake_secret: str
) -> None:
    """The ARIA-role-aware locator binds a username reachable only via its
    accessible name (no id/name/type matches any structural fallback tier)
    and fills both credentials."""
    session_id = fast_fill_client.post("/sessions", json={}).json()["session_id"]
    fast_fill_client.post(
        f"/sessions/{session_id}/navigate", json={"url": _data_url(_ARIA_LOGIN_HTML)}
    )

    response = fast_fill_client.post(
        f"/sessions/{session_id}/fill-credentials",
        json={
            "entry": "ovh-portal",
            # The literal ids #user/#pass do not exist on this form and no
            # structural attribute matches; only the ARIA accessible name
            # ('Username') can bind the username field.
            "username_selector": "#user",
            "password_selector": "#pass",
        },
    )
    assert response.status_code == 200
    assert fake_secret not in response.text

    # 200 above means both fields were located and filled (any miss raises a
    # clean 422); the username is read back to prove the write landed.
    user = fast_fill_client.get(
        f"/sessions/{session_id}/value", params={"selector": "#u"}
    )
    assert user.json()["value"] == "svc-ovh"


def test_fill_credentials_upstream_failure_surfaces_safe_reason(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """A vault upstream failure yields a 502 with the status + safe reason,
    and logs the same information without leaking secrets."""

    class _FailingVault:
        async def get_credential(self, entry: str) -> None:
            raise VaultUpstreamError(
                502, "Bad Gateway: upstream vault down", "token request"
            )

    class _FakeManager:
        def get(self, session_id: str) -> SimpleNamespace:
            return SimpleNamespace(page=object())

    client.app.dependency_overrides[get_manager] = _FakeManager
    client.app.dependency_overrides[get_vault] = _FailingVault

    with caplog.at_level(logging.WARNING):
        response = client.post(
            "/sessions/s1/fill-credentials",
            json={
                "entry": "ovh-portal",
                "username_selector": "#user",
                "password_selector": "#pass",
            },
        )

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert "upstream HTTP 502" in detail
    assert "Bad Gateway: upstream vault down" in detail
    assert any(
        "upstream HTTP 502" in record.getMessage()
        and "Bad Gateway" in record.getMessage()
        for record in caplog.records
    )


def test_vault_collections_lists_only_metadata(client: TestClient) -> None:
    response = client.get("/vault/collections")
    assert response.status_code == 200
    body = response.json()
    assert "collections" in body
    assert any(
        c == {"id": "col-123", "name": "test-collection"} for c in body["collections"]
    )


def test_vault_items_lists_metadata_without_secrets(
    client: TestClient, fake_secret: str
) -> None:
    response = client.get("/vault/items")
    assert response.status_code == 200
    body = response.json()
    assert "items" in body
    names = {item["name"] for item in body["items"]}
    assert "ovh-portal" in names
    assert all(set(item) == {"id", "name"} for item in body["items"])
    # The password / secret must never appear in the response body.
    assert fake_secret not in response.text


def test_vault_collections_upstream_failure_surfaces_safe_reason(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """An upstream collection-list failure yields a 502 + safe status/reason."""

    class _FailingVault:
        async def list_collections(self) -> None:
            raise VaultUpstreamError(
                401, "unauthorized: bad credentials", "collection list"
            )

    client.app.dependency_overrides[get_vault] = _FailingVault

    with caplog.at_level(logging.WARNING):
        response = client.get("/vault/collections")

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert "upstream HTTP 401" in detail
    assert "unauthorized: bad credentials" in detail
    assert any(
        "upstream HTTP 401" in record.getMessage()
        and "bad credentials" in record.getMessage()
        for record in caplog.records
    )


def test_vault_items_not_configured_returns_503(client: TestClient) -> None:
    """An unconfigured vault returns 503 rather than a generic 502."""

    class _UnconfiguredVault:
        async def list_items(self) -> None:
            raise VaultNotConfiguredError(
                "Vaultwarden credential injection is not configured"
            )

    client.app.dependency_overrides[get_vault] = _UnconfiguredVault

    response = client.get("/vault/items")
    assert response.status_code == 503
    assert "not configured" in response.json()["detail"]
