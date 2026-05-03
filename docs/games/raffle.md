# Monthly raffle (5B casino raffle)

## Overview

Meta-feature: a portion of every game's rake feeds a monthly prize pool. At month-end, a single winner is drawn weighted by ticket-equivalents accumulated.

> **Status**: stub (Story 1.5). Full content lands in Stories 7.1, 7.2, 7.3 (raffle implementation across the back-end + scheduler + winner drawing).

## Mechanics

TODO: Story 7.x — describe how rake feeds the pool, how user activity converts to "ticket equivalents", the monthly cycle (start, draw time, payout method).

## Provably-fair winner draw

TODO: Story 7.3 — the winner is drawn deterministically from a published `seedServer` revealed at month-end + the list of all participants (each with their ticket count). HMAC-SHA512 → uniform in `[0, total_ticket_count)` → cumulative-frequency lookup → winner.

## Public verifiability

TODO: Story 7.4 — the published artefacts (snapshot of the `luck.raffle_*` tables, revealed seedServer, hash commitment) let any participant independently verify the draw.

## References

- Luck design spec §7
