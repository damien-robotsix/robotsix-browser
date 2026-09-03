"""Structural unit tests for the chat-agent skill document.

``chat_skill()`` is a pure function; these tests pin its invariants so the
hand-maintained document cannot drift from the real API surface without a test
failure.
"""

from __future__ import annotations

from typing import Any

from robotsix_browser.app import create_app
from robotsix_browser.chat_skill import chat_skill
from robotsix_browser.config import Settings

_METHODS = {"GET", "POST", "PUT", "DELETE", "PATCH"}

#: Session lifecycle entries mutate the session registry, never the page, so
#: they are exempt from the confirmation-gated / read-only classification.
_LIFECYCLE = {"sessions.open", "sessions.close"}


def _endpoint_entries(node: Any, name: str = "") -> list[tuple[str, dict[str, Any]]]:
    """Return ``(dotted_name, entry)`` for every endpoint dict in the doc."""
    entries: list[tuple[str, dict[str, Any]]] = []
    if isinstance(node, dict):
        if "method" in node and "path" in node:
            entries.append((name, node))
        for child_name, child in node.items():
            prefix = f"{name}.{child_name}" if name else str(child_name)
            entries.extend(_endpoint_entries(child, prefix))
    return entries


def test_every_endpoint_documents_method_and_path() -> None:
    doc = chat_skill()
    entries = _endpoint_entries(doc)

    assert entries, "the skill document must document at least one endpoint"
    for name, entry in entries:
        assert entry["method"] in _METHODS, f"{name}: bad method {entry['method']!r}"
        path = entry["path"]
        assert isinstance(path, str) and path.startswith("/"), (
            f"{name}: bad path {path!r}"
        )


def test_mutating_actions_are_confirmation_gated_and_reads_are_not() -> None:
    doc = chat_skill()
    gated = set(doc["safety"]["confirmation_gated"])
    read_only = set(doc["safety"]["read_only"])
    assert gated.isdisjoint(read_only)

    # The safety lists key actions by their bare name (``navigate`` not
    # ``actions.navigate``); vault diagnostics use their dotted path.
    def classification_key(name: str) -> str:
        return name.removeprefix("actions.")

    classified: set[str] = set()
    for name, entry in _endpoint_entries(doc):
        if name in _LIFECYCLE:
            continue
        key = classification_key(name)
        if entry["method"] == "GET":
            assert key in read_only, f"GET endpoint {name!r} must be read-only"
        else:
            assert key in gated, (
                f"mutating endpoint {name!r} must be confirmation-gated"
            )
        classified.add(key)

    # The safety lists classify every documented action and nothing else; a
    # stale gating name means a documented action was removed or renamed.
    assert gated | read_only == classified
    # The consequential submit action is gated; the read-only eyes are not.
    assert "submit" in gated
    assert {"state", "value"} <= read_only
    assert {"vault_diagnostics.collections", "vault_diagnostics.items"} <= read_only


def test_documented_endpoints_match_app_routes() -> None:
    from fastapi.routing import APIRoute

    doc = chat_skill()
    documented = {
        (entry["method"], entry["path"].replace("{id}", "{session_id}"))
        for _name, entry in _endpoint_entries(doc)
    }

    app = create_app(Settings(headless=True))
    app_routes: set[tuple[str, str]] = set()
    for route in app.routes:
        if not isinstance(route, APIRoute) or route.path in {"/health", "/chat-skill"}:
            continue
        methods = {m for m in route.methods if m in _METHODS}
        assert len(methods) == 1, f"route {route.path} has methods {methods!r}"
        app_routes.add((methods.pop(), route.path))

    assert documented == app_routes
