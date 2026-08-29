"""Runtime configuration for the robotsix-browser service."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Service settings, populated from ``ROBOTSIX_BROWSER_*`` env vars."""

    model_config = SettingsConfigDict(env_prefix="ROBOTSIX_BROWSER_")

    #: Base URL of the robotsix-file-hub service used by the upload endpoint.
    file_hub_base_url: str = "http://localhost:8080"
    #: Launch Chromium headless.  Always true in production; overridable in dev.
    headless: bool = True
    #: Default action timeout in milliseconds for page interactions.
    default_timeout_ms: int = 30_000

    # --- Vaultwarden / Bitwarden CLI credential injection -----------------
    # All of the following are provisioned via the deploy EnvStore as masked
    # container env vars.  They are NEVER echoed, logged, or returned in an
    # HTTP response.  When any required value is blank the credential-fill
    # endpoint responds 503 (not configured) instead of leaking a partial
    # configuration.
    #
    #: Vaultwarden server URL (Vaultwarden speaks the Bitwarden API).
    bw_server_url: str = ""
    #: API-key ``client_id`` for the dedicated service account
    #: (``user.<uuid>``).
    bw_client_id: str = ""
    #: API-key ``client_secret`` for the dedicated service account.
    bw_client_secret: str = ""
    #: Unlock secret (the service account master password) used to derive a
    #: ``BW_SESSION`` for reads.
    bw_unlock_secret: str = ""
    #: The single collection id the service is scoped to.  Any entry outside
    #: this collection is rejected.
    bw_collection_id: str = ""
    #: Path to the Bitwarden CLI binary.
    bw_cli_path: str = "bw"


def get_settings() -> Settings:
    """Return a fresh :class:`Settings` instance read from the environment."""
    return Settings()
