"""Shared pytest fixtures."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from robotsix_browser.app import create_app, get_filehub
from robotsix_browser.config import Settings
from robotsix_browser.filehub import FileHubFile


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
    with TestClient(app) as test_client:
        yield test_client
