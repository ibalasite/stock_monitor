"""BDD step definitions for FR-19: 全市場估值掃描.

Steps implement scenarios in features/market_scan.feature.
All scenarios are RED until FR-19 (EDD §14) is implemented.
"""

from __future__ import annotations

import csv
from dataclasses import fields as dc_fields
from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, parsers, then, when


# ---------------------------------------------------------------------------
# Shared per-scenario context fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def scan_ctx() -> dict[str, Any]:
    """Mutable dict shared between Given/When/Then steps of one scenario."""
    return {}


# ---------------------------------------------------------------------------
# Shared stubs (mirror of test_market_scan.py stubs)
# ---------------------------------------------------------------------------

class _StubProvider:
    def __init__(self, stocks: list[dict]):
        self._stocks = list(stocks)

    def get_all_listed_stocks(self) -> list[dict]:
        return list(self._stocks)


class _StubMethod:
    def __init__(self, method_name: str, results_by_stock: dict | None = None):
        self.method_name = method_name
        self.method_version = "v1"
        self._by_stock: dict = results_by_stock or {}

    def compute(self, stock_no: str, trade_date_local: str) -> dict:
        outcome = self._by_stock.get(stock_no)
        if outcome is None:
            return {
                "status": "SKIP_INSUFFICIENT_DATA",
                "fair_price": None,
                "cheap_price": None,
                "method_name": self.method_name,
                "method_version": self.method_version,
            }
        return {
            "status": "SUCCESS",
            "fair_price": outcome["fair"],
            "cheap_price": outcome["cheap"],
            "method_name": self.method_name,
            "method_version": self.method_version,
        }


# ---------------------------------------------------------------------------
# TP-SCAN-001  TwseAllListedStocksProvider symbol contract
# ---------------------------------------------------------------------------

@given("TwseAllListedStocksProvider is importable from all_listed_stocks_twse module")
def given_provider_importable(scan_ctx: dict):
    from tests._contract import require_symbol
    TwseAllListedStocksProvider = require_symbol(
        "stock_monitor.adapters.all_listed_stocks_twse",
        "TwseAllListedStocksProvider",
        "TP-SCAN-001",
    )
    scan_ctx["TwseAllListedStocksProvider"] = TwseAllListedStocksProvider


@when("TwseAllListedStocksProvider is instantiated")
def when_provider_instantiated(scan_ctx: dict):
    cls = scan_ctx["TwseAllListedStocksProvider"]
    scan_ctx["provider_instance"] = cls()


@then("get_all_listed_stocks method should exist on the provider")
def then_method_exists(scan_ctx: dict):
    provider = scan_ctx["provider_instance"]
    assert callable(getattr(provider, "get_all_listed_stocks", None)), (
        "[TP-SCAN-001] get_all_listed_stocks() method not found on provider instance."
    )


# ---------------------------------------------------------------------------
# TP-SCAN-002  run_market_scan_job + MarketScanResult symbol contract
# ---------------------------------------------------------------------------

@given("run_market_scan_job is importable from market_scan module")
def given_run_market_scan_importable(scan_ctx: dict):
    from tests._contract import require_symbol
    fn = require_symbol(
        "stock_monitor.application.market_scan",
        "run_market_scan_job",
        "TP-SCAN-002",
    )
    scan_ctx["run_market_scan_job"] = fn


@given("MarketScanResult is importable from market_scan module")
def given_market_scan_result_importable(scan_ctx: dict):
    from tests._contract import require_symbol
    cls = require_symbol(
        "stock_monitor.application.market_scan",
        "MarketScanResult",
        "TP-SCAN-002",
    )
    scan_ctx["MarketScanResult"] = cls


@then("MarketScanResult should have scan_date total_stocks watchlist_upserted near_fair_count uncalculable_count output_dir fields")
def then_market_scan_result_fields(scan_ctx: dict):
    cls = scan_ctx["MarketScanResult"]
    field_names = {f.name for f in dc_fields(cls)}
    required = {
        "scan_date", "total_stocks", "watchlist_upserted",
        "near_fair_count", "uncalculable_count", "output_dir",
    }
    missing = required - field_names
    assert not missing, (
        f"[TP-SCAN-002] MarketScanResult missing fields: {missing}"
    )


# ---------------------------------------------------------------------------
# TP-UAT-016  End-to-end scan acceptance
# ---------------------------------------------------------------------------

@pytest.fixture
def uat016_db(tmp_path: Path):
    """Fixture: create a fresh DB for TP-UAT-016."""
    from stock_monitor.adapters.sqlite_repo import apply_schema, connect_sqlite
    db_path = str(tmp_path / "uat016.db")
    conn = connect_sqlite(db_path)
    apply_schema(conn)
    conn.close()
    return db_path


@given("a fresh database is initialized for market scan")
def given_fresh_db(scan_ctx: dict, tmp_path: Path):
    from stock_monitor.adapters.sqlite_repo import apply_schema, connect_sqlite
    db_path = str(tmp_path / "uat016.db")
    conn = connect_sqlite(db_path)
    apply_schema(conn)
    conn.close()
    scan_ctx["db_path"] = db_path
    scan_ctx["output_dir"] = str(tmp_path / "uat016_output")


@given(parsers.parse('stocks provider supplies below_cheap stock "{below_no}" and near_fair stock "{near_no}"'))
def given_stub_stocks(scan_ctx: dict, below_no: str, near_no: str):
    # below_cheap: close=80, cheap=100, fair=150 → 80 ≤ 100
    # near_fair:   close=120, cheap=100, fair=150 → 100 < 120 ≤ 150
    scan_ctx["stocks"] = [
        {"stock_no": below_no, "stock_name": f"股票{below_no}", "yesterday_close": 80.0, "market": "TWSE"},
        {"stock_no": near_no,  "stock_name": f"股票{near_no}",  "yesterday_close": 120.0, "market": "TWSE"},
    ]
    scan_ctx["below_no"] = below_no
    scan_ctx["near_no"] = near_no
    method = _StubMethod(
        "stub_method",
        results_by_stock={
            below_no: {"fair": 150.0, "cheap": 100.0},
            near_no:  {"fair": 150.0, "cheap": 100.0},
        },
    )
    scan_ctx["valuation_methods"] = [method]


@when("run_market_scan_job is executed with stub provider and fresh database")
def when_run_scan(scan_ctx: dict):
    from tests._contract import require_symbol
    run_market_scan_job = require_symbol(
        "stock_monitor.application.market_scan",
        "run_market_scan_job",
        "TP-UAT-016",
    )
    provider = _StubProvider(scan_ctx["stocks"])
    result = run_market_scan_job(
        db_path=scan_ctx["db_path"],
        output_dir=scan_ctx["output_dir"],
        stocks_provider=provider,
        valuation_methods=scan_ctx["valuation_methods"],
    )
    scan_ctx["result"] = result


@then(parsers.parse('watchlist should contain stock "{stock_no}"'))
def then_watchlist_contains(scan_ctx: dict, stock_no: str):
    from stock_monitor.adapters.sqlite_repo import connect_sqlite
    conn = connect_sqlite(scan_ctx["db_path"])
    row = conn.execute(
        "SELECT stock_no FROM watchlist WHERE stock_no=?", (stock_no,)
    ).fetchone()
    conn.close()
    assert row is not None, (
        f"[TP-UAT-016] Stock {stock_no} not found in watchlist after scan."
    )


@then("scan_results_above_cheap csv should exist in the output directory")
def then_csv_exists(scan_ctx: dict):
    from datetime import date as _date
    scan_date = _date.today().strftime("%Y%m%d")
    csv_path = Path(scan_ctx["output_dir"]) / f"scan_{scan_date}_near_fair.csv"
    assert csv_path.exists(), (
        f"[TP-UAT-016] scan_{scan_date}_near_fair.csv not found at {csv_path}"
    )
    scan_ctx["csv_path"] = csv_path


@then(parsers.parse('scan_results_above_cheap csv should contain a row for stock "{stock_no}"'))
def then_csv_contains_stock(scan_ctx: dict, stock_no: str):
    from datetime import date as _date
    scan_date = _date.today().strftime("%Y%m%d")
    csv_path = Path(scan_ctx["output_dir"]) / f"scan_{scan_date}_near_fair.csv"
    with csv_path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    found = any(r.get("stock_no") == stock_no for r in rows)
    assert found, (
        f"[TP-UAT-016] Stock {stock_no} not found in scan_{scan_date}_near_fair.csv. Rows: {rows}"
    )


@then("MarketScanResult watchlist_upserted should equal 1")
def then_upserted_count(scan_ctx: dict):
    result = scan_ctx["result"]
    assert result.watchlist_upserted == 1, (
        f"[TP-UAT-016] Expected watchlist_upserted=1, got {result.watchlist_upserted}"
    )


@then("MarketScanResult near_fair_count should equal 1")
def then_near_fair_count(scan_ctx: dict):
    result = scan_ctx["result"]
    assert result.near_fair_count == 1, (
        f"[TP-UAT-016] Expected near_fair_count=1, got {result.near_fair_count}"
    )


@then("system_logs in database should have no LINE_SEND event")
def then_no_line_send_in_logs(scan_ctx: dict):
    from stock_monitor.adapters.sqlite_repo import connect_sqlite
    conn = connect_sqlite(scan_ctx["db_path"])
    rows = conn.execute(
        "SELECT id FROM system_logs WHERE event LIKE '%LINE_SEND%'"
    ).fetchall()
    conn.close()
    assert len(rows) == 0, (
        f"[TP-UAT-016] scan-market must NOT produce LINE_SEND events in system_logs. "
        f"Found {len(rows)} row(s)."
    )
