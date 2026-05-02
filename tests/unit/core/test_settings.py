"""Unit tests for `goldrush_core.config.DwSettings`.

Settings load from environment variables (and from .env files in dev).
These tests validate the type contract: required fields, defaults,
and the validation of constrained values (log_format, guild_id).
"""

from __future__ import annotations

import pytest
from goldrush_core.config import DwSettings
from pydantic import SecretStr, ValidationError


def _good_env() -> dict[str, str]:
    return {
        "DISCORD_TOKEN": "dummy.token.string",
        "GUILD_ID": "1234567890",
        "POSTGRES_DSN": "postgresql://goldrush_dw:secret@localhost:5432/goldrush",
    }


def test_loads_required_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in _good_env().items():
        monkeypatch.setenv(k, v)
    s = DwSettings()
    assert isinstance(s.discord_token, SecretStr)
    assert s.guild_id == 1234567890
    assert isinstance(s.postgres_dsn, SecretStr)


def test_default_log_level_is_info(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in _good_env().items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    s = DwSettings()
    assert s.log_level == "INFO"


def test_default_log_format_is_json(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in _good_env().items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("LOG_FORMAT", raising=False)
    s = DwSettings()
    assert s.log_format == "json"


def test_log_format_console_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in _good_env().items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("LOG_FORMAT", "console")
    s = DwSettings()
    assert s.log_format == "console"


def test_log_format_invalid_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in _good_env().items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("LOG_FORMAT", "yaml")
    with pytest.raises(ValidationError):
        DwSettings()


def test_missing_discord_token_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)
    monkeypatch.setenv("GUILD_ID", "1")
    monkeypatch.setenv("POSTGRES_DSN", "postgresql://x@y/z")
    with pytest.raises(ValidationError):
        DwSettings()


def test_missing_guild_id_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "x")
    monkeypatch.delenv("GUILD_ID", raising=False)
    monkeypatch.setenv("POSTGRES_DSN", "postgresql://x@y/z")
    with pytest.raises(ValidationError):
        DwSettings()


def test_guild_id_must_be_integer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "x")
    monkeypatch.setenv("GUILD_ID", "not-a-number")
    monkeypatch.setenv("POSTGRES_DSN", "postgresql://x@y/z")
    with pytest.raises(ValidationError):
        DwSettings()


def test_secret_str_does_not_leak_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    """The bot token and DSN are SecretStr; their values must not appear
    in the default repr — defensive against accidental log lines."""
    for k, v in _good_env().items():
        monkeypatch.setenv(k, v)
    s = DwSettings()
    rendered = repr(s)
    assert "dummy.token.string" not in rendered
    assert "secret" not in rendered  # the DSN password
