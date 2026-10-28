# Changelog

All notable changes to this project are documented here. From now on this
file is written by [release-please](https://github.com/googleapis/release-please)
from conventional commits — do not edit it by hand.

## [0.2.0](https://github.com/damien-robotsix/robotsix-browser/compare/v0.1.0...v0.2.0) (2026-10-28)


### Features

* Add drift check for chat-skill document vs. actual API routes/models (20260903T192239Z-add-drift-check-for-chat-skill-document-132a) ([#29](https://github.com/damien-robotsix/robotsix-browser/issues/29)) ([6bc4e27](https://github.com/damien-robotsix/robotsix-browser/commit/6bc4e27d04cc3c41eed39cdfed86c4bad209fef7))
* Add read-only Vaultwarden diagnostics endpoint (20260901T214021Z-add-read-only-vaultwarden-diagnostics-en-88bf) ([#25](https://github.com/damien-robotsix/robotsix-browser/issues/25)) ([76bc57c](https://github.com/damien-robotsix/robotsix-browser/commit/76bc57c011503f365d737c16fdf347dfe28e75ea))
* Expose robotsix-browser as a chat component (chat-skill + auth token + port-contract fix) (20260831T091514Z-expose-robotsix-browser-as-a-chat-compon-f036) ([#21](https://github.com/damien-robotsix/robotsix-browser/issues/21)) ([6269ab7](https://github.com/damien-robotsix/robotsix-browser/commit/6269ab7ce50ceff35951599b036dacadffc37ab2))
* **fill-credentials:** drive two-phase logins with a `fields` option ([#92](https://github.com/damien-robotsix/robotsix-browser/issues/92)) ([d46c20f](https://github.com/damien-robotsix/robotsix-browser/commit/d46c20fb479ccaf65e54def6cc1331ecbaa28901))
* Vaultwarden credential injection for the browser service (zero-leakage, no stored 2FA) (20260829T171306Z-vaultwarden-credential-injection-for-the-f175) ([#5](https://github.com/damien-robotsix/robotsix-browser/issues/5)) ([e4307c8](https://github.com/damien-robotsix/robotsix-browser/commit/e4307c86573d3c9d3d4aef9a5da4725e62de6bcd))


### Bug Fixes

* Align robotsix-browser /chat-skill to the markdown+frontmatter chat-access standard (currently serves JSON) (20260916T080026Z-align-robotsix-browser-chat-skill-to-the-2815) ([#56](https://github.com/damien-robotsix/robotsix-browser/issues/56)) ([7a93849](https://github.com/damien-robotsix/robotsix-browser/commit/7a93849e85b1421d4f141ff622b834cf81162131))
* CI failure: CI on main (20261022T202303Z-ci-failure-ci-on-main-8547) ([#116](https://github.com/damien-robotsix/robotsix-browser/issues/116)) ([6efd226](https://github.com/damien-robotsix/robotsix-browser/commit/6efd2267f760c1d728c45797b348465cceb9e9fb))
* **deploy:** add the exact central-deploy-contract-version header to deploy/docker-compose.yml ([#12](https://github.com/damien-robotsix/robotsix-browser/issues/12)) ([ab01758](https://github.com/damien-robotsix/robotsix-browser/commit/ab01758afb29e4f2ff59ffd7f0615eb23acfa486))
* **deploy:** drop the config-target label — the service is env-var configured ([#15](https://github.com/damien-robotsix/robotsix-browser/issues/15)) ([6d82f8b](https://github.com/damien-robotsix/robotsix-browser/commit/6d82f8b80adecaaa24ae1272a6f7b0a561d7d349))
* **docker:** install git in the build stage for the robotsix-config git dep ([#18](https://github.com/damien-robotsix/robotsix-browser/issues/18)) ([8807c82](https://github.com/damien-robotsix/robotsix-browser/commit/8807c825c4a2b844d803caa20f03542ccc7e51c2))
* **docker:** start from the venv entrypoint instead of uv run — crash loop ([#17](https://github.com/damien-robotsix/robotsix-browser/issues/17)) ([ee15946](https://github.com/damien-robotsix/robotsix-browser/commit/ee15946ad3a7437ef926d60153717143ff91ad6e))
* Handle LinkedIn consent-redirect wall before credential fill (20260905T134501Z-handle-linkedin-consent-redirect-wall-be-3d48) ([#46](https://github.com/damien-robotsix/robotsix-browser/issues/46)) ([889dfd7](https://github.com/damien-robotsix/robotsix-browser/commit/889dfd7f109c61793ecdfdfbfeb79ccd1da5eccb))
* Make fill-credentials fail cleanly when login field is absent; dismiss consent/interstitial walls before fill (20260903T221210Z-make-fill-credentials-fail-cleanly-when-ac9e) ([#36](https://github.com/damien-robotsix/robotsix-browser/issues/36)) ([e000d04](https://github.com/damien-robotsix/robotsix-browser/commit/e000d04433524e7dc339151828fc52cda3ed5030))
* **vault:** decrypt items that carry a per-item cipher key ([#91](https://github.com/damien-robotsix/robotsix-browser/issues/91)) ([b061e77](https://github.com/damien-robotsix/robotsix-browser/commit/b061e7725f595c95934546e69d70e1b6a454d4e7))


### Documentation

* AGENT.md: Configuration changes — When adding or changing a field on the `Settings` model in `src/robotsix_browse… (20260901T224607Z-agent-md-configuration-changes-when-addi-a83b) ([#24](https://github.com/damien-robotsix/robotsix-browser/issues/24)) ([10e2c9b](https://github.com/damien-robotsix/robotsix-browser/commit/10e2c9ba974671ac78f093f89e5a565d5570c627))
* Change robotsix-browser README image reference from :latest to :main (20261026T122713Z-change-robotsix-browser-readme-image-ref-23bb) ([#129](https://github.com/damien-robotsix/robotsix-browser/issues/129)) ([393022e](https://github.com/damien-robotsix/robotsix-browser/commit/393022ebd536f21f6864947922916f6d0bacf0d0))
* docstring gap: add docstring to upload in src/robotsix_browser/app.py:479 (20261021T175652Z-docstring-gap-add-docstring-to-upload-in-c119) ([#109](https://github.com/damien-robotsix/robotsix-browser/issues/109)) ([b5d2917](https://github.com/damien-robotsix/robotsix-browser/commit/b5d2917b9f5034adc5ff9b04c7519bee5607e55b))
* Document structured 404 responses for /value and /click in README/API examples (20260904T181753Z-document-structured-404-responses-for-va-f158) ([#44](https://github.com/damien-robotsix/robotsix-browser/issues/44)) ([8b36136](https://github.com/damien-robotsix/robotsix-browser/commit/8b361363acb6ffb10ba4cfe0954a276c3563781d))

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
