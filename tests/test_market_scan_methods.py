from __future__ import annotations

import pytest

from stock_monitor.adapters.sqlite_repo import apply_schema, connect_sqlite
from stock_monitor.application.market_scan_methods import (
    SnapshotBackedScanValuationMethod,
    load_enabled_scan_methods,
)


def _make_db(tmp_path):
    db_path = str(tmp_path / "scan_methods.db")
    conn = connect_sqlite(db_path)
    apply_schema(conn)
    conn.close()
    return db_path


def _seed_snapshot_prerequisites(conn, stock_no: str, method_name: str, method_version: str = "v1"):
    conn.execute(
        "INSERT OR IGNORE INTO watchlist(stock_no, stock_name, manual_fair_price, manual_cheap_price, enabled, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (str(stock_no), "測試股", 100.0, 80.0, 1, 1713000000, 1713000000),
    )
    conn.execute(
        "INSERT OR IGNORE INTO valuation_methods(method_name, method_version, enabled, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (str(method_name), str(method_version), 1, 1713000000, 1713000000),
    )


def test_load_enabled_scan_methods_raises_when_empty(tmp_path):
    db_path = _make_db(tmp_path)
    conn = connect_sqlite(db_path)
    try:
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
    assert names == ["emily_composite", "oldbull_dividend_yield", "raysky_blended_margin"]


def test_snapshot_method_compute_success_with_latest_snapshot(tmp_path):
    db_path = _make_db(tmp_path)
    conn = connect_sqlite(db_path)
    try:
        _seed_snapshot_prerequisites(conn, "2330", "emily_composite", "v1")
        conn.execute(
            "INSERT INTO valuation_snapshots(stock_no, trade_date, method_name, method_version, fair_price, cheap_price, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("2330", "2026-04-16", "emily_composite", "v1", 1500.0, 1200.0, 1713000000),
        )
        conn.execute(
            "INSERT INTO valuation_snapshots(stock_no, trade_date, method_name, method_version, fair_price, cheap_price, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("2330", "2026-04-17", "emily_composite", "v1", 1600.0, 1300.0, 1713000000),
        )
        conn.commit()

        method = SnapshotBackedScanValuationMethod(
            method_name="emily_composite",
            method_version="v1",
            conn=conn,
            as_of_date="2026-04-17",
        )
        result = method.compute("2330", "2026-04-17")
    finally:
        conn.close()

    assert result["status"] == "SUCCESS"
    assert result["method_name"] == "emily_composite"
    assert float(result["fair_price"]) == 1600.0
    assert float(result["cheap_price"]) == 1300.0


def test_snapshot_method_compute_skips_when_no_snapshot(tmp_path):
    db_path = _make_db(tmp_path)
    conn = connect_sqlite(db_path)
    try:
        method = SnapshotBackedScanValuationMethod(
            method_name="oldbull_dividend_yield",
            method_version="v1",
            conn=conn,
            as_of_date="2026-04-17",
        )
        result = method.compute("1101", "2026-04-17")
    finally:
        conn.close()

    assert result["status"] == "SKIP_INSUFFICIENT_DATA"
    assert result["fair_price"] is None
    assert result["cheap_price"] is None


def test_snapshot_method_compute_skips_on_bad_snapshot_values(tmp_path):
    class _FakeCursor:
        def fetchone(self):
            return ("N/A", "N/A")

    class _FakeConn:
        def execute(self, *_args, **_kwargs):
            return _FakeCursor()

    method = SnapshotBackedScanValuationMethod(
        method_name="raysky_blended_margin",
        method_version="v1",
        conn=_FakeConn(),
        as_of_date="2026-04-17",
    )
    result = method.compute("2330", "2026-04-17")

    assert result["status"] == "SKIP_INSUFFICIENT_DATA"
    assert result["fair_price"] is None
    assert result["cheap_price"] is None


def test_snapshot_method_compute_uses_as_of_date_cutoff(tmp_path):
    db_path = _make_db(tmp_path)
    conn = connect_sqlite(db_path)
    try:
        _seed_snapshot_prerequisites(conn, "2330", "emily_composite", "v1")
        conn.execute(
            "INSERT INTO valuation_snapshots(stock_no, trade_date, method_name, method_version, fair_price, cheap_price, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("2330", "2026-04-18", "emily_composite", "v1", 1700.0, 1400.0, 1713000000),
        )
        conn.commit()

        method = SnapshotBackedScanValuationMethod(
            method_name="emily_composite",
            method_version="v1",
            conn=conn,
            as_of_date="2026-04-17",
        )
        result = method.compute("2330", "2026-04-17")
    finally:
        conn.close()

    assert result["status"] == "SKIP_INSUFFICIENT_DATA"
