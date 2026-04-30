"""Entry point for the GoldRush Deposit/Withdraw bot.

Run with: ``python -m goldrush_deposit_withdraw``

The full implementation lands in Epic 4 (bot skeleton) of the implementation
plan. Until then, this file behaves as a long-running placeholder so that
container orchestration treats it as an alive process and `restart:
unless-stopped` does not crash-loop.

Behaviour:
- When invoked directly under ``__main__`` (e.g. inside the Docker container),
  the process logs that the placeholder is running and waits indefinitely on a
  threading event, exiting 0 cleanly when SIGTERM/SIGINT is received.
- When the ``main()`` function is called explicitly from tests, it returns
  immediately with 0 so the smoke test suite stays fast.

See: docs/superpowers/specs/2026-04-29-goldrush-dw-v1-implementation-plan.md
"""

from __future__ import annotations

import signal
import sys
import threading


def main() -> int:
    """Placeholder entry point invoked from tests. Returns 0 immediately."""
    return 0


def _serve_forever() -> int:
    """Long-running placeholder used when launched as ``python -m goldrush_deposit_withdraw``.

    Blocks until SIGTERM or SIGINT is received, then exits 0. This keeps
    container orchestration happy until Epic 4 replaces this with the real
    discord.py client.
    """
    print(
        "[goldrush_deposit_withdraw] placeholder process running; "
        "Epic 4 will replace this with the real bot.",
        file=sys.stderr,
        flush=True,
    )
    stop = threading.Event()

    def _handle_signal(signum: int, _frame: object) -> None:
        print(
            f"[goldrush_deposit_withdraw] received signal {signum}, exiting cleanly.",
            file=sys.stderr,
            flush=True,
        )
        stop.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    stop.wait()
    return 0


if __name__ == "__main__":
    raise SystemExit(_serve_forever())
