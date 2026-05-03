# Dice Duel

## Overview

Two players roll dice against each other; the higher roll wins the pot (after rake).

> **Status**: stub (Story 1.5). Full content lands in Story 6.8 (Dice Duel game logic).

## Rules

TODO: Story 6.8 — match-making (lobby? auto-pair?), tie-breaking, rake percentage, max simultaneous duels per user.

## Outcome derivation (provably fair)

TODO: Story 6.8 — both players' rolls derived from the same shared HMAC-SHA512 stream, with each player consuming a different nonce window. Uses the `luck.bet_rounds` table to record both players' rolls under a shared `bet_uid`.

## Payout table

TODO: Story 6.8.

## Verifier reference

TODO: Story 13.x.

## References

- Luck design spec §6.8
