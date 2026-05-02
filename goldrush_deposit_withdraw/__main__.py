"""Entry point for the GoldRush Deposit/Withdraw bot.

Run with: ``python -m goldrush_deposit_withdraw``

Boot order (per spec §5.7):

1. Load settings from environment / .env files (validation errors here
   surface as a startup crash with a typed message rather than a silent
   misconfig).
2. Configure structlog with the requested level + format.
3. Build the ``DwBot`` and wire its DB pool.
4. ``bot.start(token)`` — opens the gateway and runs forever.

Steps 4-6 of the spec (sync command tree, ensure system row,
ensure dynamic embeds, start workers) land in subsequent stories.

Test note: the existing smoke tests for the placeholder ``main()``
were removed in Story 4.1 because the new ``main()`` does not return
without a Discord token; the structural contract is now covered by
``tests/unit/dw/test_client.py``.
"""

from __future__ import annotations

import asyncio
import sys

import structlog
from goldrush_core.config import DwSettings
from goldrush_core.logging import setup_logging

from goldrush_deposit_withdraw.client import DwBot, build_bot

_log = structlog.get_logger(__name__)


def main() -> int:
    """Bin entry. Loads settings, configures logging, runs the bot.

    Returns 0 on clean shutdown (SIGTERM/SIGINT). Any other exit path
    (uncaught exception, login failure) propagates as a non-zero exit
    so Docker's ``restart: unless-stopped`` policy can react.
    """
    settings = DwSettings()
    setup_logging(settings.log_level, format=settings.log_format)
    _log.info(
        "boot",
        guild_id=settings.guild_id,
        log_format=settings.log_format,
        log_level=settings.log_level,
    )
    bot = build_bot(settings)
    asyncio.run(_run(bot, settings))
    return 0


async def _run(bot: DwBot, settings: DwSettings) -> None:
    """Run the bot until shutdown; ensure the DB pool is closed cleanly."""
    try:
        async with bot:
            await bot.start(settings.discord_token.get_secret_value())
    finally:
        await bot.close_pool()


if __name__ == "__main__":
    sys.exit(main())
