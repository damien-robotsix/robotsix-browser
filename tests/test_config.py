"""Tests for :mod:`robotsix_browser.config` (``Settings`` / ``get_settings``)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import SecretStr
from robotsix_config import CONFIG_FILE_ENV

from robotsix_browser.config import Settings, get_settings
from robotsix_browser.vault import VaultClient

_SECRETS = {
    "bw_client_id": "user.client-id-value",
    "bw_client_secret": "client-secret-value",
    "bw_master_password": "master-password-value",
}


def test_documented_defaults() -> None:
    settings = Settings()

    assert settings.file_hub_base_url == "http://localhost:8080"
    assert settings.headless is True
    assert settings.default_timeout_ms == 30_000
    assert settings.credential_fill_timeout_ms == 5_000
    assert settings.bw_server_url == ""
    assert settings.bw_email == ""
    assert settings.bw_collection_id == ""
    assert settings.bw_device_type == 0
    assert settings.bw_device_identifier == "robotsix-browser"
    assert settings.bw_device_name == "robotsix-browser"


def test_blank_secret_defaults_leave_vault_unconfigured() -> None:
    settings = Settings()

    for field in _SECRETS:
        value = getattr(settings, field)
        assert isinstance(value, SecretStr)
        assert value.get_secret_value() == ""
    # Blank secrets are what makes the credential endpoint answer 503.
    assert VaultClient.from_settings(settings).is_configured is False


def test_get_settings_loads_json_config_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps(
            {
                "file_hub_base_url": "http://file-hub:8080",
                "headless": False,
                "default_timeout_ms": 1234,
                "bw_server_url": "https://vault.example",
                "bw_email": "svc@example.com",
                "bw_collection_id": "col-1",
                "bw_device_name": "custom-device",
                **_SECRETS,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(CONFIG_FILE_ENV, str(config_file))

    settings = get_settings()

    assert settings.file_hub_base_url == "http://file-hub:8080"
    assert settings.headless is False
    assert settings.default_timeout_ms == 1234
    assert settings.bw_server_url == "https://vault.example"
    assert settings.bw_device_name == "custom-device"
    # Fields absent from the file keep their model defaults.
    assert settings.credential_fill_timeout_ms == 5_000
    assert settings.bw_device_identifier == "robotsix-browser"
    for field, secret in _SECRETS.items():
        assert getattr(settings, field).get_secret_value() == secret
    assert VaultClient.from_settings(settings).is_configured is True


def test_configuration_reference_lists_every_schema_field() -> None:
    """``docs/configuration.md`` must document every schema field + default."""
    root = Path(__file__).resolve().parent.parent
    schema = json.loads(
        (root / "config" / "config.schema.json").read_text(encoding="utf-8")
    )
    doc = (root / "docs" / "configuration.md").read_text(encoding="utf-8")

    for field, spec in schema["properties"].items():
        default = json.dumps(spec["default"])
        row = f"| `{field}` | `{default}` |"
        assert row in doc, f"missing or stale row for {field!r}: {row}"


def test_secrets_are_masked_in_repr_and_json() -> None:
    settings = Settings(**_SECRETS)  # type: ignore[arg-type]

    rendered = [repr(settings), str(settings), settings.model_dump_json()]
    for text in rendered:
        for secret in _SECRETS.values():
            assert secret not in text
    assert "**********" in settings.model_dump_json()
