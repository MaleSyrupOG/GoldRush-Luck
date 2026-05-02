"""Settings classes for the GoldRush bots.

Settings are loaded from environment variables (the canonical source
in production) with optional ``.env`` file support for local dev.
``pydantic-settings`` enforces the type contract: a missing required
field is a startup error, not a silent runtime crash.

Layout:

- ``CoreSettings`` — fields shared by every GoldRush bot (DSN, log
  config, signing keys). Imported and extended by per-bot settings.
- ``DwSettings`` — the Deposit/Withdraw bot. Adds ``discord_token``
  and ``guild_id``.

The Luck bot will get its own ``LuckSettings`` extending the same
core when Luck resumes.

Secrets (``discord_token``, ``postgres_dsn``, signing keys) are
typed as ``SecretStr`` so they do not leak through ``repr`` or
``model_dump`` — defensive against accidental log lines and
exception traceback dumps.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class CoreSettings(BaseSettings):
    """Fields every bot needs.

    Local dev typically populates these from a ``.env`` file in the
    repo root or via ``ENV_DIR=/path/to/secrets`` + sourced shell.
    Production loads them from ``/opt/goldrush/secrets/.env.shared``
    via Docker Compose's ``env_file`` directive.
    """

    model_config = SettingsConfigDict(
        env_file=(".env.shared", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    postgres_dsn: SecretStr
    log_level: str = Field(default="INFO")
    log_format: Literal["json", "console"] = Field(default="json")
    button_signing_key: SecretStr | None = Field(default=None)
    audit_hash_chain_key: SecretStr | None = Field(default=None)


class DwSettings(CoreSettings):
    """Settings for the Deposit/Withdraw bot.

    Extends ``CoreSettings`` with the Discord-side fields. The
    ``env_file`` tuple is overridden so D/W also reads
    ``.env.dw`` (which carries ``DISCORD_TOKEN`` and ``GUILD_ID``).
    Missing fields raise ``ValidationError`` at startup.
    """

    model_config = SettingsConfigDict(
        env_file=(".env.shared", ".env.dw", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    discord_token: SecretStr
    guild_id: int


__all__ = ["CoreSettings", "DwSettings"]
