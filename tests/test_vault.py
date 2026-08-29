"""Unit tests for the Vaultwarden credential client (no browser required)."""

from __future__ import annotations

import asyncio

import pytest

from robotsix_browser.config import Settings
from robotsix_browser.vault import (
    EntryOutOfScopeError,
    VaultClient,
    VaultCredential,
    VaultError,
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
            bw_unlock_secret="master",
            bw_collection_id=_COLLECTION,
        )
    )
    assert configured.is_configured is True


def test_get_credential_unconfigured_raises() -> None:
    from robotsix_browser.vault import VaultNotConfiguredError

    client = VaultClient.from_settings(Settings())
    with pytest.raises(VaultNotConfiguredError):
        asyncio.run(client.get_credential("anything"))
