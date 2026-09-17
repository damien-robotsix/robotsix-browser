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


def _safety_list(body: str, label: str) -> set[str]:
    """Return the backtick-quoted action names under the ``**<label>**`` bullet
    of the Safety section."""
    lines = body.split("## Safety", 1)[1].splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(f"- **{label}**"))
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith("- **")),
        len(lines),
    )
    return set(re.findall(r"`([a-z_][a-z_.]*)`", "\n".join(lines[start:end])))


def _documented_actions(body: str) -> set[str]:
    """Return the action names advertised as ``- **name**`` bullets in the body.

    This is the action set the Safety classification must be exhaustive over.
    Session ``open`` is lifecycle setup — it creates a session but does not
    mutate a page — so it is not subject to the gate, and the vault-diagnostics
    bullets are advertised by short name but classified under
    ``vault_diagnostics.*``.
    """
    pre_safety = body.split("## Safety", 1)[0]
    names = {
        name.lower()
        for name in re.findall(r"^\- \*\*([A-Za-z_]+)\*\*", pre_safety, re.MULTILINE)
    }
    names.discard("open")
    names.discard("collections")
    names.discard("items")
    names.add("vault_diagnostics.collections")
    names.add("vault_diagnostics.items")
    return names


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
        "close",
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
    # The classification is exhaustive over the documented action set: every
    # documented mutating/read-only action is either confirmation-gated or
    # read-only.  Fail on unclassified actions so a new mutating action cannot
    # be added to the document without also being classified.
    gated = _safety_list(body, "confirmation_gated")
    read_only = _safety_list(body, "read_only")
    unclassified = _documented_actions(body) - gated - read_only
    assert not unclassified, f"unclassified actions: {sorted(unclassified)}"


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
