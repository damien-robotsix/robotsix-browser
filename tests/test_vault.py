"""Unit tests for the Vaultwarden credential client (no browser required)."""

from __future__ import annotations

import asyncio
from urllib.parse import parse_qs

import httpx
import pytest

from robotsix_browser.config import Settings
from robotsix_browser.vault import (
    EntryNotFoundError,
    EntryOutOfScopeError,
    VaultClient,
    VaultCredential,
    VaultError,
    VaultNotConfiguredError,
    VaultUpstreamError,
    _extract_credential,
)

_SECRET = "top-secret-value"  # noqa: S105 - test fixture value
_COLLECTION = "col-123"


def _item(*, collection_ids: list[str], username: str, password: str) -> dict:
    return {
        "id": "entry-1",
        "collectionIds": collection_ids,
        "login": {"username": username, "password": password},
    }


def test_extract_credential_in_scope() -> None:
    cred = _extract_credential(
        _item(collection_ids=[_COLLECTION], username="user", password=_SECRET),
        _COLLECTION,
    )
    assert cred == VaultCredential(username="user", password=_SECRET)


def test_extract_credential_out_of_scope_fails_cleanly() -> None:
    with pytest.raises(EntryOutOfScopeError) as exc:
        _extract_credential(
            _item(collection_ids=["other"], username="user", password=_SECRET),
            _COLLECTION,
        )
    assert _SECRET not in str(exc.value)


def test_extract_credential_missing_login() -> None:
    with pytest.raises(VaultError):
        _extract_credential(
            {"id": "x", "collectionIds": [_COLLECTION], "login": {}}, _COLLECTION
        )


def test_credential_repr_redacts_password() -> None:
    cred = VaultCredential(username="user", password=_SECRET)
    assert _SECRET not in repr(cred)
    assert _SECRET not in str(cred)
    assert "<redacted>" in repr(cred)


def test_is_configured_requires_all_fields() -> None:
    unconfigured = VaultClient.from_settings(Settings())
    assert unconfigured.is_configured is False

    configured = VaultClient.from_settings(
        Settings(
            bw_server_url="https://vault.example",
            bw_client_id="user.abc",
            bw_client_secret="shh",
            bw_collection_id=_COLLECTION,
        )
    )
    assert configured.is_configured is True


def test_get_credential_unconfigured_raises() -> None:
    client = VaultClient.from_settings(Settings())
    with pytest.raises(VaultNotConfiguredError):
        asyncio.run(client.get_credential("anything"))


# ---------------------------------------------------------------------------
# API-based flow tests (mocked httpx transport)
# ---------------------------------------------------------------------------


def _make_client() -> VaultClient:
    return VaultClient(
        server_url="https://vault.example",
        client_id="user.abc",
        client_secret="shh",
        collection_id=_COLLECTION,
    )


def test_get_credential_by_id() -> None:
    """Token + direct item-by-id fetch succeeds."""
    item_payload = _item(
        collection_ids=[_COLLECTION], username="alice", password=_SECRET
    )

    def _handler(request: httpx.Request) -> httpx.Response:
        if "/identity/connect/token" in str(request.url):
            return httpx.Response(200, json={"access_token": "tok-123"})
        if str(request.url).endswith(f"/api/items/{item_payload['id']}"):
            return httpx.Response(200, json=item_payload)
        return httpx.Response(404)

    async def _run() -> VaultCredential:
        client = VaultClient(
            server_url="https://vault.example",
            client_id="user.abc",
            client_secret="shh",
            collection_id=_COLLECTION,
            transport=httpx.MockTransport(_handler),
        )
        return await client.get_credential("entry-1")

    cred = asyncio.run(_run())
    assert cred.username == "alice"
    assert cred.password == _SECRET


def test_get_credential_by_name_fallback() -> None:
    """When id lookup returns 404, falls back to listing and name search."""
    item_payload = _item(collection_ids=[_COLLECTION], username="bob", password=_SECRET)
    item_payload["name"] = "my-entry"

    def _handler(request: httpx.Request) -> httpx.Response:
        if "/identity/connect/token" in str(request.url):
            return httpx.Response(200, json={"access_token": "tok-456"})
        if "/api/items/" in str(request.url):
            return httpx.Response(404)
        if str(request.url).endswith("/api/items"):
            return httpx.Response(200, json={"data": [item_payload]})
        return httpx.Response(404)

    async def _run() -> VaultCredential:
        client = VaultClient(
            server_url="https://vault.example",
            client_id="user.abc",
            client_secret="shh",
            collection_id=_COLLECTION,
            transport=httpx.MockTransport(_handler),
        )
        return await client.get_credential("my-entry")

    cred = asyncio.run(_run())
    assert cred.username == "bob"
    assert cred.password == _SECRET


def test_get_credential_not_found() -> None:
    """Entry not found by id or name raises EntryNotFoundError."""

    def _handler(request: httpx.Request) -> httpx.Response:
        if "/identity/connect/token" in str(request.url):
            return httpx.Response(200, json={"access_token": "tok-789"})
        if "/api/items/" in str(request.url):
            return httpx.Response(404)
        if str(request.url).endswith("/api/items"):
            return httpx.Response(200, json={"data": []})
        return httpx.Response(404)

    async def _run() -> None:
        client = VaultClient(
            server_url="https://vault.example",
            client_id="user.abc",
            client_secret="shh",
            collection_id=_COLLECTION,
            transport=httpx.MockTransport(_handler),
        )
        await client.get_credential("missing-entry")

    with pytest.raises(EntryNotFoundError):
        asyncio.run(_run())


def test_get_credential_token_failure() -> None:
    """Token endpoint returning non-200 raises VaultUpstreamError with a safe reason."""

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized")

    async def _run() -> None:
        client = VaultClient(
            server_url="https://vault.example",
            client_id="user.abc",
            client_secret="shh",
            collection_id=_COLLECTION,
            transport=httpx.MockTransport(_handler),
        )
        await client.get_credential("entry-1")

    with pytest.raises(VaultUpstreamError, match="token request failed") as exc:
        asyncio.run(_run())
    assert exc.value.status_code == 401
    assert exc.value.reason == "unauthorized"


def test_get_credential_item_failure_redacts_secrets() -> None:
    """A failing item fetch surfaces a sanitized upstream reason (no secrets)."""

    def _handler(request: httpx.Request) -> httpx.Response:
        if "/identity/connect/token" in str(request.url):
            return httpx.Response(200, json={"access_token": "tok-123"})
        return httpx.Response(503, text="Service Unavailable tok-123 shh")

    async def _run() -> None:
        client = VaultClient(
            server_url="https://vault.example",
            client_id="user.abc",
            client_secret="shh",
            collection_id=_COLLECTION,
            transport=httpx.MockTransport(_handler),
        )
        await client.get_credential("entry-1")

    with pytest.raises(VaultUpstreamError, match="item lookup failed") as exc:
        asyncio.run(_run())
    assert exc.value.status_code == 503
    assert "Service Unavailable" in exc.value.reason
    # The client secret and bearer token must never leak into the exception.
    assert "shh" not in str(exc.value)
    assert "tok-123" not in str(exc.value)


def test_token_request_includes_device_params() -> None:
    """The client-credentials token request carries the device parameters."""
    item_payload = _item(
        collection_ids=[_COLLECTION], username="alice", password=_SECRET
    )
    captured: dict[str, str] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        if "/identity/connect/token" in str(request.url):
            captured.update(
                {
                    key: values[0]
                    for key, values in parse_qs(request.content.decode()).items()
                }
            )
            return httpx.Response(200, json={"access_token": "tok-123"})
        if str(request.url).endswith(f"/api/items/{item_payload['id']}"):
            return httpx.Response(200, json=item_payload)
        return httpx.Response(404)

    async def _run() -> None:
        client = VaultClient(
            server_url="https://vault.example",
            client_id="user.abc",
            client_secret="shh",
            collection_id=_COLLECTION,
            device_type=0,
            device_identifier="robotsix-browser-service",
            device_name="robotsix-browser",
            transport=httpx.MockTransport(_handler),
        )
        await client.get_credential("entry-1")

    asyncio.run(_run())

    assert captured["grant_type"] == "client_credentials"
    assert captured["client_id"] == "user.abc"
    assert captured["client_secret"] == "shh"
    assert captured["scope"] == "api"
    assert captured["deviceType"] == "0"
    assert captured["deviceIdentifier"] == "robotsix-browser-service"
    assert captured["deviceName"] == "robotsix-browser"


def test_list_collections_returns_only_metadata() -> None:
    """Collections are listed as id/name metadata — never extra fields."""

    def _handler(request: httpx.Request) -> httpx.Response:
        if "/identity/connect/token" in str(request.url):
            return httpx.Response(200, json={"access_token": "tok-123"})
        if str(request.url).endswith("/api/collections"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "8cd27f39-60dc-4dae-b9ef-00bba3f75f2d",
                            "name": "provisioned",
                            "externalId": "x",
                            "organizationId": "org-1",
                        },
                        {"id": "col-2", "name": "second"},
                    ]
                },
            )
        return httpx.Response(404)

    async def _run() -> list[dict[str, str]]:
        client = VaultClient(
            server_url="https://vault.example",
            client_id="user.abc",
            client_secret="shh",
            collection_id=_COLLECTION,
            transport=httpx.MockTransport(_handler),
        )
        return await client.list_collections()

    result = asyncio.run(_run())
    assert result == [
        {"id": "8cd27f39-60dc-4dae-b9ef-00bba3f75f2d", "name": "provisioned"},
        {"id": "col-2", "name": "second"},
    ]


def test_list_items_returns_only_id_name_no_secrets() -> None:
    """Items expose only id/name; passwords, notes and custom fields are dropped."""
    password = "hunter2"  # noqa: S105 - test fixture
    notes = "super secret secure-note contents"
    hidden_field = "custom-hidden-value"

    def _handler(request: httpx.Request) -> httpx.Response:
        if "/identity/connect/token" in str(request.url):
            return httpx.Response(200, json={"access_token": "tok-123"})
        if str(request.url).endswith("/api/items"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "item-1",
                            "name": "linkedin.com",
                            "collectionIds": [_COLLECTION],
                            "login": {
                                "username": "alice@example.com",
                                "password": password,
                            },
                            "secureNote": {"type": 2, "notes": notes},
                            "fields": [
                                {
                                    "name": "totp",
                                    "value": hidden_field,
                                    "hidden": True,
                                }
                            ],
                        }
                    ]
                },
            )
        return httpx.Response(404)

    async def _run() -> list[dict[str, str]]:
        client = VaultClient(
            server_url="https://vault.example",
            client_id="user.abc",
            client_secret="shh",
            collection_id=_COLLECTION,
            transport=httpx.MockTransport(_handler),
        )
        return await client.list_items()

    result = asyncio.run(_run())
    assert result == [{"id": "item-1", "name": "linkedin.com"}]
    joined = " ".join(str(v) for item in result for v in item.values())
    assert password not in joined
    assert notes not in joined
    assert hidden_field not in joined


def test_list_collections_unconfigured_raises() -> None:
    client = VaultClient.from_settings(Settings())
    with pytest.raises(VaultNotConfiguredError):
        asyncio.run(client.list_collections())
    with pytest.raises(VaultNotConfiguredError):
        asyncio.run(client.list_items())


def test_list_collections_upstream_failure_surfaces_safe_reason() -> None:
    """A failing collection fetch surfaces a safe upstream status/reason."""

    def _handler(request: httpx.Request) -> httpx.Response:
        if "/identity/connect/token" in str(request.url):
            return httpx.Response(200, json={"access_token": "tok-123"})
        return httpx.Response(401, text="unauthorized tok-123 shh")

    async def _run() -> None:
        client = VaultClient(
            server_url="https://vault.example",
            client_id="user.abc",
            client_secret="shh",
            collection_id=_COLLECTION,
            transport=httpx.MockTransport(_handler),
        )
        await client.list_collections()

    with pytest.raises(VaultUpstreamError, match="collection list failed") as exc:
        asyncio.run(_run())
    assert exc.value.status_code == 401
    assert "unauthorized" in exc.value.reason
    assert "shh" not in str(exc.value)
    assert "tok-123" not in str(exc.value)


def test_list_items_upstream_failure_surfaces_safe_reason() -> None:
    """A failing item fetch surfaces a safe upstream status/reason."""

    def _handler(request: httpx.Request) -> httpx.Response:
        if "/identity/connect/token" in str(request.url):
            return httpx.Response(200, json={"access_token": "tok-123"})
        return httpx.Response(403, text="forbidden tok-123 shh")

    async def _run() -> None:
        client = VaultClient(
            server_url="https://vault.example",
            client_id="user.abc",
            client_secret="shh",
            collection_id=_COLLECTION,
            transport=httpx.MockTransport(_handler),
        )
        await client.list_items()

    with pytest.raises(VaultUpstreamError, match="item list failed") as exc:
        asyncio.run(_run())
    assert exc.value.status_code == 403
    assert "forbidden" in exc.value.reason
    assert "shh" not in str(exc.value)
    assert "tok-123" not in str(exc.value)
