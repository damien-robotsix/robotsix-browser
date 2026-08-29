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


def get_settings() -> Settings:
    """Return a fresh :class:`Settings` instance read from the environment."""
    return Settings()
