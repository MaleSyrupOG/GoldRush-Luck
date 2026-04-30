"""Discord client subclass for the GoldRush Deposit/Withdraw bot.

Concrete implementation lands in Epic 4 (bot skeleton) of the implementation
plan. The eventual ``Bot`` subclass will be responsible for:

- Loading cogs from a manifest list (deposit, withdraw, ticket, cashier, admin,
  account).
- Initialising the DB pool against the ``goldrush_dw`` Postgres role.
- Starting the lifecycle of background workers (ticket timeouts, cashier idle,
  online-cashiers embed updater, stats aggregator, audit chain verifier).
- Per-guild slash command sync at startup.

This module is intentionally empty for now so the package imports cleanly.
"""

from __future__ import annotations
