"""HTTP-level tests.

The health/404 tests run without a browser.  The smoke and upload tests drive
real headless Chromium and are skipped (via the ``browser_available`` fixture)
when no browser binary is installed.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from robotsix_browser.app import get_manager, get_vault
from robotsix_browser.vault import VaultNotConfiguredError, VaultUpstreamError

_FORM_HTML = (
    "<form><input id='name'>"
    "<select id='color'><option value='r'>Red</option>"
    "<option value='g'>Green</option></select>"
    "<input id='file' type='file'></form>"
)

_LOGIN_HTML = "<form><input id='user'><input id='pass' type='password'></form>"


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
    assert set(doc["safety"]["read_only"]) == {"state", "value"}
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
