"""Minimal client for the robotsix-file-hub service.

The upload endpoint accepts a *file-hub file id*; this client resolves that id
into the raw bytes so they can be attached to a browser ``<input type=file>``.
No credential handling lives here — that is an explicitly out-of-scope
follow-on ticket.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

#: File ids are opaque tokens; restrict to a safe charset so the value can
#: never introduce path traversal or a scheme change when placed in the URL.
_FILE_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_FILENAME_RE = re.compile(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', re.IGNORECASE)


class FileHubError(RuntimeError):
    """Raised when the file-hub request fails."""


class InvalidFileIdError(ValueError):
    """Raised when a supplied file id contains unsafe characters."""


@dataclass(slots=True)
class FileHubFile:
    """A file fetched from the file-hub."""

    name: str
    content: bytes
    content_type: str


class FileHubClient:
    """Fetches files from the robotsix-file-hub by id."""

    def __init__(
        self, base_url: str, *, client: httpx.AsyncClient | None = None
    ) -> None:
        # Normalise to a trailing slash so ``join`` preserves any base path.
        normalised = base_url if base_url.endswith("/") else base_url + "/"
        self._base_url = httpx.URL(normalised)
        self._client = client

    def _build_url(self, file_id: str) -> httpx.URL:
        if not _FILE_ID_RE.match(file_id):
            raise InvalidFileIdError(file_id)
        # file_id is validated to a safe charset above; ``join`` performs
        # RFC-3986 resolution so no string concatenation reaches the URL.
        return self._base_url.join(f"files/{file_id}")

    async def fetch(self, file_id: str) -> FileHubFile:
        """Return the bytes for ``file_id`` from the file-hub."""
        url = self._build_url(file_id)
        client = self._client or httpx.AsyncClient()
        owns_client = self._client is None
        try:
            response = await client.get(url)
            response.raise_for_status()
            content = response.content
            headers = response.headers
        except httpx.HTTPError as exc:
            raise FileHubError(
                f"file-hub request failed for {file_id!r}: {exc}"
            ) from exc
        finally:
            if owns_client:
                await client.aclose()

        return FileHubFile(
            name=_filename_from_headers(headers, fallback=file_id),
            content=content,
            content_type=headers.get("content-type", "application/octet-stream"),
        )


def _filename_from_headers(headers: httpx.Headers, *, fallback: str) -> str:
    disposition = headers.get("content-disposition")
    if disposition:
        match = _FILENAME_RE.search(disposition)
        if match:
            return match.group(1).strip()
    return fallback
