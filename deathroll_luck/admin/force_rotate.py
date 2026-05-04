"""Discord-side stubs for the force-rotate admin commands.

Spec ref: Luck design §4.3, plan Story 3.6.

Two slash commands the admin uses to rotate user seeds in
response to a leak or routine ops:

- ``/admin force-rotate-seed user:@<u>``
  Rotates one user's fairness seed.

- ``/admin force-rotate-all``
  Rotates EVERY user's fairness seed in one go (after a 2FA
  modal — same UX as the D/W treasury operations).

These are STUBS for v1 — the actual cog wiring lands in
Epic 11 (admin commands). The CLI counterpart at
``ops/scripts/force_rotate.py`` is fully functional now and is
the canonical incident-response path until Epic 11 closes.
"""

from __future__ import annotations


async def force_rotate_seed_handler(
    *,
    discord_id: int,
    admin_actor_id: int,
    reason: str = "admin force-rotation",
) -> str:
    """Stub for ``/admin force-rotate-seed``.

    Wired in Epic 11 once the Luck bot's ``deathroll_luck.cogs.admin``
    cog exists. The implementation will mirror
    ``ops/scripts/force_rotate.py``'s ``_rotate_one`` helper:
    SDF call + audit-log row.
    """
    raise NotImplementedError(
        "force_rotate_seed_handler is a Story 3.6 stub. "
        "Wire to the asyncpg pool + audit-log helper in Epic 11. "
        "Use ops/scripts/force_rotate.py meanwhile for incident response."
    )


async def force_rotate_all_handler(
    *,
    admin_actor_id: int,
    reason: str = "admin force-rotation (all)",
) -> int:
    """Stub for ``/admin force-rotate-all``. See above."""
    raise NotImplementedError(
        "force_rotate_all_handler is a Story 3.6 stub. "
        "Wired in Epic 11. Use ops/scripts/force_rotate.py meanwhile."
    )
