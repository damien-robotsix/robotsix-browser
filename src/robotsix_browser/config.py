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
    #: The single collection id the service is scoped to.  Any entry outside
    #: this collection is rejected.
    bw_collection_id: str = ""


def get_settings() -> Settings:
    """Return a :class:`Settings` instance loaded from the JSON config file."""
    return load_config(Settings)
