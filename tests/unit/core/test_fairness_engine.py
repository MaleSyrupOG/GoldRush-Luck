"""Story 3.1 — HMAC-SHA512 engine.

Spec ref: Luck design §4.1.

The single source of truth for the bot's randomness primitive:

    out = HMAC-SHA512(key=server_seed, message=f"{client_seed}:{nonce}".encode())

Always returns 64 bytes. Pure function. Imports nothing beyond
``hmac`` + ``hashlib``. Pinned with 10 known vectors so any
behavioural drift surfaces in CI.
"""

from __future__ import annotations

import hashlib
import hmac

import pytest
from deathroll_core.fairness.engine import compute
from hypothesis import given, settings
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Known-good vectors
#
# Generated 2026-05-04 via the reference implementation:
#
#   import hmac, hashlib
#   msg = f"{client_seed}:{nonce}".encode()
#   hmac.new(server_seed, msg, hashlib.sha512).digest().hex()
#
# Any drift in compute() (different message format, different hash,
# different bytes) will surface here as a test failure.
# ---------------------------------------------------------------------------


_KNOWN_VECTORS = [
    (
        "0000000000000000000000000000000000000000000000000000000000000000",
        "cs",
        0,
        "d45be310f63d617e4baa154fe0a2fd84b6d857a6414c0201b88c07715eb8904a"
        "a98cac71eb0087dbf4dbff1f1c682c60758b4edacc2ad86f222cef9a5378a3fb",
    ),
    (
        "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
        "cs",
        0,
        "e02919262e0fdc6e728abb9edaa71e0cea64628352bdaa3132006efaae04c012"
        "3db7d8310b04238157f93803fc8478202ba847f469d3b64971767a11d5e31ac9",
    ),
    (
        "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f",
        "aleix-test",
        1,
        "1a24e9cd1437f54ff8b0bd6a50cf0b33750e64cc9fc2de4d6983e648c729e120"
        "7343bb70d7b4937f8b988f9e303043ddf296edfafde251daeccd2b09a305fb8a",
    ),
    (
        "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f",
        "aleix-test",
        2,
        "6a665454bbed65cf5fd29f49da8957dc5152d37e311c980a20bbe7321f06af98"
        "f4351ff765f8df481cdc076f5a484b00b0fa694f7e868c06dcfc8874f889d08a",
    ),
    (
        "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f",
        "aleix-test",
        9999,
        "2497b8958957edcb25e657511d5082e427318fa7694ecad6d6f2db5fdd0df134"
        "dea50ba63c2d1df4a779539f1d8a92b6fcb4df2d635858e1f9713e4ccdb29636",
    ),
    (
        "deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
        "lucky",
        42,
        "e1c571040bd273bf82453ba83482637b55db1f5b88773f96eb1bc8b851ee9692"
        "3a4f6ac076a37338e688e39dec49442875345c1e35e55003ffb58187061a62c4",
    ),
    (
        "4141414141414141414141414141414141414141414141414141414141414141",
        "",
        0,
        "400ea5de217ef7c72dbcf2d9a140021cf4cde30fb71f263640c6f71d648349e6"
        "46edd7e8bac6fd025fb485741c70b5007a93bdd0cc9af48fd150b69950629dd9",
    ),
    (
        "0101010101010101010101010101010101010101010101010101010101010101",
        "a:b:c",
        0,
        "9fd68bf978ddcb5bc2671a718a7264a6ee11b02c87bcb25c620c3971558db4a2"
        "5d77a733daf3d1925ac1fd82a9d148d3bd71d625352cff844c0e111872dd04b9",
    ),
    (
        "caffeebeefdeadc0de0000000000000000000000000000000000000000000000",
        "discord-user-1234",
        100,
        "48b1a9d4930ec279859097e833b57f7c9bdcdcd0c61772528d4401988408bea9"
        "bdacdf973bedce6c5fb17e053a10ee0ea1418e23f1e209543d1d36538bf9c98f",
    ),
    (
        "5555555555555555555555555555555555555555555555555555555555555555",
        "long-client-seed-long-client-seed-long-client-seed-",
        1024,
        "f20fc18f92b5df884d04aef1d49a9672049cf05d00476cf0ba8e077028bbde10"
        "fa8f30782a5837a5b0168f04e70d53b918f392cc8468c35a5e9cee53052f02a4",
    ),
]


@pytest.mark.parametrize(
    ("server_seed_hex", "client_seed", "nonce", "expected_hex"),
    _KNOWN_VECTORS,
)
def test_known_vector(
    server_seed_hex: str, client_seed: str, nonce: int, expected_hex: str
) -> None:
    """Each pinned vector matches the spec's HMAC-SHA512 algorithm."""
    out = compute(bytes.fromhex(server_seed_hex), client_seed, nonce)
    assert out.hex() == expected_hex


# ---------------------------------------------------------------------------
# Output shape
# ---------------------------------------------------------------------------


def test_compute_returns_64_bytes() -> None:
    """SHA-512 → 64 bytes always, regardless of input size."""
    out = compute(b"\x00" * 32, "cs", 0)
    assert isinstance(out, bytes)
    assert len(out) == 64


def test_compute_returns_64_bytes_short_seed() -> None:
    """A short server_seed (< 64 bytes) is padded by HMAC's spec."""
    out = compute(b"short", "cs", 0)
    assert len(out) == 64


def test_compute_returns_64_bytes_long_seed() -> None:
    """A long server_seed (> 64 bytes) is hashed first by HMAC's spec."""
    out = compute(b"x" * 128, "cs", 0)
    assert len(out) == 64


# ---------------------------------------------------------------------------
# Determinism (property test)
# ---------------------------------------------------------------------------


@given(
    server_seed=st.binary(min_size=1, max_size=128),
    client_seed=st.text(
        alphabet=st.characters(min_codepoint=33, max_codepoint=126),
        min_size=0,
        max_size=64,
    ),
    nonce=st.integers(min_value=0, max_value=2**63 - 1),
)
@settings(max_examples=200)
def test_compute_deterministic(
    server_seed: bytes, client_seed: str, nonce: int
) -> None:
    """Same inputs always yield same output (no hidden state)."""
    a = compute(server_seed, client_seed, nonce)
    b = compute(server_seed, client_seed, nonce)
    assert a == b


@given(
    server_seed=st.binary(min_size=32, max_size=32),
    client_seed=st.text(min_size=1, max_size=32),
    n1=st.integers(min_value=0, max_value=2**31),
    n2=st.integers(min_value=0, max_value=2**31),
)
@settings(max_examples=100)
def test_compute_different_nonce_different_output(
    server_seed: bytes, client_seed: str, n1: int, n2: int
) -> None:
    """Different nonces yield different outputs (collision-resistant
    in practice over the full 64-byte space)."""
    if n1 == n2:
        return  # not meaningful
    a = compute(server_seed, client_seed, n1)
    b = compute(server_seed, client_seed, n2)
    assert a != b


# ---------------------------------------------------------------------------
# Cross-check: the engine matches the reference algorithm byte-for-byte
# ---------------------------------------------------------------------------


@given(
    server_seed=st.binary(min_size=1, max_size=64),
    client_seed=st.text(min_size=0, max_size=32),
    nonce=st.integers(min_value=0, max_value=2**32),
)
@settings(max_examples=100)
def test_compute_matches_reference(
    server_seed: bytes, client_seed: str, nonce: int
) -> None:
    """The engine matches the reference HMAC-SHA512 implementation
    byte-for-byte. This is the canonical integrity check."""
    msg = f"{client_seed}:{nonce}".encode()
    expected = hmac.new(server_seed, msg, hashlib.sha512).digest()
    actual = compute(server_seed, client_seed, nonce)
    assert actual == expected


# ---------------------------------------------------------------------------
# Module hygiene
# ---------------------------------------------------------------------------


def test_engine_module_imports_only_hmac_and_hashlib() -> None:
    """Audit constraint: the module imports only stdlib's hmac + hashlib.
    No third-party deps. No I/O. No global state.
    """
    import deathroll_core.fairness.engine as engine

    src = inspect_source(engine)
    # Allow both `import X` and `from X import Y` styles for the two
    # permitted modules. Anything else trips the test.
    forbidden_imports = []
    for line in src.splitlines():
        stripped = line.strip()
        if not (stripped.startswith("import ") or stripped.startswith("from ")):
            continue
        if (
            "hmac" in stripped
            or "hashlib" in stripped
            or "__future__" in stripped
        ):
            continue
        forbidden_imports.append(stripped)
    assert forbidden_imports == [], (
        "engine.py must import only hmac/hashlib, found extra imports: "
        f"{forbidden_imports}"
    )


def inspect_source(module: object) -> str:
    """Return the source of a module as a string. Helper for the
    import-hygiene test."""
    import inspect

    return inspect.getsource(module)
