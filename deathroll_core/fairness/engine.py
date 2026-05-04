"""HMAC-SHA512 engine — the canonical randomness primitive.

Spec ref: Luck design §4.1.

Every game's outcome derives from one call to ``compute``:

    out = HMAC-SHA512(
        key=server_seed,
        message=f"{client_seed}:{nonce}".encode("utf-8"),
    )

The 64-byte output is then sliced + decoded according to the game
(see ``deathroll_core.fairness.decoders``).

This module is deliberately tiny and audit-friendly:

- Imports only stdlib ``hmac`` + ``hashlib``. No third-party deps.
- ``compute`` is a pure function (no I/O, no global state, no
  side effects).
- The message format is FROZEN. Changing the format (e.g. adding
  a separator, padding the nonce, hashing differently) would
  break every previously-recorded bet's verifiability — strictly
  forbidden after launch.
- A 64-byte return is guaranteed by SHA-512's output size.

Pinned by ``tests/unit/core/test_fairness_engine.py`` against
10 known-good vectors and a property test that cross-checks
against the reference ``hmac.new(...).digest()`` on 100 random
inputs.
"""

from __future__ import annotations

import hashlib
import hmac


def compute(server_seed: bytes, client_seed: str, nonce: int) -> bytes:
    """Compute HMAC-SHA512 over the canonical fairness message.

    Args:
        server_seed: The 32-byte server-side secret. Sensitive;
            should never appear in logs, embeds, or error messages.
        client_seed: The user-controllable string; default is a
            16-char hex on first registration but the user can set
            it via ``/setseed``.
        nonce: The monotonic per-user counter; advanced exactly
            once per bet (atomic in ``fairness.next_nonce`` SDF).

    Returns:
        Exactly 64 bytes — the HMAC-SHA512 digest.

    Raises:
        TypeError: if ``server_seed`` is not bytes or ``client_seed``
            is not str. The standard library handles these.

    The function is fully deterministic: same inputs always yield
    the same output, with no hidden state.
    """
    message = f"{client_seed}:{nonce}".encode()
    return hmac.new(server_seed, message, hashlib.sha512).digest()
