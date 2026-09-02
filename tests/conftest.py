"""Shared pytest fixtures."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from robotsix_browser.app import create_app, get_filehub, get_vault
from robotsix_browser.config import Settings
from robotsix_browser.filehub import FileHubFile
from robotsix_browser.vault import EntryOutOfScopeError, VaultCredential


@pytest.fixture(scope="session")
def browser_available() -> None:
    """Skip the test unless a headless Chromium can actually be launched."""
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            browser.close()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"Chromium not available: {exc}")


class FakeFileHub:
    """In-memory stand-in for :class:`FileHubClient` used in tests."""

    def __init__(self, files: dict[str, FileHubFile]) -> None:
        self._files = files

    async def fetch(self, file_id: str) -> FileHubFile:
        return self._files[file_id]


#: The secret used by :class:`FakeVault`; tests assert it never leaks.
FAKE_SECRET = "s3cr3t-p@ssw0rd-never-leak"  # noqa: S105 - test fixture value


class FakeVault:
    """In-memory stand-in for :class:`VaultClient` used in tests.

    Only entries in ``entries`` are in scope; anything else raises
    :class:`EntryOutOfScopeError`, mirroring the single-collection scoping of
    the real client.
    """

    def __init__(self, entries: dict[str, VaultCredential]) -> None:
        self._entries = entries

    async def get_credential(self, entry: str) -> VaultCredential:
        try:
            return self._entries[entry]
        except KeyError:
            raise EntryOutOfScopeError(
                f"entry {entry!r} is not in the provisioned collection"
            ) from None

    async def list_collections(self) -> list[dict[str, str]]:
        """Read-only metadata for the single provisioned collection."""
        return [{"id": "col-123", "name": "test-collection"}]

    async def list_items(self) -> list[dict[str, str]]:
        """Read-only metadata (id/name) for the in-scope entries."""
        return [
            {"id": f"item-{i}", "name": name}
            for i, name in enumerate(self._entries)
        ]


@pytest.fixture
def fake_secret() -> str:
    """The secret injected by :class:`FakeVault`; tests assert it never leaks."""
    return FAKE_SECRET


@pytest.fixture
def fake_file() -> FileHubFile:
    return FileHubFile(
        name="upload.txt",
        content=b"hello from file-hub",
        content_type="text/plain",
    )


@pytest.fixture
def client(fake_file: FileHubFile) -> Iterator[TestClient]:
    app = create_app(Settings(headless=True))
    app.dependency_overrides[get_filehub] = lambda: FakeFileHub({"file-123": fake_file})
    app.dependency_overrides[get_vault] = lambda: FakeVault(
        {"ovh-portal": VaultCredential(username="svc-ovh", password=FAKE_SECRET)}
    )
    with TestClient(app) as test_client:
        yield test_client
