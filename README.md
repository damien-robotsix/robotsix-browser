# robotsix-browser

Interactive headless-browser / form-filling HTTP service for the robotsix fleet.

The chat agent already has a read-only `render_url` tool (screenshot +
accessibility tree). **robotsix-browser** goes further: it wraps
**Playwright headless Chromium** behind a small, session-scoped HTTP API so an
agent can *drive* a real browser — navigate, inspect, and fill web forms.

> ⚠️ **HUMAN SUBMIT-GATE (hard rule).** This service **never auto-submits a
> consequential form.** The final submit / confirm action is exposed as a
> *separate* endpoint (`POST /sessions/{id}/submit`) that must be invoked
> explicitly, so an operator can gate the consequential action
> in-conversation. No other endpoint submits a form. See
> [Human submit-gate](#human-submit-gate) below.

## Status / scope

This repository is the **implementation home** for the interactive-browser
capability. This first ticket bootstraps the deployable service: app skeleton,
Dockerfile, CI, and tests.

**Out of scope** (separate, dependent follow-on tickets):

1. Roster registration + chat-skill doc in `robotsix-central-deploy`.
2. 2FA handling / operator-pause-for-code.
3. The live OVH CS16584956 submission.

Scoped Vaultwarden credential injection **is** implemented — see
[Credential injection](#credential-injection-vaultwarden) below. Non-secret
values to fill are still passed in by the caller (`type`, `select`, …).

## Running

```bash
uv sync
uv run playwright install --with-deps chromium
uv run robotsix-browser              # serves on 0.0.0.0:8000
```

Or with Docker (installs Chromium into the image):

```bash
docker build -t robotsix-browser .
docker run -p 8000:8000 robotsix-browser
```

Or with Docker Compose:

```bash
docker-compose -f deploy/docker-compose.yml up
```

### Configuration

Environment variables (prefix `ROBOTSIX_BROWSER_`):

| Variable                          | Default                  | Purpose                                   |
| --------------------------------- | ------------------------ | ----------------------------------------- |
| `ROBOTSIX_BROWSER_FILE_HUB_BASE_URL` | `http://localhost:8080` | Base URL of robotsix-file-hub (uploads).  |
| `ROBOTSIX_BROWSER_HEADLESS`       | `true`                   | Launch Chromium headless.                 |
| `ROBOTSIX_BROWSER_DEFAULT_TIMEOUT_MS` | `30000`             | Default action timeout.                   |
| `ROBOTSIX_BROWSER_HOST`           | `0.0.0.0`                | Bind host.                                |
| `ROBOTSIX_BROWSER_PORT`           | `8000`                   | Bind port.                                |
| `ROBOTSIX_BROWSER_BW_SERVER_URL`  | `""`                     | Vaultwarden server URL (Bitwarden API).   |
| `ROBOTSIX_BROWSER_BW_CLIENT_ID`   | `""`                     | API-key `client_id` (`user.<uuid>`).      |
| `ROBOTSIX_BROWSER_BW_CLIENT_SECRET` | `""`                   | API-key `client_secret` (masked).         |
| `ROBOTSIX_BROWSER_BW_COLLECTION_ID` | `""`                   | The single collection the service reads.  |

The `BW_*` values are provisioned via the deploy EnvStore as masked container
env vars — never committed, never echoed. When any required value is blank the
credential-fill endpoint responds `503` (not configured).

## API

All interactions are **session-scoped**: each session is an isolated Playwright
browser context (its own cookies / storage).

| Method & path                     | Description                                        |
| --------------------------------- | -------------------------------------------------- |
| `POST /sessions`                  | Open a new session, or reuse `session_id`.         |
| `DELETE /sessions/{id}`           | Close a session.                                   |
| `POST /sessions/{id}/navigate`    | Navigate to a URL (`http`/`https`/`data`/`about`). |
| `GET /sessions/{id}/state`        | ARIA accessibility tree + full-page screenshot.    |
| `POST /sessions/{id}/click`       | Click by ARIA role (+ name) or CSS selector.       |
| `POST /sessions/{id}/type`        | Fill a text field.                                 |
| `POST /sessions/{id}/select`      | Choose a `<select>` option (by value or label).    |
| `POST /sessions/{id}/upload`      | Attach a **file-hub file id** to a file input.     |
| `POST /sessions/{id}/wait`        | Wait for a selector and/or load state.             |
| `GET /sessions/{id}/value`        | Read back a field's current value.                 |
| `POST /sessions/{id}/fill-credentials` | Inject a **scoped Vaultwarden entry** (never echoed). |
| `POST /sessions/{id}/submit`      | **HUMAN-GATED** final submit / confirm.            |

### Human submit-gate

The `submit` endpoint is deliberately kept separate from `click`. It is the
**only** action that submits a form, and it exists so the flow is:

1. The agent fills the form (`type`, `select`, `upload`, …) and reads it back
   (`value`, `state`).
2. The agent presents the filled state to the operator.
3. **Only after explicit operator confirmation** is `POST /sessions/{id}/submit`
   invoked.

Never wire an automatic call to `/submit` after filling a form.

## Credential injection (Vaultwarden)

`POST /sessions/{id}/fill-credentials` logs a session into a website **without
the secret ever passing through the chat agent, transcript, or logs**.

```jsonc
POST /sessions/{id}/fill-credentials
{
  "entry": "ovh-portal",          // vault entry name or id
  "username_selector": "#login",
  "password_selector": "#password"
}
// → 200 {"status": "ok", "url": "..."}   — value NEVER returned
```

Security model:

- **Bitwarden API + client credentials.** The service authenticates to the operator's
  Vaultwarden server (which speaks the Bitwarden JSON API) via
  `POST /identity/connect/token` with `grant_type=client_credentials`
  (`client_id` = `user.<uuid>`, `client_secret`). Both values come from env vars
  (see [Configuration](#configuration)) — never in code, never in the repo.  No
  master password or unlock step is required.
- **Dedicated service account + single collection.** The service is scoped to
  one provisioned collection. A request for an entry outside that collection
  fails cleanly with `403`; a missing entry returns `404`.
- **Zero leakage.** At fill-time the entry is fetched and the
  `username`/`password` are typed **directly into the browser fields**. The
  secret value is never returned in a response body, never logged, and never
  surfaced to the agent (`VaultCredential` redacts its password in `repr`).
- **No 2FA / TOTP.** No TOTP seed is read or stored. If a site ever presents
  2FA, the flow must pause for an operator-supplied code (not implemented yet).
- **Human submit-gate preserved.** Filling only fills fields — the separate
  `POST /sessions/{id}/submit` endpoint remains the only submit path.

## Development

```bash
uv sync
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run deptry .
uv run vulture --ignore-decorators "@field_validator,@model_validator" src/ vulture_whitelist.py
uv run pytest
```

The headless smoke tests are skipped automatically when no Chromium binary is
installed; run `uv run playwright install chromium` to exercise them locally.
