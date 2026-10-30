# Configuration reference

All runtime configuration for robotsix-browser is defined by the `Settings`
model in `src/robotsix_browser/config.py` and loaded by `get_settings()` via
`robotsix_config.load_config`.

- **Source.** A single JSON file located by the `ROBOTSIX_CONFIG_FILE`
  environment variable (default: `config/config.json`).
- **No environment overlay.** The file, plus the model defaults below, is the
  sole source of truth. A field omitted from the file keeps its default.
- **Schema.** The committed JSON Schema is
  [`config/config.schema.json`](../config/config.schema.json). This page is
  checked against it by `tests/test_config.py`: every schema field must
  appear in the table below with its schema default.
- **Secrets.** Fields typed `SecretStr` are masked (`**********`) in `repr`,
  `str` and `model_dump_json()` output, so they never leak into logs.

## Fields

| Field | Default | Type | Purpose |
| ----- | ------- | ---- | ------- |
| `file_hub_base_url` | `"http://localhost:8080"` | `string` | Base URL of robotsix-file-hub, used by the upload endpoint. |
| `headless` | `true` | `boolean` | Launch Chromium headless. Always true in production; overridable in dev. |
| `default_timeout_ms` | `30000` | `integer` | Default action timeout (ms) for page interactions; also bounds the outbound retry sequence. |
| `credential_fill_timeout_ms` | `5000` | `integer` | Bounded timeout (ms) for locating a login field during credential fill, so a missing field fails fast with a 4xx. |
| `bw_server_url` | `""` | `string` | Vaultwarden server URL (Vaultwarden speaks the Bitwarden API). |
| `bw_client_id` | `""` | `SecretStr` | API-key `client_id` of the dedicated service account (`user.<uuid>`). |
| `bw_client_secret` | `""` | `SecretStr` | API-key `client_secret` of the service account. |
| `bw_email` | `""` | `string` | Service-account email; the KDF salt used to unlock the vault. |
| `bw_master_password` | `""` | `SecretStr` | Service-account master password, used to unlock and decrypt vault entries. Never logged or returned. |
| `bw_device_type` | `0` | `integer` | Vaultwarden `DeviceType` enum sent with the token request (`0` = Cli). |
| `bw_device_identifier` | `"robotsix-browser"` | `string` | Stable, non-secret device identifier sent with the token request. |
| `bw_device_name` | `"robotsix-browser"` | `string` | Stable, non-secret device name sent with the token request. |
| `bw_collection_id` | `""` | `string` | The single vault collection the service is scoped to; entries outside it are rejected with `403`. |

## Credential-fill readiness

Vault access is enabled only when **all** of `bw_server_url`, `bw_client_id`,
`bw_client_secret`, `bw_email`, `bw_master_password` and `bw_collection_id`
are non-blank. With the blank defaults, `POST /sessions/{id}/fill-credentials`
and the `/vault/*` diagnostics answer `503` (not configured) instead of using a
partial configuration.

## Example `config.json`

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

The `bw_*` secrets are written into the config file (permissions `0600`,
inside a `0700` directory) — never committed, never echoed.

## Not configuration

The bind location (`ROBOTSIX_BROWSER_HOST` / `ROBOTSIX_BROWSER_PORT`) is set
via environment variables for uvicorn — these are **not** `Settings` fields.

## Changing a field

When adding or changing a field on `Settings`, regenerate
`config/config.schema.json` **in the same commit** (see
[Configuration changes](../README.md#configuration-changes)) and update the
table above; CI and `tests/test_config.py` fail otherwise.
