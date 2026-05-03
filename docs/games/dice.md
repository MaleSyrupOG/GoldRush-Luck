# Dice

## Overview

Roll a number; bet on whether it falls above or below a target.

> **Status**: stub (Story 1.5). Full content lands in Story 6.2 (Dice game logic).

## Rules

TODO: Story 6.2 — describe the target selection mechanic, over/under bet types, and payout curve as a function of probability.

## Outcome derivation (provably fair)

TODO: Story 6.2 — document the HMAC-SHA512 → integer roll mapping. Stake.com-style hash truncation `(100 * 2^52 - h) / (2^52 - h)` is the canonical approach.

## Payout table

TODO: Story 6.2.

## Verifier reference

TODO: Story 13.x.

## References

- Luck design spec §6.2
