# Changelog

All notable changes to this project are documented here. From now on this
file is written by [release-please](https://github.com/googleapis/release-please)
from conventional commits — do not edit it by hand.

## 0.1.0

Historical section folded in from the retired `changelog.d/` fragments.

### Features

- Bootstrap the **robotsix-browser** service: a session-scoped HTTP API wrapping
  Playwright headless Chromium for interactive form-filling (navigate, inspect,
  click, type, select, upload from file-hub, wait, read-back). The final
  submit/confirm action is a separate, human-gated endpoint — no endpoint
  auto-submits a form. Includes Dockerfile (installs Chromium), CI, and tests.
- Add `POST /sessions/{id}/fill-credentials` endpoint that injects a scoped Vaultwarden (Bitwarden CLI) entry's username/password directly into the browser form fields, with zero secret leakage into responses, logs, or the agent. Scoped to a dedicated service account and a single collection; no TOTP/2FA; the human submit-gate is preserved.
- Add `config/config.schema.json` and the `robotsix.deploy.config-target` compose label so the service passes robotsix config-standard validation during central-deploy registration.
- Add read-only `GET /vault/collections` and `GET /vault/items` diagnostic endpoints that list collection/item ids and names visible to the configured Vaultwarden API key, returning only non-secret metadata (never passwords, secure-note contents, or custom secret fields). Reuses the existing `bw_server_url`, `bw_client_id`, `bw_client_secret`, and `bw_collection_id` config; upstream auth/fetch failures surface a safe status/reason instead of a generic 502, and an unconfigured vault returns 503.
- Fix `/sessions/{id}/fill-credentials` to fail fast with a clean 422 (not 500) when a login field selector is absent on the page. Adds configurable `credential_fill_timeout_ms` (default 5s, well below the 30s global action timeout) so missing / variant login forms no longer block the request. Also dismisses common cookie-consent / interstitial walls before attempting field fill, so the underlying login form renders when possible.
- Enhance `/sessions/{id}/fill-credentials` field location to detect login inputs via a prioritized candidate list — the caller-supplied CSS selectors first, then known login ids (`#username`, `#session_key`, `#session_password`, etc.), `input[type=email]` / `input[type=password]`, `name`/`autocomplete` attributes, and finally ARIA role + accessible-name matching — instead of relying on hard-coded CSS ids alone. Classic LinkedIn member-login, guest sign-in, and locale variant forms all bind when present. The clean 4xx ("login field not found") failure behavior and the vault decrypt/unlock path are unchanged.
- Add a `fields` option (`both` | `username` | `password`, default `both`) to `POST /sessions/{id}/fill-credentials` so two-phase logins — email/identifier on one screen, password on the next (Lenovo, Microsoft, Google, …) — can be driven as two calls around the intermediate continue click instead of failing with "password field not found" on the first screen. `username_selector` / `password_selector` become optional hints; detection falls back to the built-in candidate tiers when they are omitted. The chat skill documents the two-call recipe. Secrets handling is unchanged: the password is only ever typed into the page.

### Miscellaneous

- Bring the repository up to the robotsix-standards baseline: add an MIT `LICENSE`, add `.github/dependabot.yml` covering the uv, github-actions, and docker ecosystems, declare least-privilege `permissions` (workflow-level `{}` plus scoped per-job) and a `timeout-minutes` ceiling in CI, and integrate the shared reusable security gates (zizmor workflow audit via `lint-workflows.yml`, plus SAST/dependency-audit/secret-scan/SBOM via `python-security.yml`).
- Add the `deploy/docker-compose.yml` central-deploy contract (contract version 1, service labels for deploy mode/port/health) so robotsix-central-deploy can register and manage the service.
- `deploy/docker-compose.yml` now references the published GHCR image (`ghcr.io/damien-robotsix/robotsix-browser:main`), uses the fleet-standard `robotsix.deploy.*` label prefix, and drops the build and healthcheck sections (managed centrally by robotsix-central-deploy).
