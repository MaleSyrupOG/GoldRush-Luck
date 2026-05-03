# Staking Duel

## Overview

Two players each put up an amount; the bot resolves a winner based on a single HMAC-SHA512-derived random outcome weighted by stake.

> **Status**: stub (Story 1.5). Full content lands in Story 6.9 (Staking Duel game logic).

## Rules

TODO: Story 6.9 — describe the stake-weighted probability model (e.g., player A puts 60%, player B puts 40% → A wins with probability 0.6), the rake, and max stake-ratio limits to prevent griefing.

## Outcome derivation (provably fair)

TODO: Story 6.9 — HMAC-SHA512 → 64-bit integer → divide by 2^64 → fall in `[0, p_a)` for A or `[p_a, 1)` for B.

## Payout table

TODO: Story 6.9.

## Verifier reference

TODO: Story 13.x.

## References

- Luck design spec §6.9
