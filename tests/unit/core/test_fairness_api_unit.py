"""Story 3.4 — unit-level guards for the Fairness API.

Pure-Python checks on ``deathroll_core.fairness.api`` that don't
need a Postgres container:

- The ``FairnessTicket`` model carries only public fields (no
  raw ``server_seed`` field by structural design).
- The module's source contains no log/print line that mentions
  the literal ``server_seed`` variable name (with
  ``server_seed_hash`` whitelisted).
- The internal ``_extend`` helper extends bytes deterministically
  via the same SHA-256 chain the decoders use.

The integration-level tests live in
``tests/integration/luck/test_fairness_api.py``.
"""

from __future__ import annotations

import hashlib
import re

from deathroll_core.fairness.api import FairnessTicket, _extend


def test_fairness_ticket_has_only_public_fields() -> None:
    fields = set(FairnessTicket.model_fields)
    assert "server_seed" not in fields
    assert fields == {"hmac_bytes", "server_seed_hash", "client_seed", "nonce"}


def test_fairness_ticket_is_frozen() -> None:
    """Tickets are immutable (frozen) so cogs can't mutate state
    accidentally between draw and resolve."""
    ticket = FairnessTicket(
        hmac_bytes=b"\x00" * 64,
        server_seed_hash=b"\x00" * 32,
        client_seed="cs",
        nonce=0,
    )
    import pydantic

    try:
        ticket.nonce = 7  # type: ignore[misc]
    except (pydantic.ValidationError, TypeError, AttributeError):
        pass
    else:
        raise AssertionError(
            "FairnessTicket should be frozen — mutation must raise"
        )


def test_extend_short_returns_truncated_input() -> None:
    """When byte_count < len(out), _extend just truncates."""
    out = b"\x01\x02\x03\x04\x05\x06\x07\x08"
    assert _extend(out, 3) == b"\x01\x02\x03"


def test_extend_exact_returns_input() -> None:
    out = b"a" * 64
    assert _extend(out, 64) == out


def test_extend_long_uses_sha256_chain() -> None:
    """When byte_count > len(out), _extend uses SHA-256(out ||
    counter.to_bytes(4, 'big')) starting at counter=1."""
    out = b"\xab" * 64
    extended = _extend(out, 96)
    assert len(extended) == 96
    assert extended[:64] == out
    expected_chunk1 = hashlib.sha256(out + (1).to_bytes(4, "big")).digest()
    assert extended[64:96] == expected_chunk1


def test_extend_chain_progresses_through_counter() -> None:
    """Counter increments 1, 2, 3, ... per chunk."""
    out = b"\x42" * 64
    extended = _extend(out, 192)  # head + 4 SHA-256 chunks
    assert len(extended) == 192
    assert extended[:64] == out
    for i, expected_counter in enumerate((1, 2, 3, 4), start=0):
        chunk = hashlib.sha256(
            out + expected_counter.to_bytes(4, "big")
        ).digest()
        start = 64 + i * 32
        assert extended[start : start + 32] == chunk


def test_api_module_does_not_log_server_seed() -> None:
    """Audit constraint mirroring seeds.py: no log line in api.py
    mentions the literal ``server_seed`` variable name (only
    ``server_seed_hash`` is allowed in any context)."""
    import deathroll_core.fairness.api as api_module

    src = _get_source(api_module)
    log_call_re = re.compile(
        r"(log(ger)?\.[a-zA-Z_]+\(|print\(|structlog\.|self\.log\.)"
    )
    for n, line in enumerate(src.splitlines(), start=1):
        if log_call_re.search(line):
            stripped = line.replace("server_seed_hash", "")
            assert "server_seed" not in stripped, (
                f"api.py:{n} appears to log the raw server_seed: {line!r}"
            )


def _get_source(module: object) -> str:
    import inspect

    return inspect.getsource(module)
