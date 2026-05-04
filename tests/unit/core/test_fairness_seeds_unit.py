"""Story 3.2 — unit-level tests for the SeedState model + validation.

Spec ref: Luck design §4.2, §4.3.

These tests don't need a Postgres container — they cover the
Pydantic model's redaction guarantees, the client_seed regex
validation, and the source-level audit that the wrapper module
never logs the raw ``server_seed``.

The integration-level tests (against a real DB) live in
``tests/integration/luck/test_seeds_wrapper.py``.
"""

from __future__ import annotations

import json
import re

import pytest
from deathroll_core.fairness.seeds import (
    CLIENT_SEED_REGEX,
    SeedState,
    validate_client_seed,
)

# ---------------------------------------------------------------------------
# SeedState — public surface only
# ---------------------------------------------------------------------------


def test_seed_state_has_only_public_fields() -> None:
    """SeedState must NEVER carry the raw ``server_seed``."""
    fields = set(SeedState.model_fields)
    assert "server_seed" not in fields
    # Every public field is documented + named.
    assert fields == {"server_seed_hash", "client_seed", "nonce"}


def test_seed_state_repr_redacts_no_secret() -> None:
    """repr(SeedState) cannot leak the raw seed because the field
    isn't on the model. Pin the expected shape."""
    s = SeedState(
        server_seed_hash=b"\xab" * 32,
        client_seed="aleix",
        nonce=42,
    )
    rendered = repr(s)
    # No leaked secret possible; sanity-check public fields render.
    assert "server_seed=" not in rendered
    assert "abab" in rendered or "0xab" in rendered or "aleix" in rendered


def test_seed_state_json_redacts_no_secret() -> None:
    """model_dump_json on SeedState only ever exposes the three public
    fields; never the raw server_seed (which isn't on the model)."""
    s = SeedState(
        server_seed_hash=b"\x12\x34" * 16,
        client_seed="my-seed",
        nonce=7,
    )
    payload = s.model_dump(mode="json")
    assert set(payload) == {"server_seed_hash", "client_seed", "nonce"}
    # Make sure model_dump_json round-trips cleanly via JSON.
    raw_json = s.model_dump_json()
    parsed = json.loads(raw_json)
    assert "server_seed" not in parsed
    assert set(parsed) == {"server_seed_hash", "client_seed", "nonce"}


def test_seed_state_extra_forbid() -> None:
    """A constructor that tries to pass server_seed must fail —
    Pydantic with extra='forbid' rejects unknown fields. This is
    the structural guard against accidentally smuggling secrets
    into a SeedState."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SeedState(  # type: ignore[call-arg]
            server_seed_hash=b"\x00" * 32,
            client_seed="cs",
            nonce=0,
            server_seed=b"DEADBEEF",  # never allowed
        )


# ---------------------------------------------------------------------------
# validate_client_seed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "x",
        "abc",
        "AAAAAA",
        "my_seed",
        "my-seed-2026",
        "0123456789",
        "A" * 64,
        "_-A_-",
        "discord_user_1234",
    ],
)
def test_validate_client_seed_accepts(value: str) -> None:
    """The regex ^[A-Za-z0-9_\\-]{1,64}$ accepts these values."""
    assert validate_client_seed(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "",                # empty
        "A" * 65,          # too long
        "with spaces",     # space forbidden
        "with:colon",      # punctuation forbidden
        "with.dot",        # dot forbidden
        "umlautü",         # non-ASCII forbidden
        "tab\there",       # control char forbidden
        "newline\n",       # control char forbidden
        "emoji😀",         # emoji forbidden
        "/slashes/",       # slash forbidden
        "back\\slash",     # backslash forbidden
        "quotes'inside'",  # quotes forbidden
    ],
)
def test_validate_client_seed_rejects(value: str) -> None:
    with pytest.raises(ValueError, match="invalid client_seed"):
        validate_client_seed(value)


def test_client_seed_regex_pinned() -> None:
    """The published regex is the contract; pinned so any drift
    surfaces."""
    assert CLIENT_SEED_REGEX.pattern == r"^[A-Za-z0-9_\-]{1,64}$"


def test_validate_client_seed_uses_full_match() -> None:
    """Regex must full-match — no embedded valid substring escape."""
    assert re.fullmatch(CLIENT_SEED_REGEX, "abc") is not None
    assert re.fullmatch(CLIENT_SEED_REGEX, "abc def") is None


# ---------------------------------------------------------------------------
# Module hygiene — no log.* / logger.* / print containing server_seed
# ---------------------------------------------------------------------------


def test_seeds_module_does_not_log_server_seed() -> None:
    """Audit constraint: no log line in seeds.py mentions the literal
    `server_seed` variable name (only `server_seed_hash` is allowed
    in any context).

    We grep the source for any line that contains both a logger-
    or print-like call AND the substring 'server_seed' but NOT
    'server_seed_hash'. Pinned so a future contributor can't
    accidentally start logging the raw seed.
    """
    import deathroll_core.fairness.seeds as seeds

    src = _get_source(seeds)
    log_call_re = re.compile(
        r"(log(ger)?\.[a-zA-Z_]+\(|print\(|structlog\.|self\.log\.)"
    )
    for n, line in enumerate(src.splitlines(), start=1):
        if log_call_re.search(line):
            # Strip 'server_seed_hash' first so we don't false-match.
            stripped = line.replace("server_seed_hash", "")
            assert "server_seed" not in stripped, (
                f"seeds.py:{n} appears to log the raw server_seed: {line!r}"
            )


def _get_source(module: object) -> str:
    import inspect

    return inspect.getsource(module)
