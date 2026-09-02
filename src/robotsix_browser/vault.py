"""Scoped Vaultwarden (Bitwarden API) credential retrieval.

This module fetches a *single* vault entry via the Bitwarden JSON API using
an **API-key service account scoped to one collection**, and hands the resulting
``username`` / ``password`` to the browser-side fill path.

Security model (why this file exists):

* The secret value is **never** returned in an HTTP response body, written to a
  log line, or surfaced to the chat agent.  :class:`VaultCredential` redacts its
  password in ``repr`` so a stray ``log.info(cred)`` / traceback cannot leak it.
* Authentication uses ``client_credentials`` (``client_id`` / ``client_secret``)
  provisioned as env vars — never in code, never in the repo.  The token is
  obtained via ``POST /identity/connect/token`` with
  ``grant_type=client_credentials``.
* Ciphers are enumerated via ``GET /api/sync`` (there is no ``/api/items``
  route).  The sync payload's fields are encrypted "EncString" blobs, so the
  vault is **unlocked** with the account master password (see :mod:`bwcrypto`)
  and the ``name`` / ``login`` fields are decrypted to plaintext before use.
  The access token from ``client_credentials`` alone cannot decrypt them.
* Access is **scoped to a single collection**.  An entry that is not a member of
  the provisioned collection is rejected with :class:`EntryOutOfScopeError`
  before any credential is extracted.
* **No TOTP / 2FA** is read or stored: only ``login.username`` / ``login.password``
  are extracted from the entry.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from robotsix_browser import bwcrypto
from robotsix_browser.bwcrypto import Keyring, VaultCryptoError
from robotsix_browser.config import Settings

#: Placeholder rendered wherever a password would otherwise be shown.
_REDACTED = "<redacted>"

#: Maximum length of an upstream error-body excerpt surfaced to callers.
_MAX_UPSTREAM_REASON_CHARS = 200


def _sanitize_upstream_error_body(
    resp: httpx.Response, secrets: tuple[str, ...]
) -> str:
    """Return a safe, truncated excerpt of an upstream error response body.

    Any occurrence of the provided secrets (client secret, access token) is
    redacted and whitespace is collapsed, so the excerpt can never leak
    credentials into logs or API error details.
    """
    try:
        body = resp.text
    except UnicodeDecodeError, ValueError:
        body = f"<non-text response of {len(resp.content)} bytes>"
    for secret in secrets:
        if secret:
            body = body.replace(secret, _REDACTED)
    body = " ".join(body.split())
    if len(body) > _MAX_UPSTREAM_REASON_CHARS:
        body = f"{body[:_MAX_UPSTREAM_REASON_CHARS]}..."
    return body or f"<empty error body (HTTP {resp.status_code})>"


class VaultError(RuntimeError):
    """Raised when a Bitwarden API operation fails."""


class VaultNotConfiguredError(VaultError):
    """Raised when a credential fetch is attempted without configuration."""


class EntryNotFoundError(VaultError):
    """Raised when the requested vault entry does not exist."""


class EntryOutOfScopeError(VaultError):
    """Raised when the entry is not in the service's provisioned collection."""


class VaultUpstreamError(VaultError):
    """Raised when the Vaultwarden server returns a non-success response.

    Carries the upstream HTTP status code and a sanitized excerpt of the
    error body so callers can surface a safe, diagnosable reason without
    exposing secrets.
    """

    def __init__(self, status_code: int, reason: str, operation: str) -> None:
        super().__init__(f"{operation} failed with HTTP status {status_code}: {reason}")
        self.status_code = status_code
        self.reason = reason


@dataclass(slots=True)
class VaultCredential:
    """A username / password pair fetched from the vault.

    ``repr`` (and therefore ``str``) redacts the password so the secret can
    never leak into a log line, traceback, or error message.
    """

    username: str
    password: str

    def __repr__(self) -> str:
        return f"VaultCredential(username={self.username!r}, password={_REDACTED})"


def _extract_credential(item: dict[str, Any], collection_id: str) -> VaultCredential:
    """Validate scope and pull the login fields out of a Bitwarden item payload.

    Raises :class:`EntryOutOfScopeError` when the item is not a member of the
    provisioned ``collection_id``, and :class:`VaultError` when it lacks a
    usable login.  Error messages never include the password.
    """
    collection_ids = item.get("collectionIds") or []
    if collection_id not in collection_ids:
        raise EntryOutOfScopeError(
            f"entry {item.get('id', '?')!r} is not in the provisioned collection"
        )
    login = item.get("login") or {}
    username = login.get("username")
    password = login.get("password")
    if not username or not password:
        raise VaultError("vault entry is missing a login username/password")
    return VaultCredential(username=username, password=password)


def _cipher_name(cipher: dict[str, Any], keyring: Keyring) -> str:
    """Decrypt a cipher's ``name`` to plaintext (never a secret value)."""
    try:
        return keyring.decrypt(cipher.get("name"), cipher.get("organizationId"))
    except VaultCryptoError as exc:
        raise VaultError("failed to decrypt vault entry name") from exc


def _decrypt_cipher(cipher: dict[str, Any], keyring: Keyring) -> dict[str, Any]:
    """Decrypt a sync-payload cipher into a plaintext item dict.

    Returns the minimal shape :func:`_extract_credential` expects — the id, the
    collection membership (used for scope validation) and the decrypted
    ``login`` username/password.  No other fields are decrypted or retained.
    """
    login = cipher.get("login") or {}
    org_id = cipher.get("organizationId")
    try:
        username = keyring.decrypt(login.get("username"), org_id)
        password = keyring.decrypt(login.get("password"), org_id)
    except VaultCryptoError as exc:
        raise VaultError("failed to decrypt vault entry login") from exc
    return {
        "id": cipher.get("id"),
        "collectionIds": cipher.get("collectionIds") or [],
        "login": {"username": username, "password": password},
    }


class VaultClient:
    """Retrieves scoped credentials via the Bitwarden JSON API."""

    def __init__(
        self,
        *,
        server_url: str,
        client_id: str,
        client_secret: str,
        collection_id: str,
        email: str = "",
        master_password: str = "",
        device_type: int = 0,
        device_identifier: str = "robotsix-browser",
        device_name: str = "robotsix-browser",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._server_url = server_url.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._collection_id = collection_id
        self._email = email
        self._master_password = master_password
        self._device_type = device_type
        self._device_identifier = device_identifier
        self._device_name = device_name
        self._access_token: str | None = None
        self._transport = transport

    @classmethod
    def from_settings(cls, settings: Settings) -> VaultClient:
        return cls(
            server_url=settings.bw_server_url,
            client_id=settings.bw_client_id.get_secret_value(),
            client_secret=settings.bw_client_secret.get_secret_value(),
            collection_id=settings.bw_collection_id,
            email=settings.bw_email,
            master_password=settings.bw_master_password.get_secret_value(),
            device_type=settings.bw_device_type,
            device_identifier=settings.bw_device_identifier,
            device_name=settings.bw_device_name,
        )

    @property
    def is_configured(self) -> bool:
        """True only when every required secret / scope value is present.

        Unlock/decrypt needs the account ``email`` (KDF salt) and
        ``master_password`` in addition to the API-key credentials, so both are
        required here — otherwise cipher fields could never be decrypted.
        """
        return all(
            (
                self._server_url,
                self._client_id,
                self._client_secret,
                self._collection_id,
                self._email,
                self._master_password,
            )
        )

    async def get_credential(self, entry: str) -> VaultCredential:
        """Return the scoped :class:`VaultCredential` for ``entry``.

        ``entry`` may be a vault entry name or id.  Ciphers are enumerated via
        ``GET /api/sync``, the vault is unlocked with the master password, and
        the matched entry's login is decrypted.  The returned credential is
        guaranteed to belong to the provisioned collection.
        """
        self._require_configured()
        sync, keyring = await self._load_vault()
        cipher = self._find_cipher(sync, entry, keyring)
        item = _decrypt_cipher(cipher, keyring)
        return _extract_credential(item, self._collection_id)

    async def list_collections(self) -> list[dict[str, str]]:
        """List read-only metadata (``id``, decrypted ``name``) of collections
        the scoped API key can see.

        Collection names are decrypted to plaintext; no secret value is ever
        included in the result.
        """
        self._require_configured()
        sync, keyring = await self._load_vault()
        result: list[dict[str, str]] = []
        for coll in sync.get("collections", []):
            if not coll.get("id"):
                continue
            try:
                name = keyring.decrypt(coll.get("name"), coll.get("organizationId"))
            except VaultCryptoError as exc:
                raise VaultError("failed to decrypt collection name") from exc
            result.append({"id": coll["id"], "name": name})
        return result

    async def list_items(self) -> list[dict[str, str]]:
        """List read-only metadata (``id``, decrypted ``name``) of items in the
        provisioned collection.

        Only ``id`` and the decrypted ``name`` are returned; passwords,
        secure-note contents, and custom field values are never touched or
        returned.
        """
        self._require_configured()
        sync, keyring = await self._load_vault()
        return [
            {"id": cipher["id"], "name": _cipher_name(cipher, keyring)}
            for cipher in sync.get("ciphers", [])
            if cipher.get("id") and self._in_scope(cipher)
        ]

    def _require_configured(self) -> None:
        if not self.is_configured:
            raise VaultNotConfiguredError(
                "Vaultwarden credential injection is not configured"
            )

    def _in_scope(self, cipher: dict[str, Any]) -> bool:
        """True when the cipher is a member of the provisioned collection."""
        return self._collection_id in (cipher.get("collectionIds") or [])

    def _find_cipher(
        self, sync: dict[str, Any], entry: str, keyring: Keyring
    ) -> dict[str, Any]:
        """Locate a cipher by id, falling back to a decrypted-name match."""
        ciphers: list[dict[str, Any]] = sync.get("ciphers", [])
        for cipher in ciphers:
            if cipher.get("id") == entry:
                return cipher
        for cipher in ciphers:
            if _cipher_name(cipher, keyring) == entry:
                return cipher
        raise EntryNotFoundError("vault entry was not found")

    async def _load_vault(self) -> tuple[dict[str, Any], Keyring]:
        """Authenticate, fetch the sync payload and unlock the vault."""
        token = await self._get_token()
        kdf = await self._get_prelogin()
        sync = await self._get_sync(token)
        profile = sync.get("profile") or {}
        try:
            keyring = bwcrypto.unlock(
                master_password=self._master_password,
                email=self._email,
                kdf=int(kdf.get("kdf", bwcrypto.KDF_PBKDF2)),
                iterations=int(kdf.get("kdfIterations", 0)),
                memory=kdf.get("kdfMemory"),
                parallelism=kdf.get("kdfParallelism"),
                protected_key=profile.get("key", ""),
                protected_private_key=profile.get("privateKey"),
                organizations=profile.get("organizations") or (),
            )
        except VaultCryptoError as exc:
            raise VaultError("failed to unlock vault") from exc
        return sync, keyring

    async def _get_prelogin(self) -> dict[str, Any]:
        """Fetch the account KDF parameters (unauthenticated)."""
        async with httpx.AsyncClient(transport=self._transport) as client:
            resp = await client.post(
                f"{self._server_url}/identity/accounts/prelogin",
                json={"email": self._email},
            )
        if resp.status_code != 200:
            raise VaultUpstreamError(
                resp.status_code,
                _sanitize_upstream_error_body(resp, (self._client_secret,)),
                "prelogin",
            )
        data: dict[str, Any] = resp.json()
        return data

    async def _get_sync(self, token: str) -> dict[str, Any]:
        """Fetch the full sync payload (profile + ciphers + collections)."""
        headers = {"Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient(transport=self._transport) as client:
            resp = await client.get(
                f"{self._server_url}/api/sync",
                params={"excludeDomains": "true"},
                headers=headers,
            )
        if resp.status_code != 200:
            raise VaultUpstreamError(
                resp.status_code,
                _sanitize_upstream_error_body(resp, (self._client_secret, token)),
                "vault sync",
            )
        data: dict[str, Any] = resp.json()
        return data

    async def _get_token(self) -> str:
        """Authenticate via ``client_credentials`` and return an access token."""
        async with httpx.AsyncClient(transport=self._transport) as client:
            resp = await client.post(
                f"{self._server_url}/identity/connect/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "scope": "api",
                    "deviceType": self._device_type,
                    "deviceIdentifier": self._device_identifier,
                    "deviceName": self._device_name,
                },
            )
            if resp.status_code != 200:
                raise VaultUpstreamError(
                    resp.status_code,
                    _sanitize_upstream_error_body(resp, (self._client_secret,)),
                    "token request",
                )
            token: str = resp.json()["access_token"]
            return token
