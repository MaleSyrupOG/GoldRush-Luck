# DeathRoll verifier

> **Status**: stub (Story 1.5). Full content lands in Story 8.x (fairness package + verifier publication).

## Purpose

A clone-and-run tool for any third party to independently verify the outcome of any past Luck bet, given the public bet record (which includes `serverSeed` (revealed), `clientSeed`, `nonce`, `outcome`).

The verifier deliberately has minimal dependencies — `python` + `hashlib` + `hmac` (Python verifier) and `node` + `crypto` (Node verifier). No network calls. No DB access. No reliance on the bot being online.

## Layout (planned)

```
verifier/
├── README.md       (this file — Story 1.5 stub)
├── python/
│   ├── verify.py   (Story 8.x)
│   ├── README.md   (Story 8.x — usage)
│   └── tests/      (Story 8.x — pinned vectors)
├── node/
│   ├── verify.js   (Story 8.x)
│   ├── package.json
│   ├── README.md
│   └── tests/
└── vectors/
    ├── coinflip.json
    ├── dice.json
    ├── ninetyninex.json
    ├── hotcold.json
    ├── mines.json
    ├── blackjack.json
    ├── roulette.json
    ├── diceduel.json
    ├── stakingduel.json
    └── raffle.json
```

Each vector file holds a known-good `(seedServer, clientSeed, nonce, expected_outcome)` tuple. The verifier libraries' tests pin against these vectors and the bot's CI also re-runs them on every PR — so a regression in either side surfaces.

## Verifier API (planned)

```bash
# Python
python -m deathroll_verifier coinflip \
  --server-seed <hex> --client-seed <hex> --nonce <int>
# → outcome: heads | tails

# Node
npx deathroll-verifier coinflip \
  --server-seed <hex> --client-seed <hex> --nonce <int>
# → outcome: heads | tails
```

## References

- `provably-fair.md`
- Luck design spec §5 (fairness)
- `docs/games/*.md` (per-game derivation rules)
