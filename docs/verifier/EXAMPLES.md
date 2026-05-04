# DeathRoll verifier — worked examples

One end-to-end example per game. Each shows:

- The published commitment that was visible BEFORE the bet
  (`server_seed_hash`, `client_seed`, `nonce`).
- The revealed `server_seed` (visible AFTER the user rotates
  their seed; it appears in `fairness.history` and is what the
  user records to verify retrospectively).
- The CLI invocation that re-derives the outcome.
- The expected output.

To follow along, copy the command. The verifier prints the
outcome as JSON on stdout.

> **Pre-flight**: clone this repo (or just download the two
> verifier scripts). The Python verifier uses only stdlib; the
> Node verifier uses only the built-in `crypto` module. **Do not
> install anything.** If the script asks for a third-party
> package, it has been tampered with.

---

## 1. Coinflip

```
server_seed (revealed):  0000000000000000000000000000000000000000000000000000000000000000
client_seed:             cs
nonce:                   0
```

**CLI**:

```bash
python verify.py coinflip 0000000000000000000000000000000000000000000000000000000000000000 cs 0
node   verify.js coinflip 0000000000000000000000000000000000000000000000000000000000000000 cs 0
```

**Output**: `"heads"`

The first byte of the HMAC-SHA512 output is `0xd4`, whose LSB is
`0` → heads.

---

## 2. Dice (over/under)

```
server_seed (revealed):  4141414141414141414141414141414141414141414141414141414141414141
client_seed:             ""
nonce:                   0
```

**CLI**:

```bash
python verify.py dice 4141414141414141414141414141414141414141414141414141414141414141 "" 0
```

**Output**: ~`26.40` (the first 4 bytes of HMAC mod 10000 / 100)

---

## 3. 99x

```
server_seed (revealed):  deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef
client_seed:             lucky
nonce:                   42
```

**CLI**:

```bash
python verify.py 99x deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef lucky 42
```

**Output**: an integer in `[1, 100]`. With these inputs, ~`58`.

---

## 4. Hot/Cold

```
server_seed (revealed):  ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff
client_seed:             cs
nonce:                   0
```

**CLI**:

```bash
python verify.py hotcold ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff cs 0
```

**Output**: `"hot"` (first 2 bytes mod 10000 = `0xe029 % 10000 = 3873` → in [500, 5250) → hot)

---

## 5. Mines

```
server_seed (revealed):  000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f
client_seed:             aleix-test
nonce:                   1
mines_count:             3
grid_size:               25
```

**CLI**:

```bash
python verify.py mines 000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f aleix-test 1 3 25
```

**Output**: a JSON list of 3 distinct cell indices in `[0, 25)`. The
order matters — element 0 is the first mine the partial Fisher-
Yates settled, in deterministic FY order.

---

## 6. Blackjack

```
server_seed (revealed):  caffeebeefdeadc0de0000000000000000000000000000000000000000000000
client_seed:             discord-user-1234
nonce:                   100
decks:                   6
```

**CLI**:

```bash
python verify.py blackjack caffeebeefdeadc0de0000000000000000000000000000000000000000000000 discord-user-1234 100 6
```

**Output**: a JSON list of 312 integers (6 decks × 52 cards). Each
integer in `[0, 51]`; suit = `i // 13`, rank = `i % 13`. Each
card value appears exactly 6 times in the list.

The HMAC-SHA512 output is only 64 bytes; for a 311-swap Fisher-
Yates we extend the byte stream via `chunk_n = SHA-256(out ||
n.to_bytes(4, 'big'))` for `n = 1, 2, ...` until enough bytes are
available. Both Python and Node verifiers implement the same
chain; CI cross-checks them byte-for-byte.

---

## 7. Roulette (European single-zero)

```
server_seed (revealed):  0101010101010101010101010101010101010101010101010101010101010101
client_seed:             a:b:c
nonce:                   0
```

**CLI**:

```bash
python verify.py roulette 0101010101010101010101010101010101010101010101010101010101010101 a:b:c 0
```

**Output**: an integer in `[0, 36]`. With these inputs, the first
2 bytes of HMAC = `0x9fd6 = 40918`, then `40918 % 37 = 1` → output `1`.

---

## 8. Dice Duel

```
server_seed (revealed):  5555555555555555555555555555555555555555555555555555555555555555
client_seed:             long-client-seed-long-client-seed-long-client-seed-
nonce:                   1024
```

**CLI**:

```bash
python verify.py diceduel 5555555555555555555555555555555555555555555555555555555555555555 long-client-seed-long-client-seed-long-client-seed- 1024
```

**Output**: a 2-element JSON array `[player_roll, bot_roll]`. Each
in `[1, 12]`. With these inputs: `[(0xf2 % 12) + 1, (0x0f % 12) + 1]
= [3, 4]`.

---

## 9. Staking Duel

```
server_seed (revealed):  000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f
client_seed:             aleix-test
nonce:                   2
max_rounds:              5
```

**CLI**:

```bash
python verify.py staking 000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f aleix-test 2 5
```

**Output**: a JSON array of 5 round objects:

```json
[
  {"player_roll": 11, "bot_roll": 7},
  {"player_roll": 6,  "bot_roll": 6},
  ...
]
```

Each round consumes 2 bytes (one for each side), each `% 12 + 1`.

---

## 10. Raffle (winner draw)

```
server_seed (revealed):  caffeebeefdeadc0de0000000000000000000000000000000000000000000000
client_seed:             discord-user-1234
nonce:                   100
ticket_count:            500
```

**CLI**:

```bash
python verify.py raffle caffeebeefdeadc0de0000000000000000000000000000000000000000000000 discord-user-1234 100 500
```

**Output**: a JSON array of 3 distinct ticket indices in `[0, 500)`.
Element 0 is the 1st-prize winner; element 1 is the 2nd; element
2 is the 3rd. The bot publishes a snapshot of the ticket → user
mapping at draw time so any participant can independently verify
which user won which prize.

---

## How to verify a past bet end-to-end

1. **Before the bet** — the bot showed you `server_seed_hash`
   (the commitment), `client_seed`, `nonce`. Save these locally.
2. **Bet placed** — the audit log records `server_seed_hash`,
   `client_seed`, `nonce`, `outcome`, `payout`.
3. **You rotate your seed** — the bot reveals the previous
   `server_seed` in the rotation embed (and writes it to
   `fairness.history`).
4. **Verify** — for each past bet, run:

   ```bash
   python verify.py <game> <revealed_server_seed_hex> <client_seed> <nonce> [extra...]
   ```

   The output should match the `outcome` recorded in the bet.

5. **Verify the commitment** — separately compute
   `sha256(revealed_server_seed)` (Python: `hashlib.sha256(bytes.fromhex(...)).hexdigest()`;
   Node: `crypto.createHash('sha256').update(Buffer.from(..., 'hex')).digest('hex')`).
   It must equal the `server_seed_hash` you saved BEFORE the bet.
   If it doesn't, the bot showed you one commitment and revealed
   a different seed — that's tampering.

---

## CI cross-check

The bot's CI runs:

1. `verify.py` against every entry in `test_vectors.json` →
   asserts byte-for-byte agreement.
2. The bot's in-tree decoders against the same vectors →
   asserts agreement.
3. 1000 random vectors → bot's decoders are referentially
   transparent and JSON-encodable.
4. (Optional, when Node is on the runner) `verify.js` against
   every 7th pinned vector + 20 random vectors against
   `verify.py`.

Any drift in any of the three implementations surfaces as a CI
failure within seconds of a PR.
