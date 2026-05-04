#!/usr/bin/env node
/**
 * DeathRoll Luck — public Node.js verifier.
 *
 * Zero-dependency, single-file implementation of the bot's
 * provably-fair algorithm. Same algorithm as ``verify.py``;
 * CI cross-checks them byte-for-byte against each other AND
 * against the bot's ``deathroll_core/fairness/`` package.
 *
 * Usage:
 *
 *   node verify.js <game> <server_seed_hex> <client_seed> <nonce> [extra...]
 *
 * Examples:
 *
 *   node verify.js coinflip d4...3fb cs 0
 *   node verify.js mines    cafe...    cs 0 3 25
 *   node verify.js blackjack cafe...   cs 0 6
 *
 * Output is JSON on stdout.
 */

const crypto = require('crypto');

// ---------------------------------------------------------------------------
// Engine
// ---------------------------------------------------------------------------

function compute(serverSeed, clientSeed, nonce) {
    const msg = Buffer.from(`${clientSeed}:${nonce}`, 'utf8');
    return crypto.createHmac('sha512', serverSeed).update(msg).digest();
}

function extend(out, byteCount) {
    if (byteCount <= out.length) return out.slice(0, byteCount);
    const chunks = [out];
    let total = out.length;
    let counter = 0;
    while (total < byteCount) {
        counter += 1;
        const counterBuf = Buffer.alloc(4);
        counterBuf.writeUInt32BE(counter, 0);
        const chunk = crypto
            .createHash('sha256')
            .update(Buffer.concat([out, counterBuf]))
            .digest();
        chunks.push(chunk);
        total += chunk.length;
    }
    return Buffer.concat(chunks).slice(0, byteCount);
}

// ---------------------------------------------------------------------------
// Byte-stream + Fisher-Yates helpers
// ---------------------------------------------------------------------------

function* byteStream(out) {
    for (const b of out) yield b;
    let counter = 0;
    while (true) {
        counter += 1;
        const counterBuf = Buffer.alloc(4);
        counterBuf.writeUInt32BE(counter, 0);
        const chunk = crypto
            .createHash('sha256')
            .update(Buffer.concat([out, counterBuf]))
            .digest();
        for (const b of chunk) yield b;
    }
}

function pullUint(stream, width) {
    let value = 0;
    for (let i = 0; i < width; i++) {
        value = value * 256 + stream.next().value;
    }
    return value;
}

function fisherYatesPartial(stream, n, k, pickWidth = 4) {
    const arr = [];
    for (let i = 0; i < n; i++) arr.push(i);
    const bound = Math.max(1, n - k);
    for (let i = n - 1; i >= bound; i--) {
        const j = pullUint(stream, pickWidth) % (i + 1);
        const tmp = arr[i];
        arr[i] = arr[j];
        arr[j] = tmp;
    }
    // settled slots are arr[n-k:n]; reverse so the first selected
    // is at index 0.
    return arr.slice(n - k).reverse();
}

// ---------------------------------------------------------------------------
// Decoders
// ---------------------------------------------------------------------------

function decodeCoinflip(out) {
    return (out[0] & 1) === 0 ? 'heads' : 'tails';
}

function decodeDice(out) {
    // BE int from first 4 bytes.
    const n =
        out[0] * 0x1000000 + out[1] * 0x10000 + out[2] * 0x100 + out[3];
    return (n % 10000) / 100;
}

function decode99x(out) {
    return (out[0] % 100) + 1;
}

function decodeHotcold(out) {
    const n = ((out[0] << 8) | out[1]) % 10000;
    if (n < 500) return 'rainbow';
    if (n < 5250) return 'hot';
    return 'cold';
}

function decodeRouletteEu(out) {
    return ((out[0] << 8) | out[1]) % 37;
}

function decodeMinesPositions(out, minesCount, gridSize) {
    if (!(1 <= minesCount && minesCount < gridSize)) {
        throw new Error(
            `mines_count out of range: ${minesCount} (must be in [1, ${gridSize - 1}])`,
        );
    }
    const stream = byteStream(out);
    return fisherYatesPartial(stream, gridSize, minesCount);
}

function decodeBlackjackDeck(out, decks) {
    if (decks <= 0) {
        throw new Error(`decks must be positive: ${decks}`);
    }
    const cards = [];
    for (let c = 0; c < 52; c++) {
        for (let d = 0; d < decks; d++) cards.push(c);
    }
    const stream = byteStream(out);
    const n = cards.length;
    for (let i = n - 1; i > 0; i--) {
        const j = pullUint(stream, 4) % (i + 1);
        const tmp = cards[i];
        cards[i] = cards[j];
        cards[j] = tmp;
    }
    return cards;
}

function decodeDiceDuel(out) {
    return [(out[0] % 12) + 1, (out[1] % 12) + 1];
}

function decodeStakingDuel(out, maxRounds) {
    if (maxRounds <= 0) {
        throw new Error(`max_rounds must be positive: ${maxRounds}`);
    }
    const stream = byteStream(out);
    const rounds = [];
    for (let r = 0; r < maxRounds; r++) {
        const p = (stream.next().value % 12) + 1;
        const b = (stream.next().value % 12) + 1;
        rounds.push({ player_roll: p, bot_roll: b });
    }
    return rounds;
}

function decodeRaffleWinners(out, ticketCount) {
    if (ticketCount < 3) {
        throw new Error(
            `need at least 3 tickets for 3 winners; got ${ticketCount}`,
        );
    }
    const stream = byteStream(out);
    return fisherYatesPartial(stream, ticketCount, 3);
}

// ---------------------------------------------------------------------------
// Dispatch
// ---------------------------------------------------------------------------

function dispatch(game, out, args) {
    switch (game) {
        case 'coinflip':
            return decodeCoinflip(out);
        case 'dice':
            return decodeDice(out);
        case '99x':
            return decode99x(out);
        case 'hotcold':
            return decodeHotcold(out);
        case 'roulette':
            return decodeRouletteEu(out);
        case 'diceduel':
            return decodeDiceDuel(out);
        case 'mines':
            if (args.length !== 2) throw new Error('mines: <mines_count> <grid_size>');
            return decodeMinesPositions(out, parseInt(args[0], 10), parseInt(args[1], 10));
        case 'blackjack':
            if (args.length !== 1) throw new Error('blackjack: <decks>');
            return decodeBlackjackDeck(out, parseInt(args[0], 10));
        case 'staking':
            if (args.length !== 1) throw new Error('staking: <max_rounds>');
            return decodeStakingDuel(out, parseInt(args[0], 10));
        case 'raffle':
            if (args.length !== 1) throw new Error('raffle: <ticket_count>');
            return decodeRaffleWinners(out, parseInt(args[0], 10));
        default:
            throw new Error(`unknown game: ${game}`);
    }
}

function main() {
    const argv = process.argv.slice(2);
    if (argv.length < 4) {
        console.error(
            'Usage: verify.js <game> <server_seed_hex> <client_seed> <nonce> [extra_args...]',
        );
        process.exit(2);
    }
    const game = argv[0];
    const serverSeed = Buffer.from(argv[1], 'hex');
    const clientSeed = argv[2];
    const nonce = parseInt(argv[3], 10);
    const extra = argv.slice(4);

    const head = compute(serverSeed, clientSeed, nonce);
    const extended = extend(head, 4096);
    const outcome = dispatch(game, extended, extra);
    process.stdout.write(JSON.stringify(outcome) + '\n');
}

main();
