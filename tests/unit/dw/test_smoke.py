"""Smoke tests for the deathroll_deposit_withdraw package.

Verifies that the package and its subpackages import cleanly. Acts as
the most basic regression net: any future PR that breaks the import
surface fails CI immediately, before more meaningful tests even start.

The original Story 1.1 smoke tests checked that the placeholder
``main()`` and ``healthcheck.main()`` returned 0 on a synthetic call.
Both modules were rewritten in Story 4.1 to do real work (open the
DB pool / run ``SELECT 1``); their structural contract is now
covered by ``test_client.py`` and ``test_healthcheck.py``. The smoke
suite focuses on import-level health.
"""

from __future__ import annotations

import importlib


def test_top_level_package_imports() -> None:
    pkg = importlib.import_module("deathroll_deposit_withdraw")
    assert pkg is not None


def test_subpackages_import() -> None:
    for subpackage in (
        "deathroll_deposit_withdraw.tickets",
        "deathroll_deposit_withdraw.cashiers",
        "deathroll_deposit_withdraw.commands",
        "deathroll_deposit_withdraw.views",
        "deathroll_deposit_withdraw.setup",
    ):
        module = importlib.import_module(subpackage)
        assert module is not None


def test_main_module_imports_without_running() -> None:
    """Importing the bin entry point must not require env vars or a
    Discord token — the side effects only happen inside ``main()``."""
    main_mod = importlib.import_module("deathroll_deposit_withdraw.__main__")
    assert callable(main_mod.main)


def test_healthcheck_module_imports() -> None:
    hc = importlib.import_module("deathroll_deposit_withdraw.healthcheck")
    assert callable(hc.main)
    assert callable(hc.ping)
