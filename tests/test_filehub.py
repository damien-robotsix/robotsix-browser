"""Unit tests for the file-hub client (no browser required)."""

from __future__ import annotations

import httpx
import pytest

from robotsix_browser.filehub import FileHubClient, FileHubError, InvalidFileIdError


async def test_fetch_builds_url_and_parses_response() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(
            200,
            content=b"payload",
            headers={
                "content-type": "text/plain",
                "content-disposition": 'attachment; filename="doc.txt"',
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = FileHubClient("http://hub.test/api", client=http_client)
        result = await client.fetch("abc123")

    assert seen["url"] == "http://hub.test/api/files/abc123"
    assert result.content == b"payload"
    assert result.name == "doc.txt"
    assert result.content_type == "text/plain"


async def test_fetch_falls_back_to_file_id_for_name() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"data")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = FileHubClient("http://hub.test", client=http_client)
        result = await client.fetch("plain-id")

    assert result.name == "plain-id"
    assert result.content_type == "application/octet-stream"


@pytest.mark.parametrize("bad_id", ["../etc/passwd", "a/b", "with space", "x?y", ""])
async def test_invalid_file_id_rejected(bad_id: str) -> None:
    client = FileHubClient("http://hub.test")
    with pytest.raises(InvalidFileIdError):
        await client.fetch(bad_id)


async def test_http_error_wrapped() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = FileHubClient("http://hub.test", client=http_client)
        with pytest.raises(FileHubError):
            await client.fetch("missing")
