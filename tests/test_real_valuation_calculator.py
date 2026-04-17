"""Tests for RealValuationCalculator and daemon 14:00 wiring.

Test IDs:
  TP-VAL-010  RealValuationCalculator.calculate() 使用真實方法，非公式近似
  TP-VAL-011  daemon_runner 14:00 使用 RealValuationCalculator，非 ManualValuationCalculator
  TP-VAL-012  SKIP/PROVIDER_ERROR 結果不寫入 snapshots
  TP-VAL-013  DB 中 enabled 方法全部被呼叫，每支 watchlist 股票各呼叫一次

TP-VAL-010~013 will be RED until RealValuationCalculator is implemented.
"""
from __future__ import annotations

import sqlite3

import pytest

from stock_monitor.db.schema import SCHEMA_SQL
from stock_monitor.adapters.financial_data_cache import _CACHE_CREATE_SQL


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db(tmp_path) -> str:
    path = str(tmp_path / "test.db")
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA_SQL)
        conn.execute(_CACHE_CREATE_SQL)
        import time
        now = int(time.time())
        # Two watchlist stocks
        conn.execute(
            "INSERT INTO watchlist(stock_no, stock_name, manual_fair_price, manual_cheap_price,"
            " enabled, created_at, updated_at) VALUES(?,?,?,?,1,?,?)",
            ("2330", "台積電", 2100, 2000, now, now),
        )
        conn.execute(
            "INSERT INTO watchlist(stock_no, stock_name, manual_fair_price, manual_cheap_price,"
            " enabled, created_at, updated_at) VALUES(?,?,?,?,1,?,?)",
            ("0056", "元大高股息", 45, 36, now, now),
        )
        # One enabled valuation method
        conn.execute(
            "INSERT INTO valuation_methods(method_name, method_version, enabled, created_at, updated_at)"
            " VALUES(?,?,1,?,?)",
            ("emily_composite", "v1", now, now),
        )
        conn.commit()
    return path


class _FakeWatchlistRepo:
    def list_enabled(self):
        return [
            {"stock_no": "2330", "stock_name": "台積電", "manual_fair_price": 2100, "manual_cheap_price": 2000},
            {"stock_no": "0056", "stock_name": "元大高股息", "manual_fair_price": 45, "manual_cheap_price": 36},
        ]


# ---------------------------------------------------------------------------
# TP-VAL-010  RealValuationCalculator 使用真實方法
# ---------------------------------------------------------------------------

def test_tp_val_010_real_calc_uses_real_methods(tmp_path):
    """TP-VAL-010: RealValuationCalculator.calculate() must delegate to real method instances
    (loaded from valuation_methods table), not use manual_fair_price formulas directly.

    Verification: spy the method's .compute() calls; confirm stock_no is forwarded.
    """
    from stock_monitor.application.valuation_calculator import RealValuationCalculator

    db_path = _make_db(tmp_path)
    called_with: list[str] = []

    # Inject a spy method via monkeypatching load_enabled_scan_methods
    class _SpyMethod:
        method_name = "emily_composite"
        method_version = "v1"

        def compute(self, stock_no: str, trade_date_local: str) -> dict:
            called_with.append(stock_no)
            return {
                "status": "OK",
                "fair_price": 100.0,
                "cheap_price": 80.0,
                "method_name": "emily_composite",
                "method_version": "v1",
            }

    import stock_monitor.application.market_scan_methods as msm
    original = msm.load_enabled_scan_methods

    def _fake_load(conn, as_of_date, db_path=None):
        return [_SpyMethod()]

    msm.load_enabled_scan_methods = _fake_load
    try:
        calc = RealValuationCalculator(
            watchlist_repo=_FakeWatchlistRepo(),
            trade_date="2026-04-17",
            db_path=db_path,
        )
        snapshots = calc.calculate()
    finally:
        msm.load_enabled_scan_methods = original

    assert sorted(called_with) == ["0056", "2330"], (
        f"RealValuationCalculator must call compute() for each watchlist stock; got {called_with}"
    )
    assert len(snapshots) == 2
    for snap in snapshots:
        assert snap["fair_price"] == 100.0
        assert snap["stock_no"] in ("2330", "0056")
        assert snap["trade_date"] == "2026-04-17"


# ---------------------------------------------------------------------------
# TP-VAL-011  daemon_runner 14:00 使用 RealValuationCalculator
# ---------------------------------------------------------------------------

def test_tp_val_011_daemon_uses_real_calculator():
    """TP-VAL-011: daemon_runner._run_daemon_loop must instantiate RealValuationCalculator,
    NOT ManualValuationCalculator, for the 14:00 valuation job.

    This is a contract test that reads source code to prevent regressions.
    Reading the file directly avoids triggering optional system-level imports
    (e.g. truststore) that may be unavailable in CI.
    Fake calculators in other tests cannot bypass this check.
    """
    import pathlib
    src = pathlib.Path("stock_monitor/application/daemon_runner.py").read_text()

    assert "RealValuationCalculator" in src, (
        "daemon_runner must import and use RealValuationCalculator for the 14:00 valuation job"
    )
    assert "ManualValuationCalculator(" not in src, (
        "daemon_runner must not hardcode ManualValuationCalculator() — "
        "this bypasses all real financial data providers"
    )


# ---------------------------------------------------------------------------
# TP-VAL-012  SKIP/PROVIDER_ERROR 結果不寫入 snapshots
# ---------------------------------------------------------------------------

def test_tp_val_012_skip_results_excluded(tmp_path):
    """TP-VAL-012: Results with status != 'OK' or fair_price=None must not appear in snapshots."""
    from stock_monitor.application.valuation_calculator import RealValuationCalculator

    db_path = _make_db(tmp_path)

    class _MixedMethod:
        method_name = "emily_composite"
        method_version = "v1"
        _call_count = 0

        def compute(self, stock_no: str, trade_date_local: str) -> dict:
            _MixedMethod._call_count += 1
            if stock_no == "2330":
                return {"status": "OK", "fair_price": 1800.0, "cheap_price": 1620.0,
                        "method_name": "emily_composite", "method_version": "v1"}
            else:
                return {"status": "SKIP_PROVIDER_ERROR", "fair_price": None, "cheap_price": None,
                        "method_name": "emily_composite", "method_version": "v1"}

    import stock_monitor.application.market_scan_methods as msm
    original = msm.load_enabled_scan_methods

    msm.load_enabled_scan_methods = lambda *a, **kw: [_MixedMethod()]
    try:
        calc = RealValuationCalculator(
            watchlist_repo=_FakeWatchlistRepo(),
            trade_date="2026-04-17",
            db_path=db_path,
        )
        snapshots = calc.calculate()
    finally:
        msm.load_enabled_scan_methods = original

    assert len(snapshots) == 1, f"Only OK results should be in snapshots, got {len(snapshots)}"
    assert snapshots[0]["stock_no"] == "2330"
    assert snapshots[0]["fair_price"] == 1800.0


# ---------------------------------------------------------------------------
# TP-VAL-013  enabled 方法全部被呼叫，每支股票各一次
# ---------------------------------------------------------------------------

def test_tp_val_013_all_methods_called_per_stock(tmp_path):
    """TP-VAL-013: Every enabled method must be called for every watchlist stock (m × n calls)."""
    from stock_monitor.application.valuation_calculator import RealValuationCalculator

    db_path = _make_db(tmp_path)
    calls: list[tuple[str, str]] = []

    class _TrackingMethod:
        def __init__(self, name):
            self.method_name = name
            self.method_version = "v1"

        def compute(self, stock_no: str, trade_date_local: str) -> dict:
            calls.append((self.method_name, stock_no))
            return {"status": "OK", "fair_price": 50.0, "cheap_price": 40.0,
                    "method_name": self.method_name, "method_version": "v1"}

    import stock_monitor.application.market_scan_methods as msm
    original = msm.load_enabled_scan_methods

    msm.load_enabled_scan_methods = lambda *a, **kw: [
        _TrackingMethod("emily_composite"),
        _TrackingMethod("oldbull_dividend_yield"),
    ]
    try:
        calc = RealValuationCalculator(
            watchlist_repo=_FakeWatchlistRepo(),
            trade_date="2026-04-17",
            db_path=db_path,
        )
        snapshots = calc.calculate()
    finally:
        msm.load_enabled_scan_methods = original

    # 2 stocks × 2 methods = 4 calls
    assert len(calls) == 4, f"Expected 4 calls (2 stocks × 2 methods), got {len(calls)}: {calls}"
    assert len(snapshots) == 4
    stocks_seen = {c[1] for c in calls}
    methods_seen = {c[0] for c in calls}
    assert stocks_seen == {"2330", "0056"}
    assert methods_seen == {"emily_composite", "oldbull_dividend_yield"}
