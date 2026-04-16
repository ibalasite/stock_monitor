"""TDD RED tests for FR-19 全市場估值掃描.

Test IDs: TP-SCAN-001 ~ TP-SCAN-006
Corresponds to: EDD §14 / PDD FR-19 / TEST_PLAN v1.0

All tests in this file are initially RED (FAIL) because the implementation
modules do not yet exist:
  - stock_monitor.adapters.all_listed_stocks_twse  (TwseAllListedStocksProvider)
  - stock_monitor.application.market_scan           (run_market_scan_job, MarketScanResult)

Per conflict resolution: API_CONTRACT §5.8 specifies raise-on-failure for the
provider (PDD > EDD §14.1 which says return empty list).
"""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path
from urllib.error import URLError

import pytest

from tests._contract import require_symbol


# ---------------------------------------------------------------------------
# Shared stubs
# ---------------------------------------------------------------------------

class _StubProvider:
    """Stub AllListedStocksPort: returns a controlled stock list."""

    def __init__(self, stocks: list[dict]):
        self._stocks = list(stocks)

    def get_all_listed_stocks(self) -> list[dict]:
        return list(self._stocks)


class _StubMethod:
    """Stub valuation method: returns configured result per stock_no.

    results_by_stock: mapping stock_no -> result dict or Exception instance.
    Default (unmapped stocks): SKIP_INSUFFICIENT_DATA.
    """

    def __init__(
        self,
        method_name: str,
        results_by_stock: dict | None = None,
    ):
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
        if isinstance(outcome, Exception):
            raise outcome
        return {
            "status": "SUCCESS",
            "fair_price": outcome["fair"],
            "cheap_price": outcome["cheap"],
            "method_name": self.method_name,
            "method_version": self.method_version,
        }


class _SkipMethod:
    """Stub method that always returns SKIP for any stock."""

    def __init__(self, method_name: str, skip_reason: str = "SKIP_INSUFFICIENT_DATA"):
        self.method_name = method_name
        self.method_version = "v1"
        self._reason = skip_reason

    def compute(self, stock_no: str, trade_date_local: str) -> dict:
        return {
            "status": self._reason,
            "fair_price": None,
            "cheap_price": None,
            "method_name": self.method_name,
            "method_version": self.method_version,
        }


def _make_db(tmp_path: Path) -> str:
    """Create an initialized SQLite DB at tmp_path/scan_test.db and return its path."""
    from stock_monitor.adapters.sqlite_repo import apply_schema, connect_sqlite

    db_path = str(tmp_path / "scan_test.db")
    conn = connect_sqlite(db_path)
    apply_schema(conn)
    conn.close()
    return db_path


# ---------------------------------------------------------------------------
# TP-SCAN-001  TwseAllListedStocksProvider symbol exists + method callable
# ---------------------------------------------------------------------------

def test_tp_scan_001_provider_symbol_exists():
    """[TP-SCAN-001] TwseAllListedStocksProvider must be importable and expose
    get_all_listed_stocks() method. (EDD §14.2 / PDD FR-19)
    """
    TwseAllListedStocksProvider = require_symbol(
        "stock_monitor.adapters.all_listed_stocks_twse",
        "TwseAllListedStocksProvider",
        "TP-SCAN-001",
    )
    provider = TwseAllListedStocksProvider()
    assert callable(getattr(provider, "get_all_listed_stocks", None)), (
        "[TP-SCAN-001] get_all_listed_stocks() method not found on "
        "TwseAllListedStocksProvider instance."
    )


def test_tp_scan_001b_provider_returns_required_fields(monkeypatch):
    """[TP-SCAN-001b] get_all_listed_stocks() returns list of dicts with
    stock_no, stock_name, yesterday_close, market. (EDD §14.2)
    """
    TwseAllListedStocksProvider = require_symbol(
        "stock_monitor.adapters.all_listed_stocks_twse",
        "TwseAllListedStocksProvider",
        "TP-SCAN-001b",
    )

    # Minimal JSON payload that looks like what TWSE returns.
    # Exact field names depend on the real API; the provider maps them.
    import json

    twse_payload = json.dumps({
        "stat": "OK",
        "fields": ["證券代號", "證券名稱", "收盤價"],
        "data": [
            ["2330", "台積電", "1050"],
            ["2317", "鴻海", "198.5"],
            ["00878", "ETF某某", "20.0"],  # should be filtered (not 4-digit)
        ],
    }).encode()

    # TPEx endpoint: empty JSON array → no OTC stocks in this mock
    tpex_payload = b"[]"

    def _fake_urlopen(req, timeout=None):
        url = getattr(req, "full_url", str(req))
        body = twse_payload if "twse.com.tw" in url else tpex_payload

        class _FakeResp:
            def read(self, n=-1):
                return body

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        return _FakeResp()

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

    provider = TwseAllListedStocksProvider()
    result = provider.get_all_listed_stocks()

    assert isinstance(result, list), "[TP-SCAN-001b] Should return a list."
    required_keys = {"stock_no", "stock_name", "yesterday_close", "market"}
    for row in result:
        assert required_keys.issubset(row.keys()), (
            f"[TP-SCAN-001b] Row missing required keys. Got: {set(row.keys())}"
        )
    # 00878 is not 4-digit numeric → must be filtered out
    codes = {row["stock_no"] for row in result}
    assert "00878" not in codes, (
        "[TP-SCAN-001b] ETF/non-4-digit codes must be filtered out."
    )


# ---------------------------------------------------------------------------
# TP-SCAN-002  HTTP failure → exception raised (not silent empty list)
# ---------------------------------------------------------------------------

def test_tp_scan_002_http_failure_raises(monkeypatch):
    """[TP-SCAN-002] When all HTTP retries fail, get_all_listed_stocks() must raise,
    not silently return an empty list. (PDD FR-19 fail-fast / API_CONTRACT §5.8 >
    EDD §14.1 which says return empty list — PDD wins here.)
    """
    TwseAllListedStocksProvider = require_symbol(
        "stock_monitor.adapters.all_listed_stocks_twse",
        "TwseAllListedStocksProvider",
        "TP-SCAN-002",
    )
    attempt_count = {"n": 0}

    def _always_fail(req, timeout=None):
        attempt_count["n"] += 1
        raise URLError("connection refused")

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", _always_fail)

    provider = TwseAllListedStocksProvider()
    with pytest.raises(Exception):
        provider.get_all_listed_stocks()

    assert attempt_count["n"] >= 1, (
        "[TP-SCAN-002] Expected at least one HTTP attempt before raising."
    )


# ---------------------------------------------------------------------------
# TP-SCAN-003  run_market_scan_job three-way classification
# ---------------------------------------------------------------------------

def test_tp_scan_003_three_classification(tmp_path):
    """[TP-SCAN-003] run_market_scan_job routes stocks into three buckets:
    below_cheap → watchlist_upserted,
    near_fair → near_fair_count (scan_YYYYMMDD_near_fair.csv),
    all_skip → uncalculable_count (scan_YYYYMMDD_uncalculable.csv).
    agg aggregation uses max(), not arithmetic mean.
    (EDD §14.3 / PDD FR-19)
    """
    run_market_scan_job = require_symbol(
        "stock_monitor.application.market_scan",
        "run_market_scan_job",
        "TP-SCAN-003",
    )
    MarketScanResult = require_symbol(
        "stock_monitor.application.market_scan",
        "MarketScanResult",
        "TP-SCAN-003",
    )
    db_path = _make_db(tmp_path)
    output_dir = str(tmp_path / "output")

    # Stock 1111: close=80, agg_cheap=100, agg_fair=150 → below cheap → watchlist
    # Stock 2222: close=120, agg_cheap=100, agg_fair=150 → above cheap, below fair → near_fair
    # Stock 3333: all methods SKIP → uncalculable
    stocks = [
        {"stock_no": "1111", "stock_name": "股票甲", "yesterday_close": 80.0, "market": "TWSE"},
        {"stock_no": "2222", "stock_name": "股票乙", "yesterday_close": 120.0, "market": "TWSE"},
        {"stock_no": "3333", "stock_name": "股票丙", "yesterday_close": 200.0, "market": "TWSE"},
    ]
    method = _StubMethod(
        "m_test",
        results_by_stock={
            "1111": {"fair": 150.0, "cheap": 100.0},
            "2222": {"fair": 150.0, "cheap": 100.0},
        },
    )

    result = run_market_scan_job(
        db_path=db_path,
        output_dir=output_dir,
        stocks_provider=_StubProvider(stocks),
        valuation_methods=[method],
    )

    assert isinstance(result, MarketScanResult), (
        "[TP-SCAN-003] run_market_scan_job must return a MarketScanResult."
    )
    assert result.total_stocks == 3, (
        f"[TP-SCAN-003] Expected total_stocks=3, got {result.total_stocks}"
    )
    assert result.watchlist_upserted == 1, (
        f"[TP-SCAN-003] Expected watchlist_upserted=1, got {result.watchlist_upserted}"
    )
    assert result.near_fair_count == 1, (
        f"[TP-SCAN-003] Expected near_fair_count=1, got {result.near_fair_count}"
    )
    assert result.uncalculable_count == 1, (
        f"[TP-SCAN-003] Expected uncalculable_count=1, got {result.uncalculable_count}"
    )
    assert result.above_fair_count == 0, (
        f"[TP-SCAN-003] Expected above_fair_count=0, got {result.above_fair_count}"
    )


# ---------------------------------------------------------------------------
# TP-SCAN-003b  agg aggregation uses max() not arithmetic mean
# ---------------------------------------------------------------------------

def test_tp_scan_003b_agg_uses_max(tmp_path):
    """[TP-SCAN-003b] When multiple methods succeed, agg_fair/cheap = max(), not mean().
    E.g. method A: fair=150, cheap=100; method B: fair=200, cheap=120
    → agg_fair=200 (max), agg_cheap=120 (max), not 175/110 (mean).
    (EDD §14.3 / PDD FR-19 gap-1 fix)
    """
    run_market_scan_job = require_symbol(
        "stock_monitor.application.market_scan",
        "run_market_scan_job",
        "TP-SCAN-003b",
    )
    db_path = _make_db(tmp_path)
    output_dir = str(tmp_path / "output")

    # Stock close=80, will be below cheap regardless → triggers watchlist upsert
    stocks = [{"stock_no": "8888", "stock_name": "聚合股", "yesterday_close": 80.0, "market": "TWSE"}]
    method_a = _StubMethod("method_a", results_by_stock={"8888": {"fair": 150.0, "cheap": 100.0}})
    method_b = _StubMethod("method_b", results_by_stock={"8888": {"fair": 200.0, "cheap": 120.0}})

    import sqlite3 as _sqlite3
    run_market_scan_job(
        db_path=db_path,
        output_dir=output_dir,
        stocks_provider=_StubProvider(stocks),
        valuation_methods=[method_a, method_b],
    )

    from stock_monitor.adapters.sqlite_repo import connect_sqlite
    conn = connect_sqlite(db_path)
    row = conn.execute(
        "SELECT manual_fair_price, manual_cheap_price FROM watchlist WHERE stock_no=?",
        ("8888",),
    ).fetchone()
    conn.close()

    assert row is not None, "[TP-SCAN-003b] Stock 8888 must be upserted into watchlist."
    agg_fair = float(row["manual_fair_price"])
    agg_cheap = float(row["manual_cheap_price"])
    assert agg_fair == 200.0, (
        f"[TP-SCAN-003b] agg_fair must be max(150,200)=200. Got {agg_fair}. "
        "Aggregation must use max(), not arithmetic mean (175)."
    )
    assert agg_cheap == 120.0, (
        f"[TP-SCAN-003b] agg_cheap must be max(100,120)=120. Got {agg_cheap}. "
        "Aggregation must use max(), not arithmetic mean (110)."
    )


# ---------------------------------------------------------------------------
# TP-SCAN-004  Watchlist upsert preserves existing enabled flag
# ---------------------------------------------------------------------------

def test_tp_scan_004_watchlist_upsert_preserves_enabled_flag(tmp_path):
    """[TP-SCAN-004] A watchlist record with enabled=0 must keep enabled=0 after
    upsert; only stock_name/fair/cheap should be updated. (EDD §14.3 Upsert SQL)
    """
    run_market_scan_job = require_symbol(
        "stock_monitor.application.market_scan",
        "run_market_scan_job",
        "TP-SCAN-004",
    )
    from stock_monitor.adapters.sqlite_repo import apply_schema, connect_sqlite

    db_path = _make_db(tmp_path)
    output_dir = str(tmp_path / "output")

    # Pre-insert 2330 with enabled=0 (intentionally disabled)
    conn = connect_sqlite(db_path)
    now = 1_000_000_000
    conn.execute(
        "INSERT INTO watchlist (stock_no, stock_name, manual_fair_price, manual_cheap_price, enabled, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("2330", "台積電舊名", 900.0, 800.0, 0, now, now),
    )
    conn.commit()
    conn.close()

    # 2330: pre-existing, close=500, cheap=600, fair=800 → below cheap → UPDATE
    # 6666: brand new, close=50, cheap=100, fair=150 → below cheap → INSERT (new)
    stocks = [
        {"stock_no": "2330", "stock_name": "台積電新名", "yesterday_close": 500.0, "market": "TWSE"},
        {"stock_no": "6666", "stock_name": "新股票", "yesterday_close": 50.0, "market": "TWSE"},
    ]
    method = _StubMethod("m_test", results_by_stock={
        "2330": {"fair": 800.0, "cheap": 600.0},
        "6666": {"fair": 150.0, "cheap": 100.0},
    })

    result = run_market_scan_job(
        db_path=db_path,
        output_dir=output_dir,
        stocks_provider=_StubProvider(stocks),
        valuation_methods=[method],
    )

    conn2 = connect_sqlite(db_path)
    row = conn2.execute("SELECT stock_name, manual_fair_price, manual_cheap_price, enabled FROM watchlist WHERE stock_no=?", ("2330",)).fetchone()
    conn2.close()

    assert row is not None, "[TP-SCAN-004] Stock 2330 should exist in watchlist after upsert."
    assert row["stock_name"] == "台積電新名", (
        f"[TP-SCAN-004] stock_name should be updated. Got: {row['stock_name']}"
    )
    assert float(row["manual_fair_price"]) == 800.0, (
        f"[TP-SCAN-004] manual_fair_price should be updated. Got: {row['manual_fair_price']}"
    )
    assert int(row["enabled"]) == 0, (
        f"[TP-SCAN-004] enabled=0 must NOT be overwritten to 1. Got: {row['enabled']}"
    )
    # SELECT-before-upsert: distinguish new vs updated (EDD §14.3 / ADR-016)
    assert result.watchlist_upserted == 2, (
        f"[TP-SCAN-004] Expected watchlist_upserted=2. Got: {result.watchlist_upserted}"
    )
    assert result.watchlist_updated == 1, (
        f"[TP-SCAN-004] 2330 was pre-existing → watchlist_updated must be 1. Got: {result.watchlist_updated}"
    )
    assert result.watchlist_new == 1, (
        f"[TP-SCAN-004] 6666 was new → watchlist_new must be 1. Got: {result.watchlist_new}"
    )


# ---------------------------------------------------------------------------
# TP-SCAN-005  near_fair CSV output with correct columns
# ---------------------------------------------------------------------------

def test_tp_scan_005_near_fair_csv_output(tmp_path):
    """[TP-SCAN-005] near_fair stocks are written to scan_YYYYMMDD_near_fair.csv
    with 7 required columns (methods_success + methods_skipped; no skip_reasons).
    (PDD FR-19 / EDD §14.4 / gap-3 fix)
    """
    run_market_scan_job = require_symbol(
        "stock_monitor.application.market_scan",
        "run_market_scan_job",
        "TP-SCAN-005",
    )
    db_path = _make_db(tmp_path)
    output_dir = str(tmp_path / "output")

    # Stock close=120, cheap=100, fair=150 → near_fair
    stocks = [{"stock_no": "4321", "stock_name": "測試股", "yesterday_close": 120.0, "market": "TWSE"}]
    method = _StubMethod("m_test", results_by_stock={"4321": {"fair": 150.0, "cheap": 100.0}})

    run_market_scan_job(
        db_path=db_path,
        output_dir=output_dir,
        stocks_provider=_StubProvider(stocks),
        valuation_methods=[method],
    )

    from datetime import date as _date
    scan_date = _date.today().strftime("%Y%m%d")
    csv_path = Path(output_dir) / f"scan_{scan_date}_near_fair.csv"
    assert csv_path.exists(), (
        f"[TP-SCAN-005] scan_{scan_date}_near_fair.csv not found at {csv_path}. "
        "CSV must use YYYYMMDD date prefix (not 'scan_results_above_cheap')."
    )
    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    assert len(rows) == 1, f"[TP-SCAN-005] Expected 1 row in CSV, got {len(rows)}"
    actual_cols = set(rows[0].keys())
    required_cols = {
        "stock_no", "stock_name", "agg_fair_price", "agg_cheap_price",
        "yesterday_close", "methods_success", "methods_skipped",
    }
    assert required_cols.issubset(actual_cols), (
        f"[TP-SCAN-005] CSV missing required columns.\n"
        f"  Required: {sorted(required_cols)}\n"
        f"  Got:      {sorted(actual_cols)}"
    )
    assert "skip_reasons" not in actual_cols, (
        "[TP-SCAN-005] CSV must NOT have a 'skip_reasons' column — "
        "skip reasons are embedded in methods_skipped (method:reason format)."
    )
    assert rows[0]["stock_no"] == "4321", (
        f"[TP-SCAN-005] Expected stock_no='4321'. Got: {rows[0]['stock_no']}"
    )


# ---------------------------------------------------------------------------
# TP-SCAN-006  Per-stock exception isolation + MARKET_SCAN_STOCK_ERROR log
# ---------------------------------------------------------------------------

def test_tp_scan_006_per_stock_exception_does_not_abort_scan(tmp_path):
    """[TP-SCAN-006] If a stock's compute() raises, the scan must:
    1. Write MARKET_SCAN_STOCK_ERROR to system_logs (level=ERROR).
    2. Continue processing remaining stocks without aborting.
    3. Include all-SKIP stocks in scan_results_uncalculable.csv.
    (EDD §14.3 error isolation / PDD FR-19 §19.3)
    """
    run_market_scan_job = require_symbol(
        "stock_monitor.application.market_scan",
        "run_market_scan_job",
        "TP-SCAN-006",
    )
    MarketScanResult = require_symbol(
        "stock_monitor.application.market_scan",
        "MarketScanResult",
        "TP-SCAN-006",
    )
    from stock_monitor.adapters.sqlite_repo import connect_sqlite

    db_path = _make_db(tmp_path)
    output_dir = str(tmp_path / "output")

    stocks = [
        # 5555: all methods SKIP → goes to uncalculable CSV
        {"stock_no": "5555", "stock_name": "跳過股", "yesterday_close": 100.0, "market": "TWSE"},
        # 6666: method raises RuntimeError → error-isolated, written to system_logs
        {"stock_no": "6666", "stock_name": "例外股", "yesterday_close": 100.0, "market": "TWSE"},
        # 7777: SUCCESS, near_fair → scan continues to completion
        {"stock_no": "7777", "stock_name": "正常股", "yesterday_close": 120.0, "market": "TWSE"},
    ]

    raising_method = _StubMethod(
        "m_test",
        results_by_stock={
            "6666": RuntimeError("simulated compute failure"),
            "7777": {"fair": 150.0, "cheap": 100.0},
        },
    )

    result = run_market_scan_job(
        db_path=db_path,
        output_dir=output_dir,
        stocks_provider=_StubProvider(stocks),
        valuation_methods=[raising_method],
    )

    # Scan must complete (not raise)
    assert isinstance(result, MarketScanResult), (
        "[TP-SCAN-006] Scan must complete and return MarketScanResult even if some stocks error."
    )

    # 5555 should appear in uncalculable CSV with embedded method:reason format
    from datetime import date as _date
    scan_date = _date.today().strftime("%Y%m%d")
    uncalc_path = Path(output_dir) / f"scan_{scan_date}_uncalculable.csv"
    assert uncalc_path.exists(), (
        f"[TP-SCAN-006] scan_{scan_date}_uncalculable.csv not found at {uncalc_path}. "
        "Must use YYYYMMDD date prefix (not 'scan_results_uncalculable')."
    )
    with uncalc_path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    skip_stock_nos = {r["stock_no"] for r in rows}
    assert "5555" in skip_stock_nos, (
        f"[TP-SCAN-006] Stock 5555 (all SKIP) must appear in uncalculable CSV. Got: {skip_stock_nos}"
    )
    # methods_skipped must contain embedded method:reason (no separate skip_reasons column)
    stock_5555_row = next(r for r in rows if r["stock_no"] == "5555")
    assert "skip_reasons" not in stock_5555_row, (
        "[TP-SCAN-006] CSV must NOT have 'skip_reasons' column."
    )
    methods_skipped_val = stock_5555_row.get("methods_skipped", "")
    assert ":" in methods_skipped_val, (
        f"[TP-SCAN-006] methods_skipped must use 'method:reason' format. Got: '{methods_skipped_val}'"
    )

    # system_logs must contain MARKET_SCAN_STOCK_ERROR for stock 6666
    conn = connect_sqlite(db_path)
    error_rows = conn.execute(
        "SELECT detail FROM system_logs WHERE level='ERROR' AND event='MARKET_SCAN_STOCK_ERROR'"
    ).fetchall()
    conn.close()

    assert len(error_rows) >= 1, (
        "[TP-SCAN-006] Expected at least one MARKET_SCAN_STOCK_ERROR in system_logs."
    )
    details_combined = " ".join(r[0] or "" for r in error_rows)
    assert "6666" in details_combined, (
        "[TP-SCAN-006] MARKET_SCAN_STOCK_ERROR detail must reference stock 6666."
    )


# ---------------------------------------------------------------------------
# Coverage gap tests (branch completeness)
# ---------------------------------------------------------------------------

def test_no_price_stock_goes_to_uncalculable(tmp_path):
    """Stock with yesterday_close=None → uncalculable (NO_PRICE)."""
    run_market_scan_job = require_symbol(
        "stock_monitor.application.market_scan", "run_market_scan_job", "TP-SCAN-003",
    )
    MarketScanResult = require_symbol(
        "stock_monitor.application.market_scan", "MarketScanResult", "TP-SCAN-003",
    )
    db_path = _make_db(tmp_path)
    output_dir = str(tmp_path / "output")

    stocks = [{"stock_no": "9001", "stock_name": "無價格股", "yesterday_close": None, "market": "TWSE"}]
    method = _StubMethod("m_test", results_by_stock={"9001": {"fair": 100.0, "cheap": 80.0}})

    result = run_market_scan_job(
        db_path=db_path,
        output_dir=output_dir,
        stocks_provider=_StubProvider(stocks),
        valuation_methods=[method],
    )

    assert isinstance(result, MarketScanResult)
    assert result.uncalculable_count == 1
    assert result.watchlist_upserted == 0
    assert result.near_fair_count == 0

    from datetime import date as _date
    scan_date = _date.today().strftime("%Y%m%d")
    uncalc_path = Path(output_dir) / f"scan_{scan_date}_uncalculable.csv"
    assert uncalc_path.exists(), (
        f"[no_price_test] scan_{scan_date}_uncalculable.csv not found at {uncalc_path}"
    )
    with uncalc_path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert any(r["stock_no"] == "9001" for r in rows)


def test_above_fair_stock_not_reported(tmp_path):
    """Stock with close > agg_fair → not added to any bucket or CSV."""
    run_market_scan_job = require_symbol(
        "stock_monitor.application.market_scan", "run_market_scan_job", "TP-SCAN-003",
    )
    MarketScanResult = require_symbol(
        "stock_monitor.application.market_scan", "MarketScanResult", "TP-SCAN-003",
    )
    db_path = _make_db(tmp_path)
    output_dir = str(tmp_path / "output")

    # close=300, fair=150 → above fair → not reported
    stocks = [{"stock_no": "9002", "stock_name": "高價股", "yesterday_close": 300.0, "market": "TWSE"}]
    method = _StubMethod("m_test", results_by_stock={"9002": {"fair": 150.0, "cheap": 100.0}})

    result = run_market_scan_job(
        db_path=db_path,
        output_dir=output_dir,
        stocks_provider=_StubProvider(stocks),
        valuation_methods=[method],
    )

    assert isinstance(result, MarketScanResult)
    assert result.watchlist_upserted == 0
    assert result.near_fair_count == 0
    assert result.uncalculable_count == 0
    assert result.total_stocks == 1


def test_success_with_null_prices_treated_as_skip(tmp_path):
    """SUCCESS result with fair=None or cheap=None → treated as no-data, not counted."""
    run_market_scan_job = require_symbol(
        "stock_monitor.application.market_scan", "run_market_scan_job", "TP-SCAN-003",
    )
    db_path = _make_db(tmp_path)
    output_dir = str(tmp_path / "output")

    class _NullPriceMethod:
        method_name = "null_method"
        method_version = "v1"

        def compute(self, stock_no, trade_date_local):
            return {
                "status": "SUCCESS",
                "fair_price": None,
                "cheap_price": None,
                "method_name": self.method_name,
                "method_version": self.method_version,
            }

    stocks = [{"stock_no": "9003", "stock_name": "空值股", "yesterday_close": 100.0, "market": "TWSE"}]
    result = run_market_scan_job(
        db_path=db_path,
        output_dir=output_dir,
        stocks_provider=_StubProvider(stocks),
        valuation_methods=[_NullPriceMethod()],
    )

    # All methods returned null prices -> treated as all-skip → uncalculable
    assert result.uncalculable_count == 1
    assert result.watchlist_upserted == 0


# ---------------------------------------------------------------------------
# TwseAllListedStocksProvider adapter branch coverage
# ---------------------------------------------------------------------------

def _make_urlopen(twse_body: bytes, tpex_body: bytes):
    """Return a fake urlopen that serves twse_body for TWSE URL and tpex_body for TPEx URL."""
    import urllib.request

    def _fake(req, timeout=None):
        url = getattr(req, "full_url", str(req))
        body = twse_body if "twse.com.tw" in url else tpex_body

        class _R:
            def read(self, n=-1):
                return body
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False

        return _R()

    return _fake


def test_provider_excludes_4digit_code_with_excluded_name(monkeypatch):
    """Line 39: _is_ordinary_stock returns False for 4-digit code with excluded keyword."""
    import json as _json
    import urllib.request
    TwseAllListedStocksProvider = require_symbol(
        "stock_monitor.adapters.all_listed_stocks_twse",
        "TwseAllListedStocksProvider",
        "TP-SCAN-001",
    )
    twse = _json.dumps({
        "stat": "OK",
        "fields": ["證券代號", "證券名稱", "收盤價"],
        "data": [
            ["2330", "台積電", "1050"],
            ["1234", "ETF台股", "50.0"],  # excluded keyword "ETF"
        ],
    }).encode()
    monkeypatch.setattr(urllib.request, "urlopen", _make_urlopen(twse, b"[]"))
    provider = TwseAllListedStocksProvider()
    result = provider.get_all_listed_stocks()
    codes = {r["stock_no"] for r in result}
    assert "1234" not in codes, "Stock with ETF keyword should be excluded"
    assert "2330" in codes


def test_provider_price_edge_cases(monkeypatch):
    """Lines 46, 49, 52-53: _to_float_price with None, dash, empty, and invalid input."""
    import json as _json
    import urllib.request
    TwseAllListedStocksProvider = require_symbol(
        "stock_monitor.adapters.all_listed_stocks_twse",
        "TwseAllListedStocksProvider",
        "TP-SCAN-001",
    )
    twse = _json.dumps({
        "stat": "OK",
        "fields": ["證券代號", "證券名稱", "收盤價"],
        "data": [
            ["2330", "台積電", "-"],      # dash → None (line 49)
            ["2317", "鴻海", ""],         # empty → None (line 49)
            ["2454", "聯發科", "abc"],    # invalid → None (line 52-53)
            ["2412", "中華電"],           # row too short → close_raw=None → _to_float_price(None) (line 46)
        ],
    }).encode()
    monkeypatch.setattr(urllib.request, "urlopen", _make_urlopen(twse, b"[]"))
    provider = TwseAllListedStocksProvider()
    result = provider.get_all_listed_stocks()
    closes = {r["stock_no"]: r["yesterday_close"] for r in result}
    assert closes.get("2330") is None
    assert closes.get("2317") is None
    assert closes.get("2454") is None
    assert closes.get("2412") is None


def test_provider_twse_non_dict_response_raises(monkeypatch):
    """Line 84: TWSE returns non-dict payload → RuntimeError propagated → get_all raises."""
    import urllib.request
    TwseAllListedStocksProvider = require_symbol(
        "stock_monitor.adapters.all_listed_stocks_twse",
        "TwseAllListedStocksProvider",
        "TP-SCAN-001",
    )
    monkeypatch.setattr(urllib.request, "urlopen", _make_urlopen(b"[]", b"[]"))
    provider = TwseAllListedStocksProvider()
    with pytest.raises(Exception):
        provider.get_all_listed_stocks()


def test_provider_twse_field_fallback(monkeypatch):
    """Lines 93-95: TWSE fields missing expected names → fallback to columns 0,1,8."""
    import json as _json
    import urllib.request
    TwseAllListedStocksProvider = require_symbol(
        "stock_monitor.adapters.all_listed_stocks_twse",
        "TwseAllListedStocksProvider",
        "TP-SCAN-001",
    )
    # fields don't contain expected column names → fallback: 0=code, 1=name, 8=close
    row = ["2330", "台積電", "col2", "col3", "col4", "col5", "col6", "col7", "1050"]
    twse = _json.dumps({
        "stat": "OK",
        "fields": ["CodeX", "NameX", "c2", "c3", "c4", "c5", "c6", "c7", "CloseX"],
        "data": [row],
    }).encode()
    monkeypatch.setattr(urllib.request, "urlopen", _make_urlopen(twse, b"[]"))
    provider = TwseAllListedStocksProvider()
    result = provider.get_all_listed_stocks()
    assert any(r["stock_no"] == "2330" for r in result)


def test_provider_twse_row_index_error_skips(monkeypatch):
    """Lines 103-104: TWSE data row too short → IndexError → row skipped."""
    import json as _json
    import urllib.request
    TwseAllListedStocksProvider = require_symbol(
        "stock_monitor.adapters.all_listed_stocks_twse",
        "TwseAllListedStocksProvider",
        "TP-SCAN-001",
    )
    twse = _json.dumps({
        "stat": "OK",
        "fields": ["證券代號", "證券名稱", "收盤價"],
        "data": [
            ["2330", "台積電", "1050"],
            [],  # too short → IndexError → skip
        ],
    }).encode()
    monkeypatch.setattr(urllib.request, "urlopen", _make_urlopen(twse, b"[]"))
    provider = TwseAllListedStocksProvider()
    result = provider.get_all_listed_stocks()
    assert len(result) == 1
    assert result[0]["stock_no"] == "2330"


def test_provider_tpex_non_list_response_tolerated(monkeypatch):
    """Lines 123, 174-175: TPEx returns non-list → RuntimeError caught → partial result."""
    import json as _json
    import urllib.request
    TwseAllListedStocksProvider = require_symbol(
        "stock_monitor.adapters.all_listed_stocks_twse",
        "TwseAllListedStocksProvider",
        "TP-SCAN-001",
    )
    twse = _json.dumps({
        "stat": "OK",
        "fields": ["證券代號", "證券名稱", "收盤價"],
        "data": [["2330", "台積電", "1050"]],
    }).encode()
    tpex_bad = b'{"error": "unexpected"}'  # dict, not list → RuntimeError
    monkeypatch.setattr(urllib.request, "urlopen", _make_urlopen(twse, tpex_bad))
    provider = TwseAllListedStocksProvider()
    result = provider.get_all_listed_stocks()
    # TPEx failure tolerated: only TWSE stock returned
    codes = {r["stock_no"] for r in result}
    assert "2330" in codes


def test_provider_tpex_non_empty_ordinary_stocks(monkeypatch):
    """Lines 127-134: TPEx with ordinary stock dict items → included in result."""
    import json as _json
    import urllib.request
    TwseAllListedStocksProvider = require_symbol(
        "stock_monitor.adapters.all_listed_stocks_twse",
        "TwseAllListedStocksProvider",
        "TP-SCAN-001",
    )
    twse = _json.dumps({
        "stat": "OK",
        "fields": ["證券代號", "證券名稱", "收盤價"],
        "data": [["2330", "台積電", "1050"]],
    }).encode()
    tpex = _json.dumps([
        {"SecuritiesCompanyCode": "6488", "CompanyName": "環球晶", "Close": "350.5"},
        "not_a_dict",                    # non-dict item → skipped (line 127-128)
        {"SecuritiesCompanyCode": "1234", "CompanyName": "ETF某上櫃", "Close": "25"},  # ETF excluded
    ]).encode()
    monkeypatch.setattr(urllib.request, "urlopen", _make_urlopen(twse, tpex))
    provider = TwseAllListedStocksProvider()
    result = provider.get_all_listed_stocks()
    codes = {r["stock_no"] for r in result}
    assert "2330" in codes
    assert "6488" in codes
    assert "1234" not in codes  # ETF excluded


def test_provider_twse_all_filtered_raises(monkeypatch):
    """Line 168: TWSE returns data but all entries filtered → RuntimeError."""
    import json as _json
    import urllib.request
    TwseAllListedStocksProvider = require_symbol(
        "stock_monitor.adapters.all_listed_stocks_twse",
        "TwseAllListedStocksProvider",
        "TP-SCAN-001",
    )
    twse = _json.dumps({
        "stat": "OK",
        "fields": ["證券代號", "證券名稱", "收盤價"],
        "data": [
            ["00878", "ETF國泰", "20"],           # not 4-digit → filtered
            ["1234", "認購新秀", "30"],            # 認購 keyword → filtered
        ],
    }).encode()
    monkeypatch.setattr(urllib.request, "urlopen", _make_urlopen(twse, b"[]"))
    provider = TwseAllListedStocksProvider()
    with pytest.raises(Exception):
        provider.get_all_listed_stocks()


def test_provider_tpex_fetch_exception_tolerated(monkeypatch):
    """Lines 174-175: TPEx urlopen raises → caught → partial result from TWSE only."""
    import json as _json
    import urllib.request
    TwseAllListedStocksProvider = require_symbol(
        "stock_monitor.adapters.all_listed_stocks_twse",
        "TwseAllListedStocksProvider",
        "TP-SCAN-001",
    )
    twse_body = _json.dumps({
        "stat": "OK",
        "fields": ["證券代號", "證券名稱", "收盤價"],
        "data": [["2330", "台積電", "1050"]],
    }).encode()

    def _fake_urlopen(req, timeout=None):
        url = getattr(req, "full_url", str(req))
        if "tpex.org.tw" in url:
            raise URLError("tpex unreachable")

        class _R:
            def read(self, n=-1):
                return twse_body
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False

        return _R()

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    provider = TwseAllListedStocksProvider()
    result = provider.get_all_listed_stocks()
    assert any(r["stock_no"] == "2330" for r in result)
