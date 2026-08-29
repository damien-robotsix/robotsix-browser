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
2. Vaultwarden/Bitwarden secret client with zero-leakage credential injection.
3. 2FA handling / operator-pause-for-code.
4. The live OVH CS16584956 submission.

No credential/secret handling lives in this service yet — **values to fill are
passed in by the caller.**

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

### Configuration

Environment variables (prefix `ROBOTSIX_BROWSER_`):

| Variable                          | Default                  | Purpose                                   |
| --------------------------------- | ------------------------ | ----------------------------------------- |
| `ROBOTSIX_BROWSER_FILE_HUB_BASE_URL` | `http://localhost:8080` | Base URL of robotsix-file-hub (uploads).  |
| `ROBOTSIX_BROWSER_HEADLESS`       | `true`                   | Launch Chromium headless.                 |
| `ROBOTSIX_BROWSER_DEFAULT_TIMEOUT_MS` | `30000`             | Default action timeout.                   |
| `ROBOTSIX_BROWSER_HOST`           | `0.0.0.0`                | Bind host.                                |
| `ROBOTSIX_BROWSER_PORT`           | `8000`                   | Bind port.                                |

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
