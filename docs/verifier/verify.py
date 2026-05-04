#!/usr/bin/env python3
"""DeathRoll Luck — public Python verifier.

Zero-dependency, single-file implementation of the bot's
provably-fair algorithm. Any user can clone this script and
re-derive the outcome of any past bet locally to confirm the
casino did not tamper.

The algorithm:

    out = HMAC-SHA512(
        key=server_seed,
        message=f"{client_seed}:{nonce}".encode("utf-8"),
    )

For games that need more than 64 bytes (Blackjack's full deck
shuffle of decks*52 cards), the byte stream is extended
deterministically via:

    chunk_n = SHA-256(out || n.to_bytes(4, 'big'))   for n = 1, 2, ...

This file is the canonical reference. The bot's
``deathroll_core/fairness/`` package implements the same
algorithm and CI cross-checks them byte-for-byte against this
script and against ``verify.js``.

Usage:

    python verify.py <game> <server_seed_hex> <client_seed> <nonce> [extra...]

Examples:

    python verify.py coinflip d4...3fb cs 0
    python verify.py mines    cafe...    cs 0 3 25
    python verify.py blackjack cafe...   cs 0 6
    python verify.py staking  cafe...    cs 0 5
    python verify.py raffle   cafe...    cs 0 100

Output is JSON on stdout.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sys
from typing import Any

# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


def compute(server_seed: bytes, client_seed: str, nonce: int) -> bytes:
    """HMAC-SHA512(server_seed, f'{client_seed}:{nonce}'). 64 bytes."""
    msg = f"{client_seed}:{nonce}".encode()
    return hmac.new(server_seed, msg, hashlib.sha512).digest()


def extend(out: bytes, byte_count: int) -> bytes:
    """Extend out via SHA-256 chain to byte_count bytes."""
    if byte_count <= len(out):
        return out[:byte_count]
    extended = bytearray(out)
    counter = 0
    while len(extended) < byte_count:
        counter += 1
        chunk = hashlib.sha256(out + counter.to_bytes(4, "big")).digest()
        extended.extend(chunk)
    return bytes(extended[:byte_count])


# ---------------------------------------------------------------------------
# Byte-stream + Fisher-Yates helpers (mirror deathroll_core/fairness/decoders)
# ---------------------------------------------------------------------------


def _byte_stream(out: bytes):
    """Lazy iterator: out + SHA-256(out || counter) chain."""
    yield from out
    counter = 0
    while True:
        counter += 1
        chunk = hashlib.sha256(out + counter.to_bytes(4, "big")).digest()
        yield from chunk


def _pull_uint(stream, width: int) -> int:
    value = 0
    for _ in range(width):
        value = (value << 8) | next(stream)
    return value


def _fisher_yates_partial(
    stream, n: int, k: int, *, pick_width: int = 4
) -> list[int]:
    """First-k of FY shuffle of [0, n)."""
    arr = list(range(n))
    bound = max(1, n - k)
    for i in range(n - 1, bound - 1, -1):
        j = _pull_uint(stream, pick_width) % (i + 1)
        arr[i], arr[j] = arr[j], arr[i]
    return arr[n - k :][::-1]


# ---------------------------------------------------------------------------
# Decoders
# ---------------------------------------------------------------------------


def decode_coinflip(out: bytes) -> str:
    return "heads" if (out[0] & 1) == 0 else "tails"


def decode_dice(out: bytes) -> float:
    n = int.from_bytes(out[:4], "big")
    return (n % 10000) / 100


def decode_99x(out: bytes) -> int:
    return (out[0] % 100) + 1


def decode_hotcold(out: bytes) -> str:
    n = int.from_bytes(out[:2], "big") % 10000
    if n < 500:
        return "rainbow"
    if n < 5250:
        return "hot"
    return "cold"


def decode_roulette_eu(out: bytes) -> int:
    return int.from_bytes(out[:2], "big") % 37


def decode_mines_positions(
    out: bytes, mines_count: int, grid_size: int
) -> list[int]:
    if not (1 <= mines_count < grid_size):
        raise ValueError(
            f"mines_count out of range: {mines_count} "
            f"(must be in [1, {grid_size - 1}])"
        )
    stream = _byte_stream(out)
    return _fisher_yates_partial(stream, n=grid_size, k=mines_count)


def decode_blackjack_deck(out: bytes, decks: int) -> list[int]:
    if decks <= 0:
        raise ValueError(f"decks must be positive: {decks}")
    cards = [c for c in range(52) for _ in range(decks)]
    stream = _byte_stream(out)
    n = len(cards)
    for i in range(n - 1, 0, -1):
        j = _pull_uint(stream, 4) % (i + 1)
        cards[i], cards[j] = cards[j], cards[i]
    return cards


def decode_dice_duel(out: bytes) -> tuple[int, int]:
    return ((out[0] % 12) + 1, (out[1] % 12) + 1)


def decode_staking_duel(out: bytes, max_rounds: int) -> list[dict[str, int]]:
    if max_rounds <= 0:
        raise ValueError(f"max_rounds must be positive: {max_rounds}")
    stream = _byte_stream(out)
    rounds: list[dict[str, int]] = []
    for _ in range(max_rounds):
        p = (next(stream) % 12) + 1
        b = (next(stream) % 12) + 1
        rounds.append({"player_roll": p, "bot_roll": b})
    return rounds


def decode_raffle_winners(out: bytes, ticket_count: int) -> list[int]:
    if ticket_count < 3:
        raise ValueError(
            f"need at least 3 tickets for 3 winners; got {ticket_count}"
        )
    stream = _byte_stream(out)
    return _fisher_yates_partial(stream, n=ticket_count, k=3)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def _decode(game: str, out: bytes, args: list[str]) -> Any:
    if game == "coinflip":
        return decode_coinflip(out)
    if game == "dice":
        return decode_dice(out)
    if game == "99x":
        return decode_99x(out)
    if game == "hotcold":
        return decode_hotcold(out)
    if game == "roulette":
        return decode_roulette_eu(out)
    if game == "diceduel":
        return list(decode_dice_duel(out))
    if game == "mines":
        if len(args) != 2:
            raise SystemExit("mines: <mines_count> <grid_size>")
        return decode_mines_positions(out, int(args[0]), int(args[1]))
    if game == "blackjack":
        if len(args) != 1:
            raise SystemExit("blackjack: <decks>")
        return decode_blackjack_deck(out, int(args[0]))
    if game == "staking":
        if len(args) != 1:
            raise SystemExit("staking: <max_rounds>")
        return decode_staking_duel(out, int(args[0]))
    if game == "raffle":
        if len(args) != 1:
            raise SystemExit("raffle: <ticket_count>")
        return decode_raffle_winners(out, int(args[0]))
    raise SystemExit(f"unknown game: {game}")


def main() -> int:
    if len(sys.argv) < 5:
        print(
            "Usage: verify.py <game> <server_seed_hex> <client_seed> <nonce> "
            "[extra_args...]",
            file=sys.stderr,
        )
        return 2
    game = sys.argv[1]
    server_seed = bytes.fromhex(sys.argv[2])
    client_seed = sys.argv[3]
    nonce = int(sys.argv[4])
    extra = sys.argv[5:]

    head = compute(server_seed, client_seed, nonce)
    # Extend liberally — most decoders don't need it, blackjack needs ~1244.
    extended = extend(head, 4096)
    outcome = _decode(game, extended, extra)
    json.dump(outcome, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
