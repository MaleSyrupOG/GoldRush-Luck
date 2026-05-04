"""Per-game outcome decoders.

Spec ref: Luck design §4.4.

Each decoder is a pure function that takes the HMAC-SHA512 output
bytes (and any game-specific args) and returns the game's
outcome. No I/O, no global state.

For games that need more than 64 bytes (Blackjack's full deck
shuffle of decks*52 cards), we extend the byte stream
deterministically via a SHA-256 chain:

    chunk_n = SHA-256(out || n.to_bytes(4, 'big'))   for n = 1, 2, ...

The verifier (Story 3.5) implements the same chain so a third
party can reproduce any past outcome from the published
``server_seed`` + ``client_seed`` + ``nonce``.

Module imports only stdlib ``hashlib`` (for the chain extension)
and ``dataclasses`` (for the StakingRound row type) plus typing.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal

# ---------------------------------------------------------------------------
# Byte-stream helper — lazy, optionally extended via SHA-256 chain
# ---------------------------------------------------------------------------


def _byte_stream(out: bytes) -> Iterator[int]:
    """Lazy iterator over ``out`` followed by the SHA-256 chain
    extension. Yields one int (0-255) at a time. Never terminates;
    callers pull only as many bytes as they need.
    """
    yield from out
    counter = 0
    while True:
        counter += 1
        chunk = hashlib.sha256(out + counter.to_bytes(4, "big")).digest()
        yield from chunk


def _pull_uint(stream: Iterator[int], width: int) -> int:
    """Pull ``width`` bytes from ``stream`` as a big-endian unsigned int."""
    value = 0
    for _ in range(width):
        value = (value << 8) | next(stream)
    return value


def _fisher_yates_partial(
    stream: Iterator[int], n: int, k: int, *, pick_width: int = 4
) -> list[int]:
    """Partial Fisher-Yates on the array ``[0, 1, ..., n - 1]``;
    returns the first ``k`` elements after shuffling.

    Pulls ``pick_width`` bytes per swap from ``stream``. The
    standard FY sweep runs from i = n-1 down to i = 1, swapping
    arr[i] with arr[j] where j = uint(stream) % (i + 1). For a
    partial shuffle, we only need to perform swaps until the first
    ``k`` slots are settled — i.e., from i = n - 1 down to i = n - k.

    The resulting first-k elements are uniformly drawn without
    replacement from [0, n) (modulo the negligible bias of the
    modular sample with pick_width >= 4).
    """
    if not (0 < k <= n):
        raise ValueError(f"k out of range: k={k} n={n}")
    arr = list(range(n))
    # Run FY from the top down, but stop once the top-k slots are
    # finalised. The first slot the partial shuffle settles is
    # arr[n-1]; we keep going until arr[n-k] is settled.
    bound = max(1, n - k)
    for i in range(n - 1, bound - 1, -1):
        j = _pull_uint(stream, pick_width) % (i + 1)
        arr[i], arr[j] = arr[j], arr[i]
    # The settled slots are arr[n-k:n]; reverse so the FIRST element
    # is the first selected item.
    return arr[n - k :][::-1]


def _fisher_yates_full(
    stream: Iterator[int], n: int, *, pick_width: int = 4
) -> list[int]:
    """Full Fisher-Yates of ``[0, n - 1]``."""
    arr = list(range(n))
    for i in range(n - 1, 0, -1):
        j = _pull_uint(stream, pick_width) % (i + 1)
        arr[i], arr[j] = arr[j], arr[i]
    return arr


# ---------------------------------------------------------------------------
# decode_coinflip
# ---------------------------------------------------------------------------


def decode_coinflip(out: bytes) -> Literal["heads", "tails"]:
    """``heads`` if ``out[0] & 1 == 0`` else ``tails``. Spec §4.4."""
    return "heads" if (out[0] & 1) == 0 else "tails"


# ---------------------------------------------------------------------------
# decode_dice
# ---------------------------------------------------------------------------


def decode_dice(out: bytes) -> float:
    """``(int.from_bytes(out[:4], 'big') % 10000) / 100`` → ``[0.00, 99.99]``."""
    n = int.from_bytes(out[:4], "big")
    return (n % 10000) / 100


# ---------------------------------------------------------------------------
# decode_99x
# ---------------------------------------------------------------------------


def decode_99x(out: bytes) -> int:
    """``(out[0] % 100) + 1`` → ``[1, 100]``."""
    return (out[0] % 100) + 1


# ---------------------------------------------------------------------------
# decode_hotcold
# ---------------------------------------------------------------------------


def decode_hotcold(out: bytes) -> Literal["hot", "cold", "rainbow"]:
    """First 2 bytes mod 10000 → < 500 rainbow / < 5250 hot / else cold."""
    n = int.from_bytes(out[:2], "big") % 10000
    if n < 500:
        return "rainbow"
    if n < 5250:
        return "hot"
    return "cold"


# ---------------------------------------------------------------------------
# decode_roulette_eu
# ---------------------------------------------------------------------------


def decode_roulette_eu(out: bytes) -> int:
    """First 2 bytes mod 37 → ``[0, 36]`` (European single-zero)."""
    return int.from_bytes(out[:2], "big") % 37


# ---------------------------------------------------------------------------
# decode_mines_positions
# ---------------------------------------------------------------------------


def decode_mines_positions(
    out: bytes, *, mines_count: int, grid_size: int
) -> list[int]:
    """Partial Fisher-Yates of ``[0, grid_size)``; returns the first
    ``mines_count`` indices as the bomb positions.

    Args:
        out: HMAC-SHA512 output (64 bytes).
        mines_count: number of bombs to place; must satisfy
            ``1 <= mines_count < grid_size``.
        grid_size: total cells in the grid (default Mines is 25).

    Returns:
        ``mines_count`` distinct cell indices in ``[0, grid_size)``.

    Raises:
        ValueError: if ``mines_count`` is out of range.
    """
    if not (1 <= mines_count < grid_size):
        raise ValueError(
            f"mines_count out of range: {mines_count} (must be in "
            f"[1, {grid_size - 1}])"
        )
    stream = _byte_stream(out)
    return _fisher_yates_partial(
        stream, n=grid_size, k=mines_count, pick_width=4
    )


# ---------------------------------------------------------------------------
# decode_blackjack_deck
# ---------------------------------------------------------------------------


def decode_blackjack_deck(out: bytes, *, decks: int) -> list[int]:
    """Full Fisher-Yates of a multi-deck shoe.

    Cards are encoded ``0..51`` (suit = ``i // 13``, rank = ``i % 13``).
    For ``decks=N``, the input list is ``[0..51] * N`` (so each card
    appears N times); the FY shuffle returns one of the (52*N)!/N!^52
    distinguishable permutations.

    Args:
        out: HMAC-SHA512 output (64 bytes).
        decks: number of 52-card decks in the shoe; must be > 0.

    Returns:
        A list of length ``decks * 52``.

    Raises:
        ValueError: if ``decks <= 0``.
    """
    if decks <= 0:
        raise ValueError(f"decks must be positive: {decks}")
    cards = [c for c in range(52) for _ in range(decks)]
    stream = _byte_stream(out)
    n = len(cards)
    for i in range(n - 1, 0, -1):
        j = _pull_uint(stream, 4) % (i + 1)
        cards[i], cards[j] = cards[j], cards[i]
    return cards


# ---------------------------------------------------------------------------
# decode_dice_duel
# ---------------------------------------------------------------------------


def decode_dice_duel(out: bytes) -> tuple[int, int]:
    """``(out[0] % 12 + 1, out[1] % 12 + 1)`` → two rolls in ``[1, 12]``."""
    return ((out[0] % 12) + 1, (out[1] % 12) + 1)


# ---------------------------------------------------------------------------
# decode_staking_duel
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StakingRound:
    """One round of a Staking Duel.

    Each side rolls a d12 (``[1, 12]``); the resolver compares the
    rolls to compute HP / damage / round winner. The decoder
    pre-computes the rolls so the entire bet's randomness is
    locked in at apply-bet time.
    """

    player_roll: int
    bot_roll: int


def decode_staking_duel(out: bytes, *, max_rounds: int) -> list[StakingRound]:
    """Pre-compute ``max_rounds`` staking-duel rounds.

    Each round consumes 2 bytes (one for each player). Rolls are
    in ``[1, 12]``.

    Args:
        out: HMAC-SHA512 output (64 bytes).
        max_rounds: number of rounds to pre-roll.

    Returns:
        A list of ``max_rounds`` ``StakingRound`` instances.

    Raises:
        ValueError: if ``max_rounds <= 0``.
    """
    if max_rounds <= 0:
        raise ValueError(f"max_rounds must be positive: {max_rounds}")
    stream = _byte_stream(out)
    rounds: list[StakingRound] = []
    for _ in range(max_rounds):
        p = (next(stream) % 12) + 1
        b = (next(stream) % 12) + 1
        rounds.append(StakingRound(player_roll=p, bot_roll=b))
    return rounds


# ---------------------------------------------------------------------------
# decode_raffle_winners
# ---------------------------------------------------------------------------


def decode_raffle_winners(out: bytes, *, ticket_count: int) -> list[int]:
    """Pick the first 3 winners via partial Fisher-Yates.

    Args:
        out: HMAC-SHA512 output (64 bytes).
        ticket_count: number of tickets in the pool; must be >= 3.

    Returns:
        Three distinct ticket indices in ``[0, ticket_count)``.

    Raises:
        ValueError: if ``ticket_count < 3``.
    """
    if ticket_count < 3:
        raise ValueError(
            f"need at least 3 tickets for 3 winners; got {ticket_count}"
        )
    stream = _byte_stream(out)
    return _fisher_yates_partial(stream, n=ticket_count, k=3, pick_width=4)
