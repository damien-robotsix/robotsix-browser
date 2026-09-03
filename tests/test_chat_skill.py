"""Drift guard for the chat-skill document (mirrors the CI "Config schema
sync" step).

``chat_skill()`` hand-duplicates the HTTP API surface defined by the FastAPI
routes in :mod:`robotsix_browser.app` and the ``Literal`` enum types in
:mod:`robotsix_browser.models`; it is a pure function.  These tests pin its
invariants so the hand-maintained document cannot drift from the real API
surface without a test failure:

* every structured ``(method, path)`` pair advertised in the skill matches
  ``app.routes`` and vice versa,
* the ``wait_until`` / ``state`` enum-value strings match
  ``get_args(WaitUntil)`` / ``get_args(LoadState)``,
* every endpoint entry documents a valid method/path, and
* every action is classified in the safety lists.
"""

from __future__ import annotations

import re
from typing import Any, get_args

from fastapi.routing import APIRoute

from robotsix_browser.app import create_app
from robotsix_browser.chat_skill import chat_skill
from robotsix_browser.config import Settings
from robotsix_browser.models import LoadState, WaitUntil

_METHODS = {"GET", "POST", "PUT", "DELETE", "PATCH"}

#: Session lifecycle entries mutate the session registry, never the page, so
#: they are exempt from the confirmation-gated / read-only classification.
_LIFECYCLE = {"sessions.open", "sessions.close"}

_PATH_PARAM = re.compile(r"\{[^}]+\}")

#: Routes without a structured (method, path) entry in the skill: ``/health``
#: and ``/chat-skill`` are documented in prose (the ``base.health`` string,
#: the ``endpoint`` key).  The framework OpenAPI/docs routes are plain
#: Starlette ``Route`` objects rather than ``APIRoute``, so ``_app_endpoints``
#: already skips them.
_NON_ADVERTISED_ROUTES = {
    ("GET", "/health"),
    ("GET", "/chat-skill"),
}


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


def _normalize_path(path: str) -> str:
    """Collapse route-param names so ``{id}`` and ``{session_id}`` compare equal."""
    return _PATH_PARAM.sub("{param}", path)


def _skill_endpoints() -> set[tuple[str, str]]:
    """The structured (method, path) pairs advertised in the skill doc."""

    def collect(node: Any) -> set[tuple[str, str]]:
        if not isinstance(node, dict):
            return set()
        endpoints: set[tuple[str, str]] = set()
        if "method" in node and "path" in node:
            endpoints.add((node["method"], node["path"]))
        for value in node.values():
            endpoints |= collect(value)
        return endpoints

    return collect(chat_skill())


def _app_endpoints() -> set[tuple[str, str]]:
    """The (method, path) pairs the FastAPI app actually routes."""
    app = create_app(Settings(headless=True))
    endpoints: set[tuple[str, str]] = set()
    for route in app.routes:
        if isinstance(route, APIRoute):
            for method in route.methods - {"HEAD"}:
                endpoints.add((method, _normalize_path(route.path)))
    return endpoints


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
    # FastAPI mounts its own documentation routes (/openapi.json, /docs,
    # /docs/oauth2-redirect, /redoc) alongside the app's endpoints.  They are
    # framework-internal and never part of the skill document, so exclude them
    # by path as well as by class: today they are plain starlette Routes
    # (already dropped by the isinstance filter), but excluding by path keeps
    # the comparison robust if a FastAPI upgrade turns them into APIRoutes.
    _FRAMEWORK_DOC_ROUTES = {
        "/openapi.json",
        "/docs",
        "/docs/oauth2-redirect",
        "/redoc",
    }

    doc = chat_skill()
    documented = {
        (entry["method"], entry["path"].replace("{id}", "{session_id}"))
        for _name, entry in _endpoint_entries(doc)
    }

    app = create_app(Settings(headless=True))
    app_routes: set[tuple[str, str]] = set()
    for route in app.routes:
        if (
            not isinstance(route, APIRoute)
            or route.path in {"/health", "/chat-skill"}
            or route.path in _FRAMEWORK_DOC_ROUTES
        ):
            continue
        methods = {m for m in route.methods if m in _METHODS}
        assert len(methods) == 1, f"route {route.path} has methods {methods!r}"
        app_routes.add((methods.pop(), route.path))

    assert documented == app_routes


def test_chat_skill_advertises_exactly_the_app_routes() -> None:
    advertised = {
        (method, _normalize_path(path)) for method, path in _skill_endpoints()
    }
    routed = _app_endpoints()

    # Sanity: the prose-documented routes we exclude really are routed, so
    # the exclusion list cannot silently grow stale.
    assert routed >= _NON_ADVERTISED_ROUTES

    assert advertised == routed - _NON_ADVERTISED_ROUTES


def test_enum_value_strings_match_models() -> None:
    """The advertised ``wait_until`` / ``state`` values equal the ``Literal``s."""
    doc = chat_skill()

    expected_wait_until = " | ".join(get_args(WaitUntil))
    expected_state = " | ".join(get_args(LoadState))

    assert (
        doc["actions"]["navigate"]["request"]["wait_until"]
        == f"{expected_wait_until} (default load)"
    )
    assert doc["actions"]["wait"]["request"]["state"] == f"{expected_state} | None"
