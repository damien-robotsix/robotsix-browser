"""Runtime configuration for the robotsix-browser service.

Configuration lives in a single JSON file located by
:data:`robotsix_config.CONFIG_FILE_ENV` (``ROBOTSIX_CONFIG_FILE``) or
``config/config.json`` by default.  There is **no** environment overlay — the
file (plus model defaults) is the sole source of truth.
"""

from __future__ import annotations

from pydantic import BaseModel, SecretStr
from robotsix_config import load_config


class Settings(BaseModel):
    """Typed configuration model for robotsix-browser."""

    #: Base URL of the robotsix-file-hub service used by the upload endpoint.
    file_hub_base_url: str = "http://localhost:8080"
    #: Launch Chromium headless.  Always true in production; overridable in dev.
    headless: bool = True
    #: Default action timeout in milliseconds for page interactions.
    default_timeout_ms: int = 30_000
    #: Bounded timeout (ms) for locating a login field during credential
    #: fill.  Kept well below ``default_timeout_ms`` so a missing / variant
    #: login form fails fast with a clean 4xx instead of blocking on the 30s
    #: default and surfacing as a 500.
    credential_fill_timeout_ms: int = 5_000

    # --- Vaultwarden / Bitwarden CLI credential injection -----------------
    # All of the following are read from the single JSON config file.  Secrets
    # use pydantic SecretStr so they are masked in repr / logs.  When any
    # required value is blank the credential-fill endpoint responds 503 (not
    # configured) instead of leaking a partial configuration.
    #
    #: Vaultwarden server URL (Vaultwarden speaks the Bitwarden API).
    bw_server_url: str = ""
    #: API-key ``client_id`` for the dedicated service account
    #: (``user.<uuid>``).
    bw_client_id: SecretStr = SecretStr("")
    #: API-key ``client_secret`` for the dedicated service account.
    bw_client_secret: SecretStr = SecretStr("")
    #: Account email of the service account.  Used as the KDF salt when
    #: deriving the vault symmetric key (Bitwarden salts the master-key KDF
    #: with the lower-cased email), so unlock/decrypt cannot work without it.
    bw_email: str = ""
    #: Master password of the service account.  The ``client_credentials``
    #: flow returns an access token but *not* the vault decryption key, so the
    #: master password is required to unlock the vault and decrypt cipher
    #: fields (name / username / password).  Never logged or returned.
    bw_master_password: SecretStr = SecretStr("")
    #: Vaultwarden device parameters sent with the API-key token request.
    #: These are stable, non-secret identifiers for the service.
    #: ``bw_device_type`` is the Vaultwarden DeviceType enum (0 = Cli).
    bw_device_type: int = 0
    bw_device_identifier: str = "robotsix-browser"
    bw_device_name: str = "robotsix-browser"
    #: The single collection id the service is scoped to.  Any entry outside
    #: this collection is rejected.
    bw_collection_id: str = ""


def get_settings() -> Settings:
    """Return a :class:`Settings` instance loaded from the JSON config file."""
    return load_config(Settings)
