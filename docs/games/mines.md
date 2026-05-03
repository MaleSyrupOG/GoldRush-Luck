# Mines

## Overview

Grid-based game. The user chooses how many mines to place; the bot places them randomly. The user reveals tiles one at a time, with the multiplier rising as they accumulate safe reveals. If they reveal a mine, they bust.

> **Status**: stub (Story 1.5). Full content lands in Story 6.5 (Mines game logic).

## Rules

TODO: Story 6.5 — describe grid size, mine count selection, multiplier curve as a function of `mines × safe_reveals`, cashout interaction.

## Outcome derivation (provably fair)

TODO: Story 6.5 — document the HMAC-SHA512 → Fisher-Yates shuffle of the deck. The deck is a list of `mines × 'mine' + (cells - mines) × 'safe'`; shuffled with the bet's nonce-keyed stream.

## Payout table

TODO: Story 6.5.

## Verifier reference

TODO: Story 13.x — the verifier reproduces the shuffle deterministically given the revealed seedServer + clientSeed + nonce.

## References

- Luck design spec §6.5
