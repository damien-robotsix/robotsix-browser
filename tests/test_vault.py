"""Unit tests for the Vaultwarden credential client (no browser required)."""

from __future__ import annotations

import asyncio

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
    """Token endpoint returning non-200 raises VaultError."""

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

    with pytest.raises(VaultError, match="token request failed"):
        asyncio.run(_run())
