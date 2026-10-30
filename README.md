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

### Configuration

Configuration lives in a single JSON file located by the `ROBOTSIX_CONFIG_FILE`
environment variable (default: `config/config.json`).  There is **no**
environment overlay — the file (plus model defaults) is the sole source of
truth.  Secrets use pydantic `SecretStr` and are masked in repr / logs.  When
any required `bw_*` value is blank the credential-fill endpoint responds `503`
(not configured).

Every field, its default and an example `config.json` are listed in the
[configuration reference](docs/configuration.md).  Bind location
(`ROBOTSIX_BROWSER_HOST` / `ROBOTSIX_BROWSER_PORT`) is set via environment
variables for uvicorn — these are **not** config values.

## Configuration changes

**Rule:** When adding or changing a field on the `Settings` model in
`src/robotsix_browser/config.py`, regenerate the committed
`config/config.schema.json` **in the same commit** via:

```bash
uv run python -c "from robotsix_browser.config import Settings; from robotsix_config import config_schema_json; open('config/config.schema.json','w').write(config_schema_json(Settings))"
```

The CI gate (`.github/workflows/ci.yml`) fails otherwise.

## API

All interactions are **session-scoped**: each session is an isolated Playwright
browser context (its own cookies / storage).

| Method & path                     | Description                                        |
| --------------------------------- | -------------------------------------------------- |
| `POST /sessions`                  | Open a new session, or reuse `session_id`.         |
| `DELETE /sessions/{id}`           | Close a session.                                   |
| `POST /sessions/{id}/navigate`    | Navigate to a URL (`http`/`https`/`data`/`about`). |
| `GET /sessions/{id}/state`        | ARIA accessibility tree + full-page screenshot.    |
| `POST /sessions/{id}/click`       | Click by ARIA role (+ name) or CSS selector. Returns a clean `404` if nothing matches. |
| `POST /sessions/{id}/type`        | Fill a text field.                                 |
| `POST /sessions/{id}/select`      | Choose a `<select>` option (by value or label).    |
| `POST /sessions/{id}/upload`      | Attach a **file-hub file id** to a file input.     |
| `POST /sessions/{id}/wait`        | Wait for a selector and/or load state.             |
| `GET /sessions/{id}/value`        | Read back a field's current value. Returns a clean `404` if the selector matches no element. |
| `POST /sessions/{id}/fill-credentials` | Inject a **scoped Vaultwarden entry** (never echoed). |
| `POST /sessions/{id}/submit`      | **HUMAN-GATED** final submit / confirm.            |
| `GET /vault/collections`           | Read-only: collection ids/names the vault key can see. |
| `GET /vault/items`                 | Read-only: item ids/names the vault key can see (no secrets). |
| `GET /livez`                       | Liveness probe (no dependency checks). `503` while shutting down. |
| `GET /readyz`                      | Readiness probe: `200` only after init + all dependencies reachable. |
| `GET /startup`                     | Startup probe: `503` until initialization completes, then `200`. |

The machine-readable skill document (endpoints, request/response shapes, and
the confirmation-gated safety contract) is served at `GET /chat-skill` as
`text/markdown` with YAML frontmatter, per the chat-access standard. The
frontmatter carries a kebab-case `name` and one-sentence `description`; the
markdown body documents the HTTP API and safety rules.

Selector misses on `GET /value` and `POST /click` return a clean `404` naming
the target, never a `500` — see
[Selector misses](docs/architecture.md#selector-misses-return-a-clean-404).
The request flow through the app factory, session manager, operations,
credential fill and vault client is described in the
[architecture overview](docs/architecture.md).

### Health & Kubernetes probes

`/livez` (liveness), `/readyz` (readiness: startup done and Vault, File Hub and
session manager reachable) and `/startup` follow the standard Kubernetes probe
pattern; the legacy `GET /health` remains for simple uptime checks. Probe
semantics and an example pod spec are in
[Health & Kubernetes probes](docs/architecture.md#health--kubernetes-probes).

### Access & authentication

The service itself is unauthenticated; access is mediated by the deploy edge.
A chat-agent client has two ways to reach it:

- **Internal (preferred).** On the shared `central-deploy-proxy` network the
  service is reachable at `http://robotsix-browser:8000` with no edge auth
  gate — no token required.
- **Public edge.** `https://browser.deploy.robotsix.net` sits behind a
  Tinyauth login gate. Programmatic callers bypass the interactive login by
  sending an `Authorization: Bearer <token>` header (the mobile-token bypass
  route). The token is provisioned to the chat agent as a **vaulted secret**
  in its component config — never embedded in this repo.

### Human submit-gate

The `submit` endpoint is deliberately kept separate from `click`. It is the
**only** action that submits a form, and it exists so the flow is:

1. The agent fills the form (`type`, `select`, `upload`, …) and reads it back
   (`value`, `state`).
2. The agent presents the filled state to the operator.
3. **Only after explicit operator confirmation** is `POST /sessions/{id}/submit`
   invoked.

Never wire an automatic call to `/submit` after filling a form.

## Resilience & retry behavior

Outbound HTTP calls to fleet dependencies (file-hub and Vaultwarden) are
routed through the `robotsix-http` `RetryClient`, which implements automatic
retry with exponential backoff for transient failures:

- **Retried responses**: Transient network errors and server-side 5xx errors
  (500–599) are retried up to 4 times with exponential backoff.
- **Not retried**: 4xx client errors are not retried, except for rate-limit
  responses (429) which are retried.
- **Timeout semantics**: The configured `default_timeout_ms` applies to the
  entire retry sequence, not individual attempts.
- **Affected endpoints**: `POST /sessions/{id}/upload` (file-hub), and
  `POST /sessions/{id}/fill-credentials` (Vaultwarden prelogin, sync, and
  token exchange).

If all retries are exhausted, the original error is wrapped and returned to
the caller (e.g., as `FileHubError` or `VaultUpstreamError`).

## Credential injection (Vaultwarden)

`POST /sessions/{id}/fill-credentials` logs a session into a website **without
the secret ever passing through the chat agent, transcript, or logs**.

```jsonc
POST /sessions/{id}/fill-credentials
{
  "entry": "ovh-portal",          // vault entry name or id
  "fields": "both",               // both (default) | username | password
  "username_selector": "#login",  // optional CSS hint
  "password_selector": "#password" // optional CSS hint
}
// → 200 {"status": "ok", "url": "..."}   — value NEVER returned
```

**Two-phase logins.** Many portals (Lenovo, Microsoft, Google, Amazon, …)
ask for the email / identifier on one screen and the password on the next,
so both fields are never on the page at once. Drive those as two calls around
the intermediate click, each scoped with `fields`:

```jsonc
POST /sessions/{id}/fill-credentials  {"entry": "lenovo", "fields": "username"}
POST /sessions/{id}/click             {"role": "button", "name": "Continue"}
POST /sessions/{id}/fill-credentials  {"entry": "lenovo", "fields": "password"}
```

The vault entry is fetched afresh on each call; the password is still only
ever typed into the page. The agent never needs to know the email: it is part
of the vault entry and `fields: "username"` injects it.

**Field detection.** The optional `username_selector` / `password_selector`
are only the first candidates tried; a prioritized fallback list covers common
login-form variants, and a missing field yields a clean `422`. See
[Credential fill: field detection](docs/architecture.md#credential-fill-field-detection).

**Security model.** A dedicated Vaultwarden service account scoped to a single
collection (out-of-scope entry → `403`, missing → `404`); secrets are decrypted
server-side, typed directly into the page, and never returned or logged; no
2FA/TOTP; filling never submits. Details:
[Vaultwarden security model](docs/architecture.md#vaultwarden-security-model).

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

Some validation cannot run in GitHub-hosted CI and requires a human on a
network-enabled host (e.g. tuning the LinkedIn consent cookies). See
[RUNBOOKS.md](RUNBOOKS.md) for the manual procedures.
