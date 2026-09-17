"""Chat-agent skill document describing the robotsix-browser API surface.

Served at ``GET /chat-skill`` as ``text/markdown`` with a YAML frontmatter
header, per ``robotsix-standards/docs/chat-access-standard.md`` and matching
the shape used by the other chat-accessible services (calendar, linkedin,
invest, auto-mail).  The frontmatter carries a kebab-case ``name`` matching the
component id and a one-sentence ``description``; the markdown body documents
what the service does, how to drive its HTTP API, and the safety rules.

Every state-mutating action is confirmation-gated: the safety section lists it
so the chat agent asks the operator before performing it.  No endpoint other
than ``POST /sessions/{id}/submit`` submits a form, and ``/submit`` exists
solely for an operator to gate the consequential action.  ``GET
/sessions/{id}/state`` is the read-only eye (accessibility tree + full-page
screenshot) and is never gated.
"""

from __future__ import annotations

from typing import get_args

from robotsix_browser.models import LoadState, WaitUntil

#: Enum members advertised in the skill, derived from the ``Literal`` types in
#: :mod:`robotsix_browser.models` (same drift-guard spirit as the CI "Config
#: schema sync" step): adding or renaming a member cannot leave this document
#: stale.
WAIT_UNTIL_VALUES = " | ".join(get_args(WaitUntil))
LOAD_STATE_VALUES = " | ".join(get_args(LoadState))


def chat_skill() -> str:
    """Return the chat-agent skill document as markdown with YAML frontmatter."""
    return f"""\
---
name: robotsix-browser
description: Drive real headless-browser automation against web UIs — open an isolated session, observe the page, and click/type/select/submit under human confirmation.
---

# robotsix-browser

Drive real headless-browser automation against web UIs: open an isolated
session, navigate, read the page's accessibility tree and full-page screenshot
(state), then click / type / select / wait as needed.  Each session is an
isolated browser context with its own cookies and storage.

## Reaching the service

The service itself is unauthenticated; access is mediated by the deploy edge.
A chat agent has two ways to reach it:

- **Preferred — internal:** on the shared `central-deploy-proxy` network the
  service is reachable at `http://robotsix-browser:8000` with no edge auth
  gate.  No token required.
- **Public edge:** `https://browser.deploy.robotsix.net` sits behind a
  Tinyauth login gate.  Programmatic callers bypass the interactive login by
  sending an `Authorization: Bearer <token>` header (the mobile-token bypass
  route).  The token is provisioned to the chat agent as a vaulted secret in
  its component config; it is never embedded in this document or the repo.

Health check: `GET /health` -> `{{"status": "ok"}}` (port 8000).

## Sessions

- **Open** — `POST /sessions`
  - request: `{{"session_id": "str | null (reopen an existing session)"}}`
  - response: `{{"session_id": "str"}}`
- **Close** — `DELETE /sessions/{{id}}`
  - Close the session and its browser context, destroying all in-page state.
    Always confirmation-gated.
  - response: `{{"status": "closed"}}`

## Actions

Each mutating action returns `{{"status": "ok", "url": "str"}}` unless noted.
Use `GET /sessions/{{id}}/state` after every mutating action to observe the
result.

- **navigate** — `POST /sessions/{{id}}/navigate`
  - request: `{{"url": "str (http/https/data/about)", "wait_until": "{WAIT_UNTIL_VALUES} (default load)"}}`
- **state** *(read-only)* — `GET /sessions/{{id}}/state`
  - Read-only eye: ARIA accessibility tree (YAML) + full-page screenshot
    base64.  Use after every mutating action to observe the result.
  - response: `{{"url": "str", "title": "str", "accessibility_tree": "str (ARIA snapshot, YAML)", "screenshot_base64": "str"}}`
- **click** — `POST /sessions/{{id}}/click`
  - request: `{{"selector": "str | None (CSS selector)", "role": "str | None (ARIA role, e.g. button/link)", "name": "str | None (ARIA accessible name)"}}`
- **type** — `POST /sessions/{{id}}/type`
  - request: `{{"selector": "str (CSS)", "text": "str"}}`
- **select** — `POST /sessions/{{id}}/select`
  - request: `{{"selector": "str (CSS)", "value": "str | None (option by value)", "label": "str | None (option by visible label)"}}`
- **upload** — `POST /sessions/{{id}}/upload`
  - request: `{{"selector": "str (CSS, the <input type=file>)", "file_id": "str (robotsix-file-hub id)"}}`
- **wait** — `POST /sessions/{{id}}/wait`
  - request: `{{"selector": "str | None (CSS)", "state": "{LOAD_STATE_VALUES} | None", "timeout_ms": "int | None"}}`
- **value** *(read-only)* — `GET /sessions/{{id}}/value`
  - query: `{{"selector": "str (CSS)"}}`
  - response: `{{"selector": "str", "value": "str"}}`
- **fill_credentials** — `POST /sessions/{{id}}/fill-credentials`
  - request: `{{"entry": "str (vault entry name or id)", "username_selector": "str (CSS)", "password_selector": "str (CSS)"}}`
  - Resolves the scoped Vaultwarden entry server-side: the vault is enumerated
    via `GET /api/sync` and unlocked with the configured master password to
    decrypt the entry.  username/password are typed directly into the fields
    and are never returned, logged, or surfaced to the agent.  Only fills the
    form; it never submits.

## Vault diagnostics (read-only)

Read-only reachability checks for the scoped Vaultwarden collection.  Both
return only decrypted ids/names — never usernames, passwords, or any secret
value.

- **collections** — `GET /vault/collections`
  - response: `{{"collections": "[{{id, name}}] (name decrypted)"}}`
- **items** — `GET /vault/items`
  - response: `{{"items": "[{{id, name}}] (name decrypted, no secrets)"}}`

## Submit (human-gated)

- **submit** — `POST /sessions/{{id}}/submit`
  - request: `{{"selector": "str | None (CSS)", "role": "str (default button)", "name": "str | None (ARIA accessible name)", "wait_for_navigation": "bool (default true)"}}`
  - response: `{{"status": "ok", "url": "str"}}`
  - The ONLY endpoint that submits a form.  Always confirmation-gated: never
    call it automatically after filling.

## Safety

No endpoint other than `POST /sessions/{{id}}/submit` submits a form.
Read-only observation (`state`, `value`, vault diagnostics) is always allowed.
Every action that mutates the page is confirmation-gated: present the filled
state to the operator and get explicit OK first.

- **confirmation_gated** (ask the operator before performing): `navigate`,
  `click`, `type`, `select`, `upload`, `wait`, `fill_credentials`, `submit`,
  `close`.
- **read_only** (always allowed, never gated): `state` (`GET
  /sessions/{{id}}/state`), `value`, `vault_diagnostics.collections`,
  `vault_diagnostics.items`.
"""
