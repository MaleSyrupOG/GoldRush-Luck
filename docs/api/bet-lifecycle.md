# Bet lifecycle

> **Status**: stub outline (Story 1.5). Polished as games come online (Stories 6.x).

## Sequence

TODO: Story 6.x — produce a mermaid sequence diagram covering:

1. User runs `/<game>` slash command.
2. Cog handler validates input (Pydantic), checks rate limit, checks balance.
3. Calls the SECURITY DEFINER game-settlement fn (`luck.settle_bet_<game>`).
4. SDF acquires `FOR UPDATE` lock on user's `core.balances` row, debits the stake.
5. SDF reads the user's current `fairness.user_seeds` row (server_seed, client_seed, nonce).
6. SDF derives the outcome via `HMAC-SHA512(server_seed, client_seed || nonce)` and the per-game decoder.
7. SDF computes the payout, credits the user.
8. SDF writes the audit row + the `luck.bets` row.
9. SDF increments `fairness.user_seeds.nonce`.
10. Returns to the cog, which renders the result embed.

## Multi-round games (blackjack, dice duel)

TODO: Story 6.6 / 6.8 — describe the `luck.bet_rounds` extension where each user action consumes one nonce-step and reveals the next deterministic input from the HMAC stream.

## References

- Luck design spec §4 (bet schema)
- Luck design spec §6 (per-game logic)
