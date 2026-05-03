"""Background workers for the Deposit/Withdraw bot (Epic 8).

Each worker module exposes:

- ``tick(pool, bot, ...)`` — one synchronous pass. Pure-ish: takes a
  pool + a bot, returns a summary value. Easy to unit-test.
- A subclass of ``PeriodicWorker`` that wraps ``tick`` in a
  cancellable asyncio loop. Started from ``DwBot.on_ready`` and
  stopped from ``DwBot.close_pool``.

The split keeps the operational scheduling boilerplate
(``asyncio.create_task``, stop event, broad-except guard, idempotent
``start``) in one place — see ``_periodic.PeriodicWorker``. New
workers add a tick + a 5-line subclass; nothing more.

Per-story modules:

- ``ticket_timeout``      Story 8.1 — auto-cancel expired tickets.
- ``claim_idle``          Story 8.2 — auto-release idle claims.
- ``cashier_idle``        Story 8.3 — auto-offline idle cashiers.
- ``stats_aggregator``    Story 8.5 — recompute cashier stats.
- ``audit_chain_verifier`` Story 8.6 — walk the hash-chained log.

Story 8.4 (online cashiers embed updater) lives at
``deathroll_deposit_withdraw.cashiers.live_updater`` because it
predates this package (Story 4.5). Its semantics match the new base
class but we don't refactor working code.
"""
