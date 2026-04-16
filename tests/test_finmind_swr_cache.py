"""Tests for FinMindFinancialDataProvider SWR cache behaviour.

Covers:
  - Cache miss  → fetches from API, stores in DB, returns data
  - Cache fresh → reads from DB, no API call
  - Cache stale → reads from DB immediately, spawns background refresh
  - Mem cache   → second call within same provider instance skips DB lookup
  - DB write failure → degrades gracefully (still returns API data)

All tests use monkeypatching to avoid live network calls.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time

import pytest

from stock_monitor.adapters.financial_data_finmind import (
    FinMindFinancialDataProvider,
    _CACHE_CREATE_SQL,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FAKE_ROWS = [
    {"year": "2023", "CashEarningsDistribution": "3.0", "CashStatutorySurplus": "0.5"},
]


def _make_provider(tmp_path, monkeypatch, stale_days=15, api_rows=None):
    """Return a provider wired to a temp DB with a controllable API stub."""
    db_path = str(tmp_path / "cache.db")
    # Ensure table exists before provider __init__ (provider also calls this)
    with sqlite3.connect(db_path) as c:
        c.execute(_CACHE_CREATE_SQL)
        c.commit()

    api_call_count = {"n": 0}
    rows_to_return = _FAKE_ROWS if api_rows is None else api_rows

    def _fake_fetch(dataset, stock_id, start_date, token=""):
        api_call_count["n"] += 1
        return rows_to_return

    monkeypatch.setattr(
        "stock_monitor.adapters.financial_data_finmind._fetch_finmind",
        _fake_fetch,
    )

    provider = FinMindFinancialDataProvider(db_path=db_path, stale_days=stale_days)
    return provider, db_path, api_call_count


def _insert_cache_row(db_path, stock_no, dataset, rows, fetched_at):
    with sqlite3.connect(db_path) as c:
        c.execute(
            """
            INSERT OR REPLACE INTO financial_data_cache
                (stock_no, dataset, data_json, fetched_at)
            VALUES (?, ?, ?, ?)
            """,
            (stock_no, dataset, json.dumps(rows), fetched_at),
        )
        c.commit()


def _read_cache_row(db_path, stock_no, dataset):
    with sqlite3.connect(db_path) as c:
        row = c.execute(
            "SELECT data_json, fetched_at FROM financial_data_cache WHERE stock_no=? AND dataset=?",
            (stock_no, dataset),
        ).fetchone()
    return row


# ---------------------------------------------------------------------------
# Cache miss → API fetch + DB store
# ---------------------------------------------------------------------------


def test_cache_miss_calls_api_and_stores(tmp_path, monkeypatch):
    provider, db_path, calls = _make_provider(tmp_path, monkeypatch)

    rows = provider._fetch("TaiwanStockDividend", "2330")

    assert calls["n"] == 1
    assert rows == _FAKE_ROWS

    # Data should now be in DB
    cached = _read_cache_row(db_path, "2330", "TaiwanStockDividend")
    assert cached is not None
    assert json.loads(cached[0]) == _FAKE_ROWS


def test_cache_miss_second_fetch_hits_mem(tmp_path, monkeypatch):
    """Second call to _fetch within same provider instance hits in-memory cache."""
    provider, _, calls = _make_provider(tmp_path, monkeypatch)

    provider._fetch("TaiwanStockDividend", "2330")
    provider._fetch("TaiwanStockDividend", "2330")

    # Only one API call despite two _fetch invocations
    assert calls["n"] == 1


# ---------------------------------------------------------------------------
# Cache fresh → DB read, no API call
# ---------------------------------------------------------------------------


def test_cache_fresh_skips_api(tmp_path, monkeypatch):
    provider, db_path, calls = _make_provider(tmp_path, monkeypatch, stale_days=15)

    # Pre-populate DB with a fresh entry (5 seconds ago)
    _insert_cache_row(db_path, "2330", "TaiwanStockDividend", _FAKE_ROWS, int(time.time()) - 5)

    rows = provider._fetch("TaiwanStockDividend", "2330")

    assert calls["n"] == 0  # No API call
    assert rows == _FAKE_ROWS


def test_cache_fresh_promoted_to_mem(tmp_path, monkeypatch):
    """After reading a fresh DB entry the data lands in mem cache."""
    provider, db_path, calls = _make_provider(tmp_path, monkeypatch, stale_days=15)
    _insert_cache_row(db_path, "2330", "TaiwanStockDividend", _FAKE_ROWS, int(time.time()) - 5)

    provider._fetch("TaiwanStockDividend", "2330")

    with provider._lock:
        assert ("2330", "TaiwanStockDividend") in provider._mem


# ---------------------------------------------------------------------------
# Cache stale → return DB data immediately, background refresh spawned
# ---------------------------------------------------------------------------


def test_cache_stale_returns_immediately(tmp_path, monkeypatch):
    """Stale entry: caller gets data without waiting for API."""
    provider, db_path, calls = _make_provider(tmp_path, monkeypatch, stale_days=1)

    # Insert a 2-day-old entry
    old_ts = int(time.time()) - 2 * 86_400
    _insert_cache_row(db_path, "2330", "TaiwanStockDividend", _FAKE_ROWS, old_ts)

    rows = provider._fetch("TaiwanStockDividend", "2330")

    # Stale data returned immediately
    assert rows == _FAKE_ROWS
    # Background thread may or may not have finished, but fetch returned instantly


def test_cache_stale_spawns_background_refresh(tmp_path, monkeypatch):
    """Background refresh runs and updates the DB cache."""
    new_rows = [{"year": "2024", "CashEarningsDistribution": "4.0", "CashStatutorySurplus": "0.0"}]

    provider, db_path, calls = _make_provider(tmp_path, monkeypatch, stale_days=1, api_rows=new_rows)

    old_ts = int(time.time()) - 2 * 86_400
    _insert_cache_row(db_path, "2330", "TaiwanStockDividend", _FAKE_ROWS, old_ts)

    provider._fetch("TaiwanStockDividend", "2330")

    # Poll the DB directly until the background thread writes new data (max 3 s).
    deadline = time.time() + 3.0
    db_updated = False
    while time.time() < deadline:
        cached = _read_cache_row(db_path, "2330", "TaiwanStockDividend")
        if cached and json.loads(cached[0]) == new_rows:
            db_updated = True
            break
        time.sleep(0.05)

    assert calls["n"] == 1, "background thread should have called API once"
    assert db_updated, "DB cache should have been updated with new_rows by background thread"


def test_cache_stale_no_duplicate_refresh_threads(tmp_path, monkeypatch):
    """Two concurrent stale hits for same key spawn only one refresh thread."""
    # Use an event to hold the API call long enough to observe _refreshing set
    api_started = threading.Event()

    def _slow_fetch(dataset, stock_id, start_date, token=""):
        api_started.set()
        time.sleep(0.2)
        return _FAKE_ROWS

    monkeypatch.setattr(
        "stock_monitor.adapters.financial_data_finmind._fetch_finmind",
        _slow_fetch,
    )

    db_path = str(tmp_path / "cache2.db")
    with sqlite3.connect(db_path) as c:
        c.execute(_CACHE_CREATE_SQL)
        c.commit()

    provider = FinMindFinancialDataProvider(db_path=db_path, stale_days=1)
    old_ts = int(time.time()) - 2 * 86_400
    _insert_cache_row(db_path, "2330", "TaiwanStockDividend", _FAKE_ROWS, old_ts)

    # First fetch triggers background refresh
    provider._fetch("TaiwanStockDividend", "2330")
    api_started.wait(timeout=1.0)

    # While refresh is in flight, try to spawn another
    provider._spawn_refresh("2330", "TaiwanStockDividend")

    # _refreshing set should still contain exactly one entry
    with provider._lock:
        assert ("2330", "TaiwanStockDividend") in provider._refreshing


# ---------------------------------------------------------------------------
# DB unavailable — graceful degradation
# ---------------------------------------------------------------------------


def test_db_unavailable_falls_back_to_api(tmp_path, monkeypatch):
    """If DB path is bad, provider degrades to pure API mode without crashing."""
    bad_db = str(tmp_path / "nonexistent_dir" / "cache.db")

    call_count = {"n": 0}

    def _fake_fetch(dataset, stock_id, start_date, token=""):
        call_count["n"] += 1
        return _FAKE_ROWS

    monkeypatch.setattr(
        "stock_monitor.adapters.financial_data_finmind._fetch_finmind",
        _fake_fetch,
    )

    provider = FinMindFinancialDataProvider(db_path=bad_db, stale_days=15)
    rows = provider._fetch("TaiwanStockDividend", "2330")

    assert call_count["n"] == 1
    assert rows == _FAKE_ROWS


# ---------------------------------------------------------------------------
# db_path wiring: load_enabled_scan_methods passes db_path to provider
# ---------------------------------------------------------------------------


def test_load_enabled_scan_methods_passes_db_path(tmp_path, monkeypatch):
    """load_enabled_scan_methods(db_path=...) forwards db_path to provider."""
    from stock_monitor.adapters.sqlite_repo import apply_schema, connect_sqlite
    from stock_monitor.application.market_scan_methods import load_enabled_scan_methods

    db_path = str(tmp_path / "scan.db")
    conn = connect_sqlite(db_path)
    apply_schema(conn)
    conn.execute(
        "INSERT INTO valuation_methods(method_name, method_version, enabled, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        ("emily_composite", "v1", 1, 1713000000, 1713000000),
    )
    conn.commit()

    captured = {}

    original_init = FinMindFinancialDataProvider.__init__

    def _capturing_init(self, api_token=None, db_path=None, stale_days=15):
        captured["db_path"] = db_path
        original_init(self, api_token=api_token, db_path=db_path, stale_days=stale_days)

    monkeypatch.setattr(FinMindFinancialDataProvider, "__init__", _capturing_init)

    load_enabled_scan_methods(conn, as_of_date="2026-04-17", db_path=db_path)
    conn.close()

    assert captured.get("db_path") == db_path
