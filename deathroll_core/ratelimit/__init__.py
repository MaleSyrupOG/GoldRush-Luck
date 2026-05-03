"""In-memory fixed-window rate limiter.

Used by the slash commands that touch the economic frontier
(``/deposit``, ``/withdraw``) plus future admin / cashier
commands. The limiter is process-local: for v1 single-instance
deployment that's the right call; a Redis-backed implementation
is deferred to v1.x when (if) we run multiple bot replicas.

The implementation is a simple sliding window — we keep timestamps
of recent acquires per key and prune anything older than the
window on each call. Memory is bounded by the active key set
(stale keys get garbage-collected lazily).
"""

from __future__ import annotations

import time as _time
from collections import deque
from collections.abc import Hashable


class FixedWindowLimiter:
    """Allow at most ``capacity`` calls per ``window_seconds`` per key.

    The limiter is intentionally clock-injectable: callers pass
    ``now`` so the tests can drive deterministic timelines. In
    production the cog passes ``time.monotonic()``.

    ``capacity=1, window_seconds=60.0`` is the spec default for
    deposit / withdraw ticket creation.
    """

    def __init__(self, *, capacity: int, window_seconds: float) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be > 0")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be > 0")
        self._capacity = capacity
        self._window = window_seconds
        self._buckets: dict[Hashable, deque[float]] = {}

    def acquire(self, key: Hashable, *, now: float | None = None) -> bool:
        """Return True if the call is permitted and recorded; False otherwise.

        On True, the timestamp is appended to the key's bucket. On
        False, the bucket is left untouched (a denial does not
        count against the user).
        """
        now = now if now is not None else _time.monotonic()
        bucket = self._buckets.setdefault(key, deque())
        cutoff = now - self._window
        # Prune timestamps older than the window. ``deque`` makes
        # this O(k) where k is the number of stale entries, not the
        # total bucket size.
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if len(bucket) >= self._capacity:
            return False
        bucket.append(now)
        return True


__all__ = ["FixedWindowLimiter"]
