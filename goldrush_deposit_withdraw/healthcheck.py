"""Healthcheck script for the Docker HEALTHCHECK directive.

Run with: ``python -m goldrush_deposit_withdraw.healthcheck``

When fully implemented in Epic 4, the healthcheck will:

1. Open a short-lived asyncpg pool to Postgres using ``POSTGRES_DSN``.
2. Run ``SELECT 1`` with a 3-second timeout.
3. Exit 0 on success, 1 on any failure.

Until Epic 4 lands, this script exits 0 unconditionally so the container can
boot and the bot package can be exercised in tests.
"""

from __future__ import annotations

import sys


def main() -> int:
    """Placeholder healthcheck. Returns 0 until Epic 4 wires the DB probe."""
    return 0


if __name__ == "__main__":
    sys.exit(main())
