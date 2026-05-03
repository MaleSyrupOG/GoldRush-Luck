# 99x

## Overview

Crash-style game. A multiplier rises from 1.00x; the user can cash out at any time before the multiplier "crashes". If they cash out in time, they win `bet × multiplier_at_cashout`. If they don't, they lose the bet.

> **Status**: stub (Story 1.5). Full content lands in Story 6.3 (99x game logic).

## Rules

TODO: Story 6.3 — describe the multiplier curve, cashout interaction model, and the maximum-multiplier cap (99x).

## Outcome derivation (provably fair)

TODO: Story 6.3 — document the HMAC-SHA512 → crash-point derivation using the Stake.com formula `(100 * 2^52 - h) / (2^52 - h)`.

## Payout table

TODO: Story 6.3.

## Verifier reference

TODO: Story 13.x.

## References

- Luck design spec §6.3
