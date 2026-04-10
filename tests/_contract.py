"""Shared helpers for contract-first TDD tests.

These helpers intentionally fail with readable messages when required
modules/symbols are not implemented yet.
"""

from __future__ import annotations

import importlib


def require_symbol(module_name: str, symbol_name: str, test_id: str):
    """Import symbol from module, failing test with actionable guidance."""
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        raise AssertionError(
            f"[{test_id}] Missing module '{module_name}'. "
            "Implement main code to satisfy this test."
        ) from exc

    if not hasattr(module, symbol_name):
        raise AssertionError(
            f"[{test_id}] Missing symbol '{symbol_name}' in '{module_name}'. "
            "Implement main code to satisfy this test."
        )

    return getattr(module, symbol_name)
