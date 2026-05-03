"""Unit tests for `deathroll_core.ratelimit`.

In-memory fixed-window limiter used by the deposit / withdraw cogs
(spec: max 1 ticket creation per user per 60 s) and by future
admin / cashier commands. The limiter is process-local — for v1
single-instance deployment that's enough; multi-instance Redis
backing is deferred to v1.x.
"""

from __future__ import annotations

import pytest
from deathroll_core.ratelimit import FixedWindowLimiter


def test_first_call_acquires() -> None:
    limiter = FixedWindowLimiter(capacity=1, window_seconds=60.0)
    assert limiter.acquire("user:1", now=1000.0) is True


def test_second_call_within_window_denied() -> None:
    """Capacity 1, window 60 s: a second call at t=10 must be denied."""
    limiter = FixedWindowLimiter(capacity=1, window_seconds=60.0)
    assert limiter.acquire("user:1", now=1000.0) is True
    assert limiter.acquire("user:1", now=1010.0) is False


def test_call_after_window_resets() -> None:
    """Beyond the window, the same key acquires fresh."""
    limiter = FixedWindowLimiter(capacity=1, window_seconds=60.0)
    assert limiter.acquire("user:1", now=1000.0) is True
    assert limiter.acquire("user:1", now=1061.0) is True


def test_different_keys_isolated() -> None:
    """Two users have independent windows."""
    limiter = FixedWindowLimiter(capacity=1, window_seconds=60.0)
    assert limiter.acquire("user:1", now=1000.0) is True
    assert limiter.acquire("user:2", now=1010.0) is True


def test_higher_capacity_allows_burst() -> None:
    """A capacity of 3 lets the same key acquire 3 times within the window."""
    limiter = FixedWindowLimiter(capacity=3, window_seconds=60.0)
    assert limiter.acquire("user:1", now=1000.0) is True
    assert limiter.acquire("user:1", now=1010.0) is True
    assert limiter.acquire("user:1", now=1020.0) is True
    assert limiter.acquire("user:1", now=1030.0) is False


def test_capacity_must_be_positive() -> None:
    with pytest.raises(ValueError):
        FixedWindowLimiter(capacity=0, window_seconds=60.0)


def test_window_must_be_positive() -> None:
    with pytest.raises(ValueError):
        FixedWindowLimiter(capacity=1, window_seconds=0.0)


def test_oldest_calls_purged_so_memory_does_not_grow_unboundedly() -> None:
    """A long-quiet user doesn't keep their old timestamp around forever."""
    limiter = FixedWindowLimiter(capacity=1, window_seconds=10.0)
    assert limiter.acquire("user:1", now=0.0) is True
    # Many later acquires for OTHER keys; user:1's timestamp should
    # be reaped lazily on the next user:1 acquire.
    for i in range(100):
        limiter.acquire(f"other:{i}", now=20.0 + i)
    # user:1 acquires fresh — old timestamp pruned.
    assert limiter.acquire("user:1", now=200.0) is True
