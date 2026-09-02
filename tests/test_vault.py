"""Unit tests for the Vaultwarden credential client (no browser required)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from urllib.parse import parse_qs

import httpx
import pytest

import vault_fixtures as vf
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
_ORG = "org-1"

Handler = Callable[[httpx.Request], httpx.Response]


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

    # Missing the unlock secrets (email / master password) is still not enough.
    without_unlock = VaultClient.from_settings(
        Settings(
            bw_server_url="https://vault.example",
            bw_client_id="user.abc",
            bw_client_secret="shh",
            bw_collection_id=_COLLECTION,
        )
    )
    assert without_unlock.is_configured is False

    configured = VaultClient.from_settings(
        Settings(
            bw_server_url="https://vault.example",
            bw_client_id="user.abc",
            bw_client_secret="shh",
            bw_collection_id=_COLLECTION,
            bw_email="svc@example.com",
            bw_master_password="master",
        )
    )
    assert configured.is_configured is True


def test_get_credential_unconfigured_raises() -> None:
    client = VaultClient.from_settings(Settings())
    with pytest.raises(VaultNotConfiguredError):
        asyncio.run(client.get_credential("anything"))


# ---------------------------------------------------------------------------
# Sync-based flow tests (mocked httpx transport, real encryption round-trip)
# ---------------------------------------------------------------------------


def _client(handler: Handler, *, collection_id: str = _COLLECTION) -> VaultClient:
    return VaultClient(
        server_url="https://vault.example",
        client_id="user.abc",
        client_secret="shh",
        collection_id=collection_id,
        email=vf.EMAIL,
        master_password=vf.MASTER_PASSWORD,
        transport=httpx.MockTransport(handler),
    )


def _handler_for(
    sync: dict,
    *,
    token_status: int = 200,
    sync_status: int = 200,
    sync_text: str = "",
) -> Handler:
    def _handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/identity/connect/token" in url:
            if token_status != 200:
                return httpx.Response(token_status, text="unauthorized")
            return httpx.Response(200, json={"access_token": "tok-123"})
        if "/identity/accounts/prelogin" in url:
            return httpx.Response(200, json=vf.prelogin_body())
        if url.split("?")[0].endswith("/api/sync"):
            if sync_status != 200:
                return httpx.Response(sync_status, text=sync_text)
            return httpx.Response(200, json=sync)
        return httpx.Response(404)

    return _handler


def _sync_with_entry(**overrides: object) -> dict:
    entry = {
        "id": "entry-1",
        "name": "linkedin.com",
        "username": "alice@example.com",
        "password": _SECRET,
    }
    entry.update(overrides)
    return vf.build_sync(
        org_id=_ORG,
        collection_id=_COLLECTION,
        collection_name="Chat",
        entries=[entry],
    )


def test_get_credential_by_id_decrypts_login() -> None:
    """The sync payload is unlocked and the matched cipher login decrypted."""
    sync = _sync_with_entry()
    cred = asyncio.run(_client(_handler_for(sync)).get_credential("entry-1"))
    assert cred.username == "alice@example.com"
    assert cred.password == _SECRET


def test_get_credential_by_decrypted_name() -> None:
    """When the id does not match, the decrypted name is used to find the entry."""
    sync = _sync_with_entry()
    cred = asyncio.run(_client(_handler_for(sync)).get_credential("linkedin.com"))
    assert cred.username == "alice@example.com"
    assert cred.password == _SECRET


def test_get_credential_not_found() -> None:
    sync = _sync_with_entry()
    with pytest.raises(EntryNotFoundError):
        asyncio.run(_client(_handler_for(sync)).get_credential("missing"))


def test_get_credential_out_of_scope_rejected() -> None:
    """A cipher outside the provisioned collection is rejected before use."""
    sync = _sync_with_entry(collectionIds=["some-other-collection"])
    with pytest.raises(EntryOutOfScopeError):
        asyncio.run(_client(_handler_for(sync)).get_credential("entry-1"))


def test_get_credential_token_failure() -> None:
    """Token endpoint returning non-200 raises VaultUpstreamError with a safe reason."""
    handler = _handler_for(_sync_with_entry(), token_status=401)
    with pytest.raises(VaultUpstreamError, match="token request failed") as exc:
        asyncio.run(_client(handler).get_credential("entry-1"))
    assert exc.value.status_code == 401


def test_get_credential_sync_failure_redacts_secrets() -> None:
    """A failing sync fetch surfaces a sanitized upstream reason (no secrets)."""
    handler = _handler_for(
        _sync_with_entry(),
        sync_status=503,
        sync_text="Service Unavailable tok-123 shh",
    )
    with pytest.raises(VaultUpstreamError, match="vault sync failed") as exc:
        asyncio.run(_client(handler).get_credential("entry-1"))
    assert exc.value.status_code == 503
    assert "Service Unavailable" in exc.value.reason
    # The client secret and bearer token must never leak into the exception.
    assert "shh" not in str(exc.value)
    assert "tok-123" not in str(exc.value)


def test_token_request_includes_device_params() -> None:
    """The client-credentials token request carries the device parameters."""
    sync = _sync_with_entry()
    captured: dict[str, str] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/identity/connect/token" in url:
            captured.update(
                {
                    key: values[0]
                    for key, values in parse_qs(request.content.decode()).items()
                }
            )
            return httpx.Response(200, json={"access_token": "tok-123"})
        if "/identity/accounts/prelogin" in url:
            return httpx.Response(200, json=vf.prelogin_body())
        if url.split("?")[0].endswith("/api/sync"):
            return httpx.Response(200, json=sync)
        return httpx.Response(404)

    client = VaultClient(
        server_url="https://vault.example",
        client_id="user.abc",
        client_secret="shh",
        collection_id=_COLLECTION,
        email=vf.EMAIL,
        master_password=vf.MASTER_PASSWORD,
        device_type=0,
        device_identifier="robotsix-browser-service",
        device_name="robotsix-browser",
        transport=httpx.MockTransport(_handler),
    )
    asyncio.run(client.get_credential("entry-1"))

    assert captured["grant_type"] == "client_credentials"
    assert captured["client_id"] == "user.abc"
    assert captured["client_secret"] == "shh"
    assert captured["scope"] == "api"
    assert captured["deviceType"] == "0"
    assert captured["deviceIdentifier"] == "robotsix-browser-service"
    assert captured["deviceName"] == "robotsix-browser"


def test_list_collections_returns_decrypted_names() -> None:
    """Collections are listed with decrypted id/name — never encrypted blobs."""
    sync = _sync_with_entry()
    result = asyncio.run(_client(_handler_for(sync)).list_collections())
    assert result == [{"id": _COLLECTION, "name": "Chat"}]
    for item in result:
        assert not item["name"].startswith("2.")


def test_list_items_returns_only_id_name_no_secrets() -> None:
    """Items expose only id and decrypted name; secrets are never returned."""
    sync = _sync_with_entry()
    result = asyncio.run(_client(_handler_for(sync)).list_items())
    assert result == [{"id": "entry-1", "name": "linkedin.com"}]
    joined = " ".join(str(v) for item in result for v in item.values())
    assert _SECRET not in joined
    assert "alice@example.com" not in joined


def test_list_items_scoped_to_provisioned_collection() -> None:
    """Only entries in the provisioned collection are listed."""
    sync = vf.build_sync(
        org_id=_ORG,
        collection_id=_COLLECTION,
        collection_name="Chat",
        entries=[
            {
                "id": "in-scope",
                "name": "linkedin.com",
                "username": "alice",
                "password": _SECRET,
            },
            {
                "id": "out-of-scope",
                "name": "other.com",
                "username": "bob",
                "password": _SECRET,
                "collectionIds": ["some-other-collection"],
            },
        ],
    )
    result = asyncio.run(_client(_handler_for(sync)).list_items())
    assert result == [{"id": "in-scope", "name": "linkedin.com"}]


def test_list_collections_unconfigured_raises() -> None:
    client = VaultClient.from_settings(Settings())
    with pytest.raises(VaultNotConfiguredError):
        asyncio.run(client.list_collections())
    with pytest.raises(VaultNotConfiguredError):
        asyncio.run(client.list_items())


def test_list_items_upstream_failure_surfaces_safe_reason() -> None:
    """A failing sync fetch surfaces a safe upstream status/reason."""
    handler = _handler_for(
        _sync_with_entry(), sync_status=403, sync_text="forbidden tok-123 shh"
    )
    with pytest.raises(VaultUpstreamError, match="vault sync failed") as exc:
        asyncio.run(_client(handler).list_items())
    assert exc.value.status_code == 403
    assert "forbidden" in exc.value.reason
    assert "shh" not in str(exc.value)
    assert "tok-123" not in str(exc.value)
