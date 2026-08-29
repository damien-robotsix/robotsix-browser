"""Scoped Vaultwarden (Bitwarden CLI) credential retrieval.

This module fetches a *single* vault entry via the Bitwarden CLI (``bw``) using
an **API-key service account scoped to one collection**, and hands the resulting
``username`` / ``password`` to the browser-side fill path.

Security model (why this file exists):

* The secret value is **never** returned in an HTTP response body, written to a
  log line, or surfaced to the chat agent.  :class:`VaultCredential` redacts its
  password in ``repr`` so a stray ``log.info(cred)`` / traceback cannot leak it.
* Authentication uses ``client_credentials`` (``client_id`` / ``client_secret``)
  provisioned as env vars — never in code, never in the repo.  The unlock secret
  is passed to ``bw`` via ``--passwordenv`` so it never appears on a process
  argv.
* Access is **scoped to a single collection**.  An entry that is not a member of
  the provisioned collection is rejected with :class:`EntryOutOfScopeError`
  before any credential is extracted.
* **No TOTP / 2FA** is read or stored: only ``login.username`` / ``login.password``
  are extracted from the entry.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from typing import Any

from robotsix_browser.config import Settings

#: Placeholder rendered wherever a password would otherwise be shown.
_REDACTED = "<redacted>"


class VaultError(RuntimeError):
    """Raised when a Bitwarden CLI operation fails."""


class VaultNotConfiguredError(VaultError):
    """Raised when a credential fetch is attempted without configuration."""


class EntryNotFoundError(VaultError):
    """Raised when the requested vault entry does not exist."""


class EntryOutOfScopeError(VaultError):
    """Raised when the entry is not in the service's provisioned collection."""


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
    """Validate scope and pull the login fields out of a ``bw`` item payload.

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
    """Retrieves scoped credentials via the Bitwarden CLI (``bw``)."""

    def __init__(
        self,
        *,
        server_url: str,
        client_id: str,
        client_secret: str,
        unlock_secret: str,
        collection_id: str,
        cli_path: str = "bw",
    ) -> None:
        self._server_url = server_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._unlock_secret = unlock_secret
        self._collection_id = collection_id
        self._cli_path = cli_path

    @classmethod
    def from_settings(cls, settings: Settings) -> VaultClient:
        return cls(
            server_url=settings.bw_server_url,
            client_id=settings.bw_client_id,
            client_secret=settings.bw_client_secret,
            unlock_secret=settings.bw_unlock_secret,
            collection_id=settings.bw_collection_id,
            cli_path=settings.bw_cli_path,
        )

    @property
    def is_configured(self) -> bool:
        """True only when every required secret / scope value is present."""
        return all(
            (
                self._server_url,
                self._client_id,
                self._client_secret,
                self._unlock_secret,
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
        session = await self._ensure_session()
        raw = await self._run_bw(
            "get", "item", entry, session=session, allow_not_found=True
        )
        try:
            item: dict[str, Any] = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise VaultError("could not parse the Bitwarden CLI item output") from exc
        return _extract_credential(item, self._collection_id)

    async def _ensure_session(self) -> str:
        """Configure the server, log in via API key, and unlock; return session."""
        await self._run_bw("config", "server", self._server_url)
        try:
            await self._run_bw(
                "login",
                "--apikey",
                env_extra={
                    "BW_CLIENTID": self._client_id,
                    "BW_CLIENTSECRET": self._client_secret,
                },
            )
        except VaultError as exc:
            # A pre-existing login is fine — the unlock below still succeeds.
            if "already logged in" not in str(exc).lower():
                raise
        return await self._run_bw(
            "unlock",
            "--passwordenv",
            "BW_PASSWORD",
            "--raw",
            env_extra={"BW_PASSWORD": self._unlock_secret},
        )

    async def _run_bw(
        self,
        *args: str,
        env_extra: dict[str, str] | None = None,
        session: str | None = None,
        allow_not_found: bool = False,
    ) -> str:
        """Run ``bw`` with the given args and return trimmed stdout.

        Secrets are passed via the environment (``env_extra`` / ``BW_SESSION``),
        never on argv.  ``stderr`` from ``bw`` never contains the password, so it
        is safe to surface in a :class:`VaultError` message.
        """
        env = os.environ.copy()
        if env_extra:
            env.update(env_extra)
        if session:
            env["BW_SESSION"] = session
        proc = await asyncio.create_subprocess_exec(
            self._cli_path,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            message = stderr.decode(errors="replace").strip()
            if allow_not_found and "not found" in message.lower():
                raise EntryNotFoundError("vault entry was not found")
            raise VaultError(f"bw {args[0]} failed: {message}")
        return stdout.decode(errors="replace").strip()
