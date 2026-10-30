# Architecture overview

robotsix-browser wraps Playwright headless Chromium behind a small,
session-scoped HTTP API. This page describes how a request flows through the
service and collects the behavioural deep-dives that used to live in the
README. For configuration see [configuration.md](configuration.md).

## Components

| Module | Role |
| ------ | ---- |
| `config.py` | `Settings` model + `get_settings()` (single JSON file, see [configuration.md](configuration.md)). |
| `app.py` | `create_app()` application factory: route handlers, dependencies, error mapping, request logging, `lifespan` hook, Kubernetes probes. |
| `sessions.py` | `SessionManager`: lazily launches Chromium and owns one isolated browser context (cookies / storage) per session. |
| `operations.py` | All direct Playwright `Page` usage (navigate, state, click, type, select, upload, wait, value, submit). |
| `credential_fill.py` | Locates login fields (with fallbacks and consent-wall handling) and types the vault credential into them. |
| `vault.py` / `bwcrypto.py` | `VaultClient`: Vaultwarden (Bitwarden JSON API) login, sync, unlock and decrypt of the scoped collection. |
| `filehub.py` | `FileHubClient`: fetches files from robotsix-file-hub for the upload endpoint. |
| `models.py` | Pydantic request / response models (also the OpenAPI schema). |
| `chat_skill.py` | The `GET /chat-skill` markdown document for chat agents. |

## Request flow

1. **App factory.** `create_app(settings)` builds the FastAPI app, stores the
   effective `Settings` (from `get_settings()` when none is passed) on
   `app.state.settings`, and registers every route.
2. **Lifespan.** On startup the `lifespan` hook builds the `SessionManager`,
   `FileHubClient` and `VaultClient.from_settings(settings)` on `app.state`,
   installs the SIGTERM handler and marks the probe state started. On shutdown
   it marks the service shutting down and stops the session manager, closing
   every browser context.
3. **Dependencies.** Handlers receive these shared resources through the
   `get_manager`, `get_filehub`, `get_vault` and `get_app_settings`
   dependencies (tests override them to inject fakes).
4. **Session lookup.** Every `/sessions/{id}/…` handler resolves the session
   first; an unknown id returns `404`. Chromium is launched lazily on the first
   `POST /sessions`.
5. **Operation.** The handler delegates to a function in `operations.py`
   (or `credential_fill.fill_credentials`), which drives the Playwright `Page`
   and returns the current page URL.
6. **Error mapping.** Domain exceptions are translated to clean HTTP errors:
   unsupported URL scheme → `400`; selector miss → `404`; invalid file id →
   `400`; file-hub failure → `502`; login field not found → `422`; vault entry
   out of scope → `403`; entry missing → `404`; vault not configured → `503`;
   vault upstream failure → `502`.
7. **Credential fill.** `fill-credentials` asks the `VaultClient` for the
   named entry (token exchange, `GET /api/sync`, unlock, decrypt), then
   `credential_fill` types the username / password directly into the page.
   The secret is never returned or logged.
8. **Submit.** Only `POST /sessions/{id}/submit` submits a form (the
   [human submit-gate](../README.md#human-submit-gate)).

## Selector misses return a clean `404`

`GET /value` and `POST /click` never raise an unhandled `500` / stack trace
when their selector (or role + name) matches no element. Both return a clean,
structured HTTP `404` whose body names the target that was not found:

```jsonc
GET /sessions/{id}/value?selector=#does-not-exist
// → 404
{
  "detail": "selector '#does-not-exist' matched no element"
}
```

```jsonc
POST /sessions/{id}/click
{
  "selector": "#does-not-exist"
}
// → 404
{
  "detail": "selector '#does-not-exist' matched no element"
}
```

A role+name click miss produces the same shape using the accessible-target
description, e.g. `"role 'button' with name 'Sign in' matched no element"`.
The miss is detected within a bounded timeout, so the `404` arrives promptly.

## Health & Kubernetes probes

The service follows the standard three-endpoint Kubernetes probe pattern so an
orchestrator can distinguish *alive*, *ready*, and *started*. The legacy
`GET /health` endpoint remains for simple uptime checks.

| Probe       | Endpoint   | Meaning                                                        |
| ----------- | ---------- | ------------------------------------------------------------- |
| Liveness    | `/livez`   | Is the process alive? No dependency checks (<100ms). Returns `503 {"status": "shutting_down"}` after SIGTERM so new traffic drains. |
| Readiness   | `/readyz`  | Can this pod serve traffic? `200` only after startup completes **and** every critical dependency (Vault, File Hub, session manager) answers within ~1s. Returns `503 {"status": "unready", "failures": [...]}` if any dependency is unreachable; the pod is removed from service endpoints without a restart. |
| Startup     | `/startup` | Has initialization finished? `503 {"status": "starting"}` until the lifespan hook has built every shared resource, then `200`. |

Example pod spec wiring all three probes (a high `startupProbe.failureThreshold`
gives slow startups up to five minutes before liveness takes over):

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: robotsix-browser
spec:
  containers:
    - name: robotsix-browser
      image: robotsix-browser:main
      ports:
        - containerPort: 8000
      startupProbe:
        httpGet:
          path: /startup
          port: 8000
        periodSeconds: 10
        failureThreshold: 30   # up to 5 minutes for slow startup
      livenessProbe:
        httpGet:
          path: /livez
          port: 8000
        periodSeconds: 10
        failureThreshold: 3
      readinessProbe:
        httpGet:
          path: /readyz
          port: 8000
        periodSeconds: 10
        failureThreshold: 3
```

## Credential fill: field detection

The optional `username_selector` / `password_selector` are just the *first*
(highest-priority) candidates. The fields are actually located via a
prioritized fallback list so classic member-login, guest sign-in, and locale
variant forms all bind when present:

- Username/email: caller selector → `#username`, `#session_key`, `#email`,
  `#login` → `input[type=email]` → `autocomplete="username"|"email"` →
  `name` containing `user`/`mail`/`login` (case-insensitive) → ARIA
  role + accessible-name match (`textbox` whose name mentions
  username/user/email/e-mail/login/sign-in/account/identifier).
- Password: caller selector → `#password`, `#session_password`, `#passwd` →
  `input[type=password]` → `autocomplete="current-password"` → `name`
  containing `pass` (case-insensitive) → ARIA role + accessible-name match
  (`textbox` whose name mentions password).

If no candidate matches, the endpoint returns a clean 4xx (e.g. `422` with a
`"login <field> not found"` reason) instead of hanging or returning a 500.
Vault decrypt/unlock behavior is unchanged.

## Vaultwarden security model

- **Bitwarden API + client credentials.** The service authenticates to the
  operator's Vaultwarden server (which speaks the Bitwarden JSON API) via
  `POST /identity/connect/token` with `grant_type=client_credentials`
  (`client_id` = `user.<uuid>`, `client_secret`). Both values come from the
  JSON config file (see [configuration.md](configuration.md)) — never in
  code, never in the repo.
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
