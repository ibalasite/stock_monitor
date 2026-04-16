from __future__ import annotations

import sqlite3

import pytest

from stock_monitor.adapters.sqlite_repo import apply_schema, connect_sqlite
from stock_monitor.application.market_scan_methods import (
    ScanValuationMethod,
    load_enabled_scan_methods,
)


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
            load_enabled_scan_methods(conn)
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

        methods = load_enabled_scan_methods(conn)
    finally:
        conn.close()

    names = [m.method_name for m in methods]
    assert names == ["emily_composite", "oldbull_dividend_yield", "raysky_blended_margin"]


def test_scan_method_compute_success_emily():
    method = ScanValuationMethod(method_name="emily_composite", method_version="v1")
    result = method.compute("2330", "2026-04-17")

    assert result["status"] == "SUCCESS"
    assert result["method_name"] == "emily_composite"
    assert float(result["cheap_price"]) <= float(result["fair_price"])


def test_scan_method_compute_success_oldbull_and_raysky():
    oldbull = ScanValuationMethod(method_name="oldbull_dividend_yield", method_version="v1")
    raysky = ScanValuationMethod(method_name="raysky_blended_margin", method_version="v1")

    o = oldbull.compute("1101", "2026-04-17")
    r = raysky.compute("1101", "2026-04-17")

    assert o["status"] == "SUCCESS"
    assert r["status"] == "SUCCESS"
    assert float(o["cheap_price"]) <= float(o["fair_price"])
    assert float(r["cheap_price"]) <= float(r["fair_price"])


def test_scan_method_compute_skips_on_bad_stock_no():
    method = ScanValuationMethod(method_name="emily_composite", method_version="v1")
    result = method.compute("BAD", "2026-04-17")
    assert result["status"] == "SKIP_INSUFFICIENT_DATA"
    assert result["fair_price"] is None
    assert result["cheap_price"] is None


def test_scan_method_compute_skips_unknown_method():
    method = ScanValuationMethod(method_name="unknown_method", method_version="v1")
    result = method.compute("2330", "2026-04-17")
    assert result["status"] == "SKIP_UNSUPPORTED_METHOD"
    assert result["fair_price"] is None
    assert result["cheap_price"] is None
