# DeathRoll — Provably fair (HMAC-SHA512)

> **Status**: stub (Story 1.5). Full content lands in Story 8.x (fairness package + verifier).

## What "provably fair" means here

TODO: Story 8.x — explain the commit-reveal model: the bot commits to a `serverSeed` by publishing `SHA-256(serverSeed)` BEFORE the bet; the user can mix in their own `clientSeed`; outcomes are derived deterministically from `HMAC-SHA512(serverSeed, clientSeed || nonce)` and become independently verifiable once the `serverSeed` is revealed.

## Per-user seed lifecycle

TODO: Story 8.x — describe the `fairness.user_seeds` table model (current `serverSeed` + `serverSeedHash` published, `clientSeed` user-editable, `nonce` monotonic) and the on-demand rotation API (`/fair-rotate`).

## Derivation algorithm

TODO: Story 8.x — concrete bit-mapping per game type. The Stake.com hash-truncation algorithm (`(100 * 2^52 - h) / (2^52 - h)`) for multipliers; modulo-N for discrete outcomes; Fisher-Yates with a CSPRNG-seeded stream for shuffles.

## Verification workflow

TODO: Story 8.x — how a user verifies a past bet:
1. Trigger seed rotation; the previous `serverSeed` is revealed in `fairness.user_seed_history`.
2. Fetch the bet record (including `serverSeedHash`, `clientSeed`, `nonce`).
3. Verify `SHA-256(serverSeed) == serverSeedHash`.
4. Recompute the outcome and compare.

## Verifier surface

TODO: Story 13.x — published verifier in Python (`verifier/python/`) and Node.js (`verifier/node/`); test vectors in `verifier/vectors/`; CLI `python -m deathroll_verifier` and `npx deathroll-verifier`.

## References

- Luck design spec §5 (fairness)
- `verifier/README.md`
- `docs/games/*.md` — per-game outcome derivation
