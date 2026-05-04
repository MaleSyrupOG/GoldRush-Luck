"""Story 3.3 — Per-game decoders.

Spec ref: Luck design §4.4.

Each decoder is a pure function: bytes (HMAC output, or longer
via SHA-256 chain extension) → game outcome. No I/O, no global
state, no mutation. Inputs that are valid produce outputs in the
documented range; inputs are deterministic across calls.

This file pins the contract (ranges, lengths, determinism, no-
duplicates for shuffles). Cross-checking the per-byte output
against the verifier's reference happens in Story 3.5 via
``test_vectors.json``.
"""

from __future__ import annotations

import secrets

import pytest
from deathroll_core.fairness.decoders import (
    StakingRound,
    decode_99x,
    decode_blackjack_deck,
    decode_coinflip,
    decode_dice,
    decode_dice_duel,
    decode_hotcold,
    decode_mines_positions,
    decode_raffle_winners,
    decode_roulette_eu,
    decode_staking_duel,
)
from hypothesis import given, settings
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# decode_coinflip
# ---------------------------------------------------------------------------


def test_coinflip_uses_first_byte_lsb() -> None:
    """heads if out[0] & 1 == 0 else tails (per spec §4.4)."""
    assert decode_coinflip(b"\x00" + b"\x00" * 63) == "heads"
    assert decode_coinflip(b"\x01" + b"\x00" * 63) == "tails"
    assert decode_coinflip(b"\xfe" + b"\x00" * 63) == "heads"  # LSB = 0
    assert decode_coinflip(b"\xff" + b"\x00" * 63) == "tails"  # LSB = 1


@given(out=st.binary(min_size=64, max_size=64))
@settings(max_examples=200)
def test_coinflip_outputs_only_heads_or_tails(out: bytes) -> None:
    """Across any 64-byte input, output is exactly heads or tails."""
    result = decode_coinflip(out)
    assert result in ("heads", "tails")


# ---------------------------------------------------------------------------
# decode_dice
# ---------------------------------------------------------------------------


def test_dice_uses_first_4_bytes_be_mod_10000_div_100() -> None:
    """roll = (int.from_bytes(out[:4], 'big') % 10000) / 100."""
    # out[:4] = 0x00000000 → 0 % 10000 / 100 = 0.00
    assert decode_dice(b"\x00\x00\x00\x00" + b"\x00" * 60) == 0.0
    # out[:4] = 0x0000270F = 9999 → 9999 / 100 = 99.99
    assert decode_dice(b"\x00\x00\x27\x0f" + b"\x00" * 60) == 99.99
    # out[:4] = 0x00002710 = 10000 → 0 / 100 = 0.0
    assert decode_dice(b"\x00\x00\x27\x10" + b"\x00" * 60) == 0.0


@given(out=st.binary(min_size=64, max_size=64))
@settings(max_examples=300)
def test_dice_in_range(out: bytes) -> None:
    """Roll always in [0.00, 99.99]."""
    roll = decode_dice(out)
    assert 0.0 <= roll <= 99.99


# ---------------------------------------------------------------------------
# decode_99x
# ---------------------------------------------------------------------------


def test_99x_uses_first_byte_mod_100_plus_1() -> None:
    """(out[0] % 100) + 1 → range 1..100."""
    assert decode_99x(b"\x00" + b"\x00" * 63) == 1
    assert decode_99x(b"\x63" + b"\x00" * 63) == 100  # 99 + 1
    assert decode_99x(b"\x64" + b"\x00" * 63) == 1  # 100 % 100 = 0; +1
    assert decode_99x(b"\xff" + b"\x00" * 63) == 56  # 255 % 100 = 55; +1


@given(out=st.binary(min_size=64, max_size=64))
@settings(max_examples=300)
def test_99x_in_range(out: bytes) -> None:
    n = decode_99x(out)
    assert 1 <= n <= 100


# ---------------------------------------------------------------------------
# decode_hotcold
# ---------------------------------------------------------------------------


def test_hotcold_thresholds_rainbow() -> None:
    """n < 500 → rainbow (per spec §4.4)."""
    # n = 0 → rainbow
    assert decode_hotcold(b"\x00\x00" + b"\x00" * 62) == "rainbow"
    # n = 499 (just under threshold) → rainbow.
    # 499 = 0x01F3
    assert decode_hotcold(b"\x01\xf3" + b"\x00" * 62) == "rainbow"


def test_hotcold_thresholds_hot() -> None:
    """500 <= n < 5250 → hot."""
    # n = 500 = 0x01F4 → hot
    assert decode_hotcold(b"\x01\xf4" + b"\x00" * 62) == "hot"
    # n = 5249 = 0x1481 → hot
    assert decode_hotcold(b"\x14\x81" + b"\x00" * 62) == "hot"


def test_hotcold_thresholds_cold() -> None:
    """5250 <= n < 10000 → cold."""
    # n = 5250 = 0x1482 → cold
    assert decode_hotcold(b"\x14\x82" + b"\x00" * 62) == "cold"
    # n = 9999 = 0x270F → cold
    assert decode_hotcold(b"\x27\x0f" + b"\x00" * 62) == "cold"


@given(out=st.binary(min_size=64, max_size=64))
@settings(max_examples=300)
def test_hotcold_outputs_known_set(out: bytes) -> None:
    result = decode_hotcold(out)
    assert result in ("hot", "cold", "rainbow")


# ---------------------------------------------------------------------------
# decode_roulette_eu
# ---------------------------------------------------------------------------


def test_roulette_eu_in_range() -> None:
    """First 2 bytes mod 37 → range 0..36."""
    assert decode_roulette_eu(b"\x00\x00" + b"\x00" * 62) == 0
    # n = 36 = 0x0024 → 36
    assert decode_roulette_eu(b"\x00\x24" + b"\x00" * 62) == 36
    # n = 37 = 0x0025 → 0 (wraps)
    assert decode_roulette_eu(b"\x00\x25" + b"\x00" * 62) == 0


@given(out=st.binary(min_size=64, max_size=64))
@settings(max_examples=300)
def test_roulette_eu_property(out: bytes) -> None:
    n = decode_roulette_eu(out)
    assert 0 <= n <= 36


# ---------------------------------------------------------------------------
# decode_mines_positions
# ---------------------------------------------------------------------------


def test_mines_returns_correct_count() -> None:
    """N mines requested → exactly N positions returned."""
    out = secrets.token_bytes(64)
    for n in (1, 3, 5, 12, 24):
        positions = decode_mines_positions(out, mines_count=n, grid_size=25)
        assert len(positions) == n


def test_mines_positions_distinct() -> None:
    """No duplicate positions (Fisher-Yates property)."""
    out = secrets.token_bytes(64)
    positions = decode_mines_positions(out, mines_count=12, grid_size=25)
    assert len(set(positions)) == 12


def test_mines_positions_in_range() -> None:
    """Every position is in [0, grid_size)."""
    out = secrets.token_bytes(64)
    positions = decode_mines_positions(out, mines_count=24, grid_size=25)
    for p in positions:
        assert 0 <= p < 25


def test_mines_deterministic() -> None:
    """Same input → same output."""
    out = b"\x42" * 64
    a = decode_mines_positions(out, mines_count=5, grid_size=25)
    b = decode_mines_positions(out, mines_count=5, grid_size=25)
    assert a == b


def test_mines_rejects_invalid_count() -> None:
    """mines_count must be in [1, grid_size - 1]."""
    out = b"\x00" * 64
    with pytest.raises(ValueError):
        decode_mines_positions(out, mines_count=0, grid_size=25)
    with pytest.raises(ValueError):
        decode_mines_positions(out, mines_count=25, grid_size=25)
    with pytest.raises(ValueError):
        decode_mines_positions(out, mines_count=-1, grid_size=25)


# ---------------------------------------------------------------------------
# decode_blackjack_deck
# ---------------------------------------------------------------------------


def test_blackjack_deck_total_cards() -> None:
    """decks * 52 cards in the result."""
    out = secrets.token_bytes(64)
    for d in (1, 2, 6, 8):
        deck = decode_blackjack_deck(out, decks=d)
        assert len(deck) == d * 52


def test_blackjack_deck_each_card_appears_decks_times() -> None:
    """In a 6-deck shoe, each rank-suit (0..51) appears exactly 6 times."""
    out = secrets.token_bytes(64)
    deck = decode_blackjack_deck(out, decks=6)
    from collections import Counter

    counts = Counter(deck)
    assert all(v == 6 for v in counts.values())
    assert set(counts) == set(range(52))


def test_blackjack_deck_deterministic() -> None:
    """Same input → same shuffled deck."""
    out = b"\x42" * 64
    a = decode_blackjack_deck(out, decks=6)
    b = decode_blackjack_deck(out, decks=6)
    assert a == b


def test_blackjack_deck_different_seeds_different_decks() -> None:
    """Different HMAC outputs produce different shuffles (essentially
    always — collision probability over 312! permutations is 0)."""
    a = decode_blackjack_deck(b"\x00" * 64, decks=6)
    b = decode_blackjack_deck(b"\xff" * 64, decks=6)
    assert a != b


def test_blackjack_deck_rejects_invalid_decks() -> None:
    out = b"\x00" * 64
    with pytest.raises(ValueError):
        decode_blackjack_deck(out, decks=0)
    with pytest.raises(ValueError):
        decode_blackjack_deck(out, decks=-1)


# ---------------------------------------------------------------------------
# decode_dice_duel
# ---------------------------------------------------------------------------


def test_dice_duel_returns_tuple_in_range() -> None:
    out = b"\x00\x00" + b"\x00" * 62
    p, b = decode_dice_duel(out)
    assert 1 <= p <= 12
    assert 1 <= b <= 12


def test_dice_duel_uses_first_two_bytes() -> None:
    """player = out[0] % 12 + 1; bot = out[1] % 12 + 1."""
    # out[0] = 0 → 1; out[1] = 11 → 12
    assert decode_dice_duel(b"\x00\x0b" + b"\x00" * 62) == (1, 12)
    # out[0] = 12 → 1; out[1] = 23 → 12
    assert decode_dice_duel(b"\x0c\x17" + b"\x00" * 62) == (1, 12)


@given(out=st.binary(min_size=64, max_size=64))
@settings(max_examples=200)
def test_dice_duel_property(out: bytes) -> None:
    p, b = decode_dice_duel(out)
    assert 1 <= p <= 12
    assert 1 <= b <= 12


# ---------------------------------------------------------------------------
# decode_staking_duel
# ---------------------------------------------------------------------------


def test_staking_duel_returns_correct_round_count() -> None:
    out = secrets.token_bytes(64)
    for r in (1, 3, 5, 10):
        rounds = decode_staking_duel(out, max_rounds=r)
        assert len(rounds) == r


def test_staking_duel_round_shape() -> None:
    """Each round has player_roll and bot_roll, each in [1, 12]."""
    out = secrets.token_bytes(64)
    rounds = decode_staking_duel(out, max_rounds=10)
    for r in rounds:
        assert isinstance(r, StakingRound)
        assert 1 <= r.player_roll <= 12
        assert 1 <= r.bot_roll <= 12


def test_staking_duel_deterministic() -> None:
    out = b"\x42" * 64
    a = decode_staking_duel(out, max_rounds=5)
    b = decode_staking_duel(out, max_rounds=5)
    assert a == b


def test_staking_duel_rejects_invalid_rounds() -> None:
    out = b"\x00" * 64
    with pytest.raises(ValueError):
        decode_staking_duel(out, max_rounds=0)
    with pytest.raises(ValueError):
        decode_staking_duel(out, max_rounds=-1)


# ---------------------------------------------------------------------------
# decode_raffle_winners
# ---------------------------------------------------------------------------


def test_raffle_winners_returns_three() -> None:
    out = secrets.token_bytes(64)
    winners = decode_raffle_winners(out, ticket_count=100)
    assert len(winners) == 3


def test_raffle_winners_distinct() -> None:
    """No duplicate winners — first 3 of Fisher-Yates."""
    out = secrets.token_bytes(64)
    winners = decode_raffle_winners(out, ticket_count=100)
    assert len(set(winners)) == 3


def test_raffle_winners_in_range() -> None:
    out = secrets.token_bytes(64)
    winners = decode_raffle_winners(out, ticket_count=50)
    for w in winners:
        assert 0 <= w < 50


def test_raffle_winners_deterministic() -> None:
    out = b"\x42" * 64
    a = decode_raffle_winners(out, ticket_count=100)
    b = decode_raffle_winners(out, ticket_count=100)
    assert a == b


def test_raffle_winners_rejects_too_few_tickets() -> None:
    """Cannot pick 3 winners from < 3 tickets."""
    out = b"\x00" * 64
    with pytest.raises(ValueError):
        decode_raffle_winners(out, ticket_count=2)
    with pytest.raises(ValueError):
        decode_raffle_winners(out, ticket_count=0)


# ---------------------------------------------------------------------------
# Module hygiene
# ---------------------------------------------------------------------------


def test_decoders_no_io_or_globals() -> None:
    """Decoders are pure: no os/sys/random imports beyond hashlib for chain."""
    import deathroll_core.fairness.decoders as decoders

    src = _get_source(decoders)
    forbidden = ["import os", "import sys", "import random ", "from random "]
    for needle in forbidden:
        assert needle not in src, f"decoders.py imports forbidden: {needle}"


def _get_source(module: object) -> str:
    import inspect

    return inspect.getsource(module)
