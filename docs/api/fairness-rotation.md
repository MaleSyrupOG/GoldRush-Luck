# Fairness seed rotation

> **Status**: stub outline (Story 1.5). Full content lands in Story 8.x.

## Current state

TODO: Story 8.x — the per-user `fairness.user_seeds` row carries `(server_seed, server_seed_hash, client_seed, nonce)`. The bot publishes `server_seed_hash` (a SHA-256 commitment) at all times; `server_seed` is private until rotation.

## Rotation trigger

TODO: Story 8.x — user runs `/fair-rotate`. The bot:

1. Reveals the current `server_seed` to the user (in the response embed).
2. Archives the `(revealed_server_seed, server_seed_hash, client_seed, last_nonce)` to `fairness.user_seed_history`.
3. Generates a new `server_seed` (from `secrets.token_bytes(32)`).
4. Publishes the new `server_seed_hash`.
5. Resets `nonce = 0`.

## Verifying past bets after rotation

TODO: Story 8.x — once rotated, the user can fetch the row from `fairness.user_seed_history` and replay every bet in `[start_of_history_row, end_of_history_row]` against the published verifier.

## Setting a custom client seed

TODO: Story 8.x — `/fair-set-client-seed <text>` updates the user's `client_seed`. This rotates the seed implicitly (the new client seed only affects future bets under the next rotation).

## References

- `provably-fair.md`
- Luck design spec §5
- Luck design spec §4 (`fairness.user_seeds` and `fairness.user_seed_history` schemas)
