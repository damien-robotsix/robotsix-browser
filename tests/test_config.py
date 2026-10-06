"""Unit tests for :mod:`robotsix_browser.config`.

Exercises :class:`Settings` validation / defaults / secret masking and the
:func:`get_settings` loader, which reads the single JSON config file located by
``ROBOTSIX_CONFIG_FILE`` (see :mod:`robotsix_config`).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError
from robotsix_config import CONFIG_FILE_ENV, InvalidConfigError, load_config

from robotsix_browser.config import Settings, get_settings


def _write_config(tmp_path: Path, data: dict[str, object]) -> Path:
    """Write ``data`` as the JSON config file and return its path."""
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_load_valid_config_from_file(tmp_path: Path) -> None:
    """A fully specified config file loads into a validated ``Settings``."""
    path = _write_config(
        tmp_path,
        {
            "file_hub_base_url": "http://file-hub:9000",
            "headless": False,
            "default_timeout_ms": 10_000,
            "credential_fill_timeout_ms": 2_000,
            "bw_server_url": "https://vault.example",
            "bw_client_id": "user.abc",
            "bw_client_secret": "shhh",
            "bw_email": "svc@example.com",
            "bw_master_password": "master-pw",
            "bw_device_type": 0,
            "bw_collection_id": "col-1",
        },
    )

    settings = load_config(Settings, path)

    assert settings.file_hub_base_url == "http://file-hub:9000"
    assert settings.headless is False
    assert settings.default_timeout_ms == 10_000
    assert settings.credential_fill_timeout_ms == 2_000
    assert settings.bw_server_url == "https://vault.example"
    assert settings.bw_email == "svc@example.com"
    assert settings.bw_collection_id == "col-1"
    # Secrets round-trip through SecretStr and expose their value explicitly.
    assert settings.bw_client_secret.get_secret_value() == "shhh"
    assert settings.bw_master_password.get_secret_value() == "master-pw"


def test_defaults_when_optional_fields_omitted(tmp_path: Path) -> None:
    """An empty config file yields the model's field defaults."""
    path = _write_config(tmp_path, {})

    settings = load_config(Settings, path)

    assert settings.file_hub_base_url == "http://localhost:8080"
    assert settings.headless is True
    assert settings.default_timeout_ms == 30_000
    assert settings.credential_fill_timeout_ms == 5_000
    assert settings.bw_device_type == 0
    assert settings.bw_device_identifier == "robotsix-browser"
    assert settings.bw_device_name == "robotsix-browser"
    # Blank secrets / URLs are the "not configured" sentinel.
    assert settings.bw_server_url == ""
    assert settings.bw_client_id.get_secret_value() == ""
    assert settings.bw_master_password.get_secret_value() == ""


def test_partial_config_merges_with_defaults(tmp_path: Path) -> None:
    """Fields omitted from a partial file keep their model defaults."""
    path = _write_config(tmp_path, {"headless": False, "bw_email": "a@b.co"})

    settings = load_config(Settings, path)

    assert settings.headless is False
    assert settings.bw_email == "a@b.co"
    # Untouched fields still carry their defaults.
    assert settings.default_timeout_ms == 30_000
    assert settings.file_hub_base_url == "http://localhost:8080"


def test_secrets_masked_in_repr_and_str() -> None:
    """``SecretStr`` fields never expose their value in repr / str output."""
    settings = Settings(
        bw_client_id=SecretStr("client-id-secret"),
        bw_client_secret=SecretStr("client-secret-value"),
        bw_master_password=SecretStr("top-secret-master"),
    )

    rendered = f"{settings!r} {settings}"

    assert "client-id-secret" not in rendered
    assert "client-secret-value" not in rendered
    assert "top-secret-master" not in rendered
    # The masked placeholder is present instead of the raw value.
    assert "**********" in repr(settings.bw_master_password)


def test_invalid_field_type_raises(tmp_path: Path) -> None:
    """A non-coercible value for a typed field fails validation."""
    path = _write_config(tmp_path, {"default_timeout_ms": "not-a-number"})

    with pytest.raises(InvalidConfigError):
        load_config(Settings, path)


def test_invalid_json_raises(tmp_path: Path) -> None:
    """A malformed JSON config file is rejected, not silently ignored."""
    path = tmp_path / "config.json"
    path.write_text("{ this is not json", encoding="utf-8")

    with pytest.raises(InvalidConfigError):
        load_config(Settings, path)


def test_direct_construction_rejects_bad_types() -> None:
    """Constructing ``Settings`` directly still enforces field types."""
    with pytest.raises(ValidationError):
        Settings(default_timeout_ms="nope")  # type: ignore[arg-type]


def test_get_settings_reads_env_config_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``get_settings`` loads the file named by ``ROBOTSIX_CONFIG_FILE``."""
    path = _write_config(
        tmp_path,
        {"bw_email": "env@example.com", "default_timeout_ms": 12_345},
    )
    monkeypatch.setenv(CONFIG_FILE_ENV, str(path))

    settings = get_settings()

    assert isinstance(settings, Settings)
    assert settings.bw_email == "env@example.com"
    assert settings.default_timeout_ms == 12_345
