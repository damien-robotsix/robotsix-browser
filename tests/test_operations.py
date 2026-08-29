"""Unit tests for browser-independent bits of the operations module."""

from __future__ import annotations

import pytest

from robotsix_browser.operations import UnsupportedUrlError, _validate_url


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "https://example.com/form",
        "data:text/html,x",
        "about:blank",
    ],
)
def test_validate_url_allows_supported_schemes(url: str) -> None:
    assert _validate_url(url) == url


@pytest.mark.parametrize(
    "url", ["file:///etc/passwd", "ftp://host/x", "javascript:alert(1)"]
)
def test_validate_url_rejects_disallowed_schemes(url: str) -> None:
    with pytest.raises(UnsupportedUrlError):
        _validate_url(url)
