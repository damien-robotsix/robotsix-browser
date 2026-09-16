"""Validate the ``GET /chat-skill`` markdown document.

The chat-access standard (``robotsix-standards/docs/chat-access-standard.md``)
requires the endpoint to return ``text/markdown`` with a YAML frontmatter
header — a kebab-case ``name`` matching the component id and a one-sentence
``description`` — followed by a markdown body documenting the HTTP API and its
safety rules.
"""

from __future__ import annotations

import re
from typing import get_args

from fastapi.testclient import TestClient

from robotsix_browser import chat_skill
from robotsix_browser.models import LoadState, WaitUntil

COMPONENT_ID = "robotsix-browser"

#: HTTP ``method /path`` pairs advertised in code spans within the body.
_METHOD_PATH_RE = re.compile(r"`([A-Z]+)\s+(/[^`]+?)`")

#: Route path prefixes that are not part of the advertised chat API
#: (health probe, the skill document itself, and FastAPI's own docs routes).
_UNDOCUMENTED_PREFIXES = ("/health", "/chat-skill", "/openapi", "/docs", "/redoc")


def _split_frontmatter(doc: str) -> tuple[dict[str, str], str]:
    """Split a ``---``-fenced YAML frontmatter header from the markdown body."""
    assert doc.startswith("---\n"), "document must open with a YAML frontmatter fence"
    _, frontmatter, body = doc.split("---\n", 2)
    meta: dict[str, str] = {}
    for line in frontmatter.splitlines():
        if not line.strip():
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()
    return meta, body


def test_served_as_markdown(client: TestClient) -> None:
    response = client.get("/chat-skill")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert response.text.startswith("---\n")


def test_frontmatter_name_and_description() -> None:
    meta, _ = _split_frontmatter(chat_skill.chat_skill())
    # Kebab-case name matching the component id.
    assert meta["name"] == COMPONENT_ID
    assert re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", meta["name"])
    # One-sentence description on a single line.
    assert meta["description"]
    assert "\n" not in meta["description"]


def test_safety_section_preserves_classification() -> None:
    _, body = _split_frontmatter(chat_skill.chat_skill())
    assert "## Safety" in body
    # Only /submit submits; state is the read-only eye.
    assert "/sessions/{id}/submit" in body
    assert "/sessions/{id}/state" in body
    # The two classifications are preserved with their members.
    gated_line = next(
        line for line in body.splitlines() if "confirmation_gated" in line
    )
    for action in (
        "navigate",
        "click",
        "type",
        "select",
        "upload",
        "wait",
        "fill_credentials",
        "submit",
    ):
        assert action in gated_line or f"`{action}`" in body
    assert "read_only" in body
    for read in (
        "state",
        "value",
        "vault_diagnostics.collections",
        "vault_diagnostics.items",
    ):
        assert read in body


def test_enum_value_strings_match_models() -> None:
    _, body = _split_frontmatter(chat_skill.chat_skill())
    for value in get_args(WaitUntil):
        assert value in body
    for value in get_args(LoadState):
        assert value in body


def test_documented_endpoints_cover_app_routes(client: TestClient) -> None:
    """Every real API route (bar framework/health) is documented in the body."""
    _, body = _split_frontmatter(chat_skill.chat_skill())
    documented = {
        (method, path.replace("{id}", "{session_id}"))
        for method, path in _METHOD_PATH_RE.findall(body)
    }

    app = client.app
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if not path or not methods or path.startswith(_UNDOCUMENTED_PREFIXES):
            continue
        for method in methods:
            if method in {"HEAD", "OPTIONS"}:
                continue
            assert (method, path) in documented, f"{method} {path} not documented"
