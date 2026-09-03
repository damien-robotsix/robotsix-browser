"""Chat-agent skill document describing the robotsix-browser API surface.

Served at ``GET /chat-skill``.  The document follows the same convention as
other robotsix component skills: a JSON object the chat agent reads to learn
how to drive this component's *real* automation API, including request and
response shapes.

Every state-mutating action is gated: the skill lists it under
``safety.confirmation_gated`` so the chat agent asks the operator before
performing it.  No endpoint other than ``POST /sessions/{id}/submit`` submits
a form, and ``/submit`` exists solely for an operator to gate the
consequential action.  ``GET /state`` is the read-only eye (accessibility tree
+ full-page screenshot) and is never gated.
"""

from __future__ import annotations

from typing import Any, get_args

from robotsix_browser.models import LoadState, WaitUntil

#: Enum members advertised in the skill, derived from the ``Literal`` types in
#: :mod:`robotsix_browser.models` (same drift-guard spirit as the CI "Config
#: schema sync" step): adding or renaming a member cannot leave this document
#: stale.
WAIT_UNTIL_VALUES = " | ".join(get_args(WaitUntil))
LOAD_STATE_VALUES = " | ".join(get_args(LoadState))


def chat_skill() -> dict[str, Any]:
    """Return the chat-agent skill document for robotsix-browser."""
    return {
        "component": "robotsix-browser",
        "endpoint": "/chat-skill",
        "description": (
            "Drive real headless-browser automation against web UIs: open an "
            "isolated session, navigate, read the page's accessibility tree "
            "and full-page screenshot (state), then click / type / select / "
            "wait as needed.  Each session is an isolated browser context "
            "with its own cookies and storage."
        ),
        "base": {
            "port": 8000,
            "health": 'GET /health -> {"status": "ok"}',
        },
        "auth": {
            "description": (
                "The service itself is unauthenticated; access is mediated by "
                "the deploy edge.  A chat agent has two ways to reach it."
            ),
            "internal": {
                "preferred": True,
                "network": "central-deploy-proxy",
                "base_url": "http://robotsix-browser:8000",
                "note": (
                    "Preferred path: on the shared central-deploy-proxy "
                    "network the service is reachable at its internal address "
                    "with no edge auth gate.  No token required."
                ),
            },
            "public_edge": {
                "base_url": "https://browser.deploy.robotsix.net",
                "gate": "tinyauth",
                "programmatic_bypass": {
                    "header": "Authorization: Bearer <token>",
                    "note": (
                        "The public edge sits behind a Tinyauth login gate. "
                        "Programmatic callers bypass the interactive login by "
                        "sending a Bearer token (the mobile-token bypass "
                        "route).  The token is provisioned to the chat agent "
                        "as a vaulted secret in its component config; it is "
                        "never embedded in this document or the repo."
                    ),
                },
            },
        },
        "sessions": {
            "open": {
                "method": "POST",
                "path": "/sessions",
                "request": {"session_id": "str | null (reopen an existing session)"},
                "response": {"session_id": "str"},
            },
            "close": {
                "method": "DELETE",
                "path": "/sessions/{id}",
                "description": "Close the session and its browser context.",
                "response": {"status": "closed"},
            },
        },
        "actions": {
            "navigate": {
                "method": "POST",
                "path": "/sessions/{id}/navigate",
                "request": {
                    "url": "str (http/https/data/about)",
                    "wait_until": f"{WAIT_UNTIL_VALUES} (default load)",
                },
                "response": {"status": "ok", "url": "str"},
            },
            "state": {
                "method": "GET",
                "path": "/sessions/{id}/state",
                "description": (
                    "Read-only eye: ARIA accessibility tree (YAML) + full-page "
                    "screenshot base64.  Use after every mutating action to "
                    "observe the result."
                ),
                "response": {
                    "url": "str",
                    "title": "str",
                    "accessibility_tree": "str (ARIA snapshot, YAML)",
                    "screenshot_base64": "str",
                },
            },
            "click": {
                "method": "POST",
                "path": "/sessions/{id}/click",
                "request": {
                    "selector": "str | None (CSS selector)",
                    "role": "str | None (ARIA role, e.g. button/link)",
                    "name": "str | None (ARIA accessible name)",
                },
                "response": {"status": "ok", "url": "str"},
            },
            "type": {
                "method": "POST",
                "path": "/sessions/{id}/type",
                "request": {"selector": "str (CSS)", "text": "str"},
                "response": {"status": "ok", "url": "str"},
            },
            "select": {
                "method": "POST",
                "path": "/sessions/{id}/select",
                "request": {
                    "selector": "str (CSS)",
                    "value": "str | None (option by value)",
                    "label": "str | None (option by visible label)",
                },
                "response": {"status": "ok", "url": "str"},
            },
            "upload": {
                "method": "POST",
                "path": "/sessions/{id}/upload",
                "request": {
                    "selector": "str (CSS, the <input type=file>)",
                    "file_id": "str (robotsix-file-hub id)",
                },
                "response": {"status": "ok", "url": "str"},
            },
            "wait": {
                "method": "POST",
                "path": "/sessions/{id}/wait",
                "request": {
                    "selector": "str | None (CSS)",
                    "state": f"{LOAD_STATE_VALUES} | None",
                    "timeout_ms": "int | None",
                },
                "response": {"status": "ok", "url": "str"},
            },
            "value": {
                "method": "GET",
                "path": "/sessions/{id}/value",
                "query": {"selector": "str (CSS)"},
                "response": {"selector": "str", "value": "str"},
            },
            "fill_credentials": {
                "method": "POST",
                "path": "/sessions/{id}/fill-credentials",
                "request": {
                    "entry": "str (vault entry name or id)",
                    "username_selector": "str (CSS)",
                    "password_selector": "str (CSS)",
                },
                "response": {"status": "ok", "url": "str"},
                "note": (
                    "Resolves the scoped Vaultwarden entry server-side: the "
                    "vault is enumerated via GET /api/sync and unlocked with "
                    "the configured master password to decrypt the entry. "
                    "username/password are typed directly into the fields and "
                    "are never returned, logged, or surfaced to the agent. "
                    "Only fills the form; it never submits."
                ),
            },
        },
        "vault_diagnostics": {
            "description": (
                "Read-only reachability checks for the scoped Vaultwarden "
                "collection.  Both return only decrypted ids/names — never "
                "usernames, passwords, or any secret value."
            ),
            "collections": {
                "method": "GET",
                "path": "/vault/collections",
                "response": {"collections": "[{id, name}] (name decrypted)"},
            },
            "items": {
                "method": "GET",
                "path": "/vault/items",
                "response": {"items": "[{id, name}] (name decrypted, no secrets)"},
            },
        },
        "submit": {
            "method": "POST",
            "path": "/sessions/{id}/submit",
            "request": {
                "selector": "str | None (CSS)",
                "role": "str (default button)",
                "name": "str | None (ARIA accessible name)",
                "wait_for_navigation": "bool (default true)",
            },
            "response": {"status": "ok", "url": "str"},
            "note": (
                "The ONLY endpoint that submits a form.  Always confirmation-"
                "gated: never call it automatically after filling."
            ),
        },
        "safety": {
            "policy": (
                "No endpoint other than /submit submits a form.  Read-only "
                "observation (state, value) is always allowed.  Every action "
                "that mutates the page is confirmation-gated: present the "
                "filled state to the operator and get explicit OK first."
            ),
            "confirmation_gated": [
                "navigate",
                "click",
                "type",
                "select",
                "upload",
                "wait",
                "fill_credentials",
                "submit",
            ],
            "read_only": [
                "state",
                "value",
                "vault_diagnostics.collections",
                "vault_diagnostics.items",
            ],
        },
    }
