# Blackjack

## Overview

Single-player vs the dealer (the bot). Standard rules with hit / stand / double / split. Uses a deterministic shuffled deck per hand for fairness.

> **Status**: stub (Story 1.5). Full content lands in Story 6.6 (Blackjack game logic).

## Rules

TODO: Story 6.6 — describe the standard ruleset variant: number of decks, dealer hits/stands on 17, blackjack payout, double-after-split rules, surrender support, etc.

## Outcome derivation (provably fair)

TODO: Story 6.6 — multi-round game; each user action consumes a `nonce` increment and reveals the next deck card via the HMAC-SHA512 stream. The `luck.bet_rounds` table records each round_index → action.

## Payout table

TODO: Story 6.6.

## Verifier reference

TODO: Story 13.x.

## References

- Luck design spec §6.6
- Luck design spec §4 (`bet_rounds` schema)
