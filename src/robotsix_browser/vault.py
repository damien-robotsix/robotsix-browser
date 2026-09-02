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


class VaultClient:
    """Retrieves scoped credentials via the Bitwarden JSON API."""

    def __init__(
        self,
        *,
        server_url: str,
        client_id: str,
        client_secret: str,
        collection_id: str,
        device_type: int = 0,
        device_identifier: str = "robotsix-browser",
        device_name: str = "robotsix-browser",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._server_url = server_url.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._collection_id = collection_id
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
            device_type=settings.bw_device_type,
            device_identifier=settings.bw_device_identifier,
            device_name=settings.bw_device_name,
        )

    @property
    def is_configured(self) -> bool:
        """True only when every required secret / scope value is present."""
        return all(
            (
                self._server_url,
                self._client_id,
                self._client_secret,
                self._collection_id,
            )
        )

    async def get_credential(self, entry: str) -> VaultCredential:
        """Return the scoped :class:`VaultCredential` for ``entry``.

        ``entry`` may be a vault entry name or id.  The returned credential is
        guaranteed to belong to the provisioned collection.
        """
        if not self.is_configured:
            raise VaultNotConfiguredError(
                "Vaultwarden credential injection is not configured"
            )
        token = await self._get_token()
        item = await self._get_item(entry, token)
        return _extract_credential(item, self._collection_id)

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

    async def _get_item(self, entry: str, token: str) -> dict[str, Any]:
        """Fetch a vault item by id, falling back to name search."""
        headers = {"Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient(transport=self._transport) as client:
            # Try fetching by id first.
            resp = await client.get(
                f"{self._server_url}/api/items/{entry}",
                headers=headers,
            )
            if resp.status_code == 200:
                result: dict[str, Any] = resp.json()
                return result
            if resp.status_code != 404:
                raise VaultUpstreamError(
                    resp.status_code,
                    _sanitize_upstream_error_body(resp, (self._client_secret, token)),
                    "item lookup",
                )
            # Fall back to listing and searching by name.
            resp = await client.get(
                f"{self._server_url}/api/items",
                headers=headers,
            )
            if resp.status_code != 200:
                raise VaultUpstreamError(
                    resp.status_code,
                    _sanitize_upstream_error_body(resp, (self._client_secret, token)),
                    "item list",
                )
            items = resp.json().get("data", [])
            for item in items:
                if item.get("name") == entry:
                    found: dict[str, Any] = item
                    return found
            raise EntryNotFoundError("vault entry was not found")
