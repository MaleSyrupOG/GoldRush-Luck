"""Smoke tests for the goldrush_deposit_withdraw package.

These tests verify that the package and its subpackages import cleanly. They
exist as the most basic regression net: any future PR that breaks the import
surface fails CI immediately, before more meaningful tests even start.

When Epic 4 lands, these tests will be joined by structural tests against the
bot client, cog manifest, and healthcheck.
"""

from __future__ import annotations

import importlib


def test_top_level_package_imports() -> None:
    pkg = importlib.import_module("goldrush_deposit_withdraw")
    assert pkg is not None


def test_subpackages_import() -> None:
    for subpackage in (
        "goldrush_deposit_withdraw.tickets",
        "goldrush_deposit_withdraw.cashiers",
        "goldrush_deposit_withdraw.commands",
        "goldrush_deposit_withdraw.views",
        "goldrush_deposit_withdraw.setup",
    ):
        module = importlib.import_module(subpackage)
        assert module is not None


def test_main_module_exits_clean() -> None:
    from goldrush_deposit_withdraw import __main__ as main_mod

    assert main_mod.main() == 0


def test_healthcheck_module_exits_clean() -> None:
    from goldrush_deposit_withdraw import healthcheck as hc

    assert hc.main() == 0
