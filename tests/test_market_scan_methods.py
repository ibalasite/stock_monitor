"""Tests for FR-19 scan-market valuation method injection.

Covers:
- load_enabled_scan_methods: DB-driven method loading
- EmilyCompositeV1, OldbullDividendYieldV1, RayskyBlendedMarginV1: real formula logic
  (uses stub provider — no live network calls)
"""

from __future__ import annotations

import pytest

from stock_monitor.adapters.sqlite_repo import apply_schema, connect_sqlite
from stock_monitor.application.market_scan_methods import load_enabled_scan_methods
from stock_monitor.application.valuation_methods_real import (
    EmilyCompositeV1,
    OldbullDividendYieldV1,
    RayskyBlendedMarginV1,
)


# ---------------------------------------------------------------------------
# Stub provider (no network calls)
# ---------------------------------------------------------------------------


_UNSET = object()  # sentinel: caller did not supply a value


class _StubProvider:
    """Configurable stub for FinMindFinancialDataProvider in unit tests.

    Pass an explicit value (including None) to control what each method returns.
    Omit a parameter to get the sensible non-None default.
    Pass None explicitly → method returns None (simulates missing data).
    """

    def __init__(
        self,
        avg_dividend=_UNSET,
        eps_data=_UNSET,
        balance=_UNSET,
        pe_pb=_UNSET,
        price_stats=_UNSET,
        shares=_UNSET,
    ):
        self._avg_dividend = 5.0 if avg_dividend is _UNSET else avg_dividend
        self._eps_data = (
            {"eps_ttm": 10.0, "eps_10y_avg": 9.0} if eps_data is _UNSET else eps_data
        )
        self._balance = (
            {"current_assets": 500_000.0, "total_liabilities": 200_000.0}
            if balance is _UNSET else balance
        )
        self._pe_pb = (
            {
                "pe_low_avg": 12.0, "pe_mid_avg": 18.0,
                "pb_low_avg": 1.2, "pb_mid_avg": 2.0,
                "bps_latest": 50.0,
            }
            if pe_pb is _UNSET else pe_pb
        )
        self._price_stats = (
            {"year_low_10y": 80.0, "year_avg_10y": 120.0}
            if price_stats is _UNSET else price_stats
        )
        self._shares = 1_000_000_000.0 if shares is _UNSET else shares

    def get_avg_dividend(self, _stock_no: str, years: int = 5) -> float | None:
        return self._avg_dividend

    def get_eps_data(self, _stock_no: str, years: int = 10) -> dict | None:
        return self._eps_data

    def get_balance_sheet_data(self, _stock_no: str) -> dict | None:
        return self._balance

    def get_pe_pb_stats(self, _stock_no: str, years: int = 10) -> dict | None:
        return self._pe_pb

    def get_price_annual_stats(self, _stock_no: str, years: int = 10) -> dict | None:
        return self._price_stats

    def get_shares_outstanding(self, _stock_no: str) -> float | None:
        return self._shares


# ---------------------------------------------------------------------------
# load_enabled_scan_methods
# ---------------------------------------------------------------------------


def _make_db(tmp_path):
    db_path = str(tmp_path / "scan_methods.db")
    conn = connect_sqlite(db_path)
    apply_schema(conn)
    conn.close()
    return db_path


def test_load_enabled_scan_methods_raises_when_empty(tmp_path):
    db_path = _make_db(tmp_path)
    conn = connect_sqlite(db_path)
    try:
        with pytest.raises(RuntimeError, match="MARKET_SCAN_METHODS_EMPTY"):
            load_enabled_scan_methods(conn, as_of_date="2026-04-17")
    finally:
        conn.close()


def test_load_enabled_scan_methods_raises_when_all_disabled(tmp_path):
    db_path = _make_db(tmp_path)
    conn = connect_sqlite(db_path)
    try:
        conn.execute(
            "INSERT INTO valuation_methods(method_name, method_version, enabled, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            ("emily_composite", "v1", 0, 1713000000, 1713000000),
        )
        conn.commit()
        with pytest.raises(RuntimeError, match="MARKET_SCAN_METHODS_EMPTY"):
            load_enabled_scan_methods(conn, as_of_date="2026-04-17")
    finally:
        conn.close()


def test_load_enabled_scan_methods_returns_only_enabled(tmp_path):
    db_path = _make_db(tmp_path)
    conn = connect_sqlite(db_path)
    try:
        conn.execute(
            "INSERT INTO valuation_methods(method_name, method_version, enabled, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            ("emily_composite", "v1", 1, 1713000000, 1713000000),
        )
        conn.execute(
            "INSERT INTO valuation_methods(method_name, method_version, enabled, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            ("oldbull_dividend_yield", "v1", 1, 1713000000, 1713000000),
        )
        conn.execute(
            "INSERT INTO valuation_methods(method_name, method_version, enabled, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            ("raysky_blended_margin", "v1", 1, 1713000000, 1713000000),
        )
        conn.execute(
            "INSERT INTO valuation_methods(method_name, method_version, enabled, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            ("oldbull_dividend_yield", "v2", 0, 1713000000, 1713000000),
        )
        conn.commit()

        methods = load_enabled_scan_methods(conn, as_of_date="2026-04-17")
    finally:
        conn.close()

    names = [m.method_name for m in methods]
    assert "emily_composite" in names
    assert "oldbull_dividend_yield" in names
    assert "raysky_blended_margin" in names
    assert len(methods) == 3


def test_load_enabled_scan_methods_instances_have_provider(tmp_path):
    db_path = _make_db(tmp_path)
    conn = connect_sqlite(db_path)
    try:
        conn.execute(
            "INSERT INTO valuation_methods(method_name, method_version, enabled, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            ("emily_composite", "v1", 1, 1713000000, 1713000000),
        )
        conn.commit()
        methods = load_enabled_scan_methods(conn, as_of_date="2026-04-17")
    finally:
        conn.close()

    assert len(methods) == 1
    assert methods[0].provider is not None


# ---------------------------------------------------------------------------
# OldbullDividendYieldV1 — simplest, pure formula
# ---------------------------------------------------------------------------


def test_oldbull_success_with_valid_dividend():
    m = OldbullDividendYieldV1(provider=_StubProvider(avg_dividend=5.0))
    r = m.compute("2330", "2026-04-17")

    assert r["status"] == "SUCCESS"
    assert r["method_name"] == "oldbull_dividend_yield"
    assert r["method_version"] == "v1"
    # fair = 5.0 / 0.05 = 100, cheap = 5.0 / 0.06 = 83.33
    assert abs(float(r["fair_price"]) - 100.0) < 0.1
    assert abs(float(r["cheap_price"]) - 83.33) < 0.1
    assert float(r["fair_price"]) >= float(r["cheap_price"])


def test_oldbull_skip_when_no_dividend():
    m = OldbullDividendYieldV1(provider=_StubProvider(avg_dividend=None))
    r = m.compute("9999", "2026-04-17")

    assert r["status"] == "SKIP_INSUFFICIENT_DATA"
    assert r["fair_price"] is None
    assert r["cheap_price"] is None


def test_oldbull_skip_when_dividend_is_zero():
    m = OldbullDividendYieldV1(provider=_StubProvider(avg_dividend=0.0))
    r = m.compute("9999", "2026-04-17")

    assert r["status"] == "SKIP_INSUFFICIENT_DATA"


def test_oldbull_skip_when_provider_is_none():
    m = OldbullDividendYieldV1(provider=None)
    r = m.compute("2330", "2026-04-17")
    assert r["status"] == "SKIP_INSUFFICIENT_DATA"


# ---------------------------------------------------------------------------
# EmilyCompositeV1
# ---------------------------------------------------------------------------


def test_emily_success_all_sub_methods():
    """All four sub-methods succeed → result is mean of sub-methods × 0.9."""
    provider = _StubProvider(
        avg_dividend=5.0,        # div: fair=100, cheap=75
        eps_data={"eps_ttm": 10.0, "eps_10y_avg": 8.0},   # PE: base=9, fair=162, cheap=108
        pe_pb={
            "pe_low_avg": 12.0, "pe_mid_avg": 18.0,
            "pb_low_avg": 1.2, "pb_mid_avg": 2.0,
            "bps_latest": 50.0,                            # PB: fair=100, cheap=60
        },
        price_stats={"year_avg_10y": 120.0, "year_low_10y": 80.0},  # hist: fair=120, cheap=80
    )
    m = EmilyCompositeV1(provider=provider)
    r = m.compute("2330", "2026-04-17")

    assert r["status"] == "SUCCESS"
    assert r["method_name"] == "emily_composite"
    assert r["method_version"] == "v1"
    assert float(r["fair_price"]) > 0
    assert float(r["cheap_price"]) > 0
    assert float(r["fair_price"]) >= float(r["cheap_price"])


def test_emily_success_dividend_only():
    """Only dividend sub-method available → still succeeds."""
    provider = _StubProvider(
        avg_dividend=5.0,
        eps_data=None,
        pe_pb=None,
        price_stats=None,
    )
    m = EmilyCompositeV1(provider=provider)
    r = m.compute("2330", "2026-04-17")

    assert r["status"] == "SUCCESS"
    # fair = 5.0*20*0.9 = 90, cheap = 5.0*15*0.9 = 67.5
    assert abs(float(r["fair_price"]) - 90.0) < 0.5
    assert abs(float(r["cheap_price"]) - 67.5) < 0.5


def test_emily_skip_when_all_sub_methods_fail():
    provider = _StubProvider(
        avg_dividend=None,
        eps_data=None,
        pe_pb=None,
        price_stats=None,
    )
    m = EmilyCompositeV1(provider=provider)
    r = m.compute("9999", "2026-04-17")

    assert r["status"] == "SKIP_INSUFFICIENT_DATA"
    assert r["fair_price"] is None


def test_emily_safety_margin_applied():
    """Verify safety_margin=0.9 is applied to sub-method averages."""
    provider = _StubProvider(
        avg_dividend=10.0,   # div: fair=200, cheap=150
        eps_data=None,
        pe_pb=None,
        price_stats=None,
    )
    m = EmilyCompositeV1(provider=provider, safety_margin=0.9)
    r = m.compute("2330", "2026-04-17")

    assert r["status"] == "SUCCESS"
    assert abs(float(r["fair_price"]) - 180.0) < 0.5   # 200 * 0.9
    assert abs(float(r["cheap_price"]) - 135.0) < 0.5  # 150 * 0.9


# ---------------------------------------------------------------------------
# RayskyBlendedMarginV1
# ---------------------------------------------------------------------------


def test_raysky_success_all_sub_methods():
    """All four sub-methods → median of [PE, div, PB, NCAV] fairs."""
    provider = _StubProvider(
        avg_dividend=5.0,           # div sub: 5/0.05 = 100
        eps_data={"eps_ttm": 10.0, "eps_10y_avg": 9.0},  # PE sub: 10*18=180
        pe_pb={
            "pe_low_avg": 12.0, "pe_mid_avg": 18.0,
            "pb_low_avg": 1.2, "pb_mid_avg": 2.0,
            "bps_latest": 50.0,     # PB sub: 50*2=100
        },
        # balance: (500000-200000)*1000 / 1e9 = 0.3 → NCAV < 0 won't add
        balance={"current_assets": 500_000.0, "total_liabilities": 200_000.0},
        shares=1_000_000_000.0,
    )
    m = RayskyBlendedMarginV1(provider=provider)
    r = m.compute("2330", "2026-04-17")

    assert r["status"] == "SUCCESS"
    assert r["method_name"] == "raysky_blended_margin"
    assert r["method_version"] == "v1"
    assert float(r["fair_price"]) > 0
    assert float(r["cheap_price"]) > 0
    # cheap = fair * 0.9
    assert abs(float(r["cheap_price"]) - float(r["fair_price"]) * 0.9) < 0.5


def test_raysky_success_dividend_only():
    """Only dividend sub-method available → uses div / 0.05."""
    provider = _StubProvider(
        avg_dividend=5.0,
        eps_data=None,
        pe_pb=None,
        balance=None,
        shares=None,
        price_stats=None,
    )
    m = RayskyBlendedMarginV1(provider=provider)
    r = m.compute("2330", "2026-04-17")

    assert r["status"] == "SUCCESS"
    assert abs(float(r["fair_price"]) - 100.0) < 0.5  # 5 / 0.05
    assert abs(float(r["cheap_price"]) - 90.0) < 0.5  # 100 * 0.9


def test_raysky_skip_when_all_fail():
    provider = _StubProvider(
        avg_dividend=None,
        eps_data=None,
        pe_pb=None,
        balance=None,
        shares=None,
        price_stats=None,
    )
    m = RayskyBlendedMarginV1(provider=provider)
    r = m.compute("9999", "2026-04-17")

    assert r["status"] == "SKIP_INSUFFICIENT_DATA"


def test_raysky_ncav_adds_when_positive():
    """NCAV sub-method only contributes when (CA - TL) * 1000 / shares > 0."""
    # CA=1_000_000, TL=500_000 → NCAV = 500_000*1000/100_000_000 = 5 > 0
    provider = _StubProvider(
        avg_dividend=None,
        eps_data=None,
        pe_pb=None,
        balance={"current_assets": 1_000_000.0, "total_liabilities": 500_000.0},
        shares=100_000_000.0,
        price_stats=None,
    )
    m = RayskyBlendedMarginV1(provider=provider)
    r = m.compute("2330", "2026-04-17")

    assert r["status"] == "SUCCESS"
    # NCAV fair = (1_000_000 - 500_000) * 1000 / 100_000_000 = 5.0
    assert abs(float(r["fair_price"]) - 5.0) < 0.1


def test_raysky_margin_factor_applied():
    """cheap = fair × margin_factor."""
    provider = _StubProvider(avg_dividend=5.0, eps_data=None, pe_pb=None, balance=None, shares=None, price_stats=None)
    m = RayskyBlendedMarginV1(provider=provider, margin_factor=0.8)
    r = m.compute("2330", "2026-04-17")

    assert r["status"] == "SUCCESS"
    assert abs(float(r["cheap_price"]) - float(r["fair_price"]) * 0.8) < 0.5
