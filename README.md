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
truth.  Secrets use pydantic `SecretStr` and are masked in repr / logs.

| Field                 | Default              | Type        | Purpose                                   |
| --------------------- | -------------------- | ----------- | ----------------------------------------- |
| `file_hub_base_url`   | `http://localhost:8080` | `string` | Base URL of robotsix-file-hub (uploads).  |
| `headless`            | `true`               | `boolean`   | Launch Chromium headless.                 |
| `default_timeout_ms`  | `30000`              | `integer`   | Default action timeout.                   |
| `credential_fill_timeout_ms` | `5000`         | `integer`   | Bounded timeout (ms) for locating a login field during credential fill. |
| `bw_server_url`       | `""`                 | `string`    | Vaultwarden server URL (Bitwarden API).   |
| `bw_client_id`        | `""`                 | `SecretStr` | API-key `client_id` (`user.<uuid>`).      |
| `bw_client_secret`    | `""`                 | `SecretStr` | API-key `client_secret` (masked).         |
| `bw_email`            | `""`                 | `string`    | Service-account email (vault-unlock KDF salt). |
| `bw_master_password`  | `""`                 | `SecretStr` | Service-account master password used to unlock/decrypt the vault (masked). |
| `bw_collection_id`    | `""`                 | `string`    | The single collection the service reads.  |

Example `config.json`:

```json
{
  "file_hub_base_url": "http://file-hub:8080",
  "bw_server_url": "https://vault.example",
  "bw_client_id": "user.XXXX",
  "bw_client_secret": "secret",
  "bw_email": "svc@example.com",
  "bw_master_password": "master-password",
  "bw_collection_id": "col-XXXX"
}
```

The `BW_*` secrets are written into the config file (permissions `0600`,
inside a `0700` directory) — never committed, never echoed.  When any
required value is blank the credential-fill endpoint responds `503` (not
configured).

Bind location (`ROBOTSIX_BROWSER_HOST` / `ROBOTSIX_BROWSER_PORT`) is set
via environment variables for uvicorn — these are **not** config values.

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
| `POST /sessions/{id}/click`       | Click by ARIA role (+ name) or CSS selector.       |
| `POST /sessions/{id}/type`        | Fill a text field.                                 |
| `POST /sessions/{id}/select`      | Choose a `<select>` option (by value or label).    |
| `POST /sessions/{id}/upload`      | Attach a **file-hub file id** to a file input.     |
| `POST /sessions/{id}/wait`        | Wait for a selector and/or load state.             |
| `GET /sessions/{id}/value`        | Read back a field's current value.                 |
| `POST /sessions/{id}/fill-credentials` | Inject a **scoped Vaultwarden entry** (never echoed). |
| `POST /sessions/{id}/submit`      | **HUMAN-GATED** final submit / confirm.            |
| `GET /vault/collections`           | Read-only: collection ids/names the vault key can see. |
| `GET /vault/items`                 | Read-only: item ids/names the vault key can see (no secrets). |

The machine-readable skill document (endpoints, request/response shapes, and
the confirmation-gated safety contract) is served at `GET /chat-skill` for a
chat agent to discover the API surface.

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
  (`client_id` = `user.<uuid>`, `client_secret`). Both values come from the
  JSON config file (see [Configuration](#configuration)) — never in code,
  never in the repo.
- **Sync + unlock/decrypt.** Ciphers are enumerated via `GET /api/sync` (there
  is no `/api/items` route on Vaultwarden). The sync payload's fields are
  encrypted "EncString" blobs, so the vault is **unlocked** with the account
  master password: the master-key-derived symmetric key decrypts the user key,
  which decrypts the RSA private key, which decrypts the organization key that
  finally decrypts the entry's `name`/`username`/`password`. This needs
  `bw_email` (KDF salt) and `bw_master_password` in the config — the
  `client_credentials` access token alone cannot decrypt vault data. Decryption
  happens server-side; plaintext secrets are never logged or returned.
- **Dedicated service account + single collection.** The service is scoped to
  one provisioned collection. A request for an entry outside that collection
  fails cleanly with `403`; a missing entry returns `404`. The read-only
  `GET /vault/collections` and `GET /vault/items` diagnostics return only
  decrypted **ids and names** — never secret values.
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
