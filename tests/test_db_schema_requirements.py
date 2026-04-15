from __future__ import annotations

import sqlite3

from ._contract import require_symbol


def _load_schema_sql(test_id: str):
    # Contract: main code should expose complete schema SQL text.
    schema_sql = require_symbol("stock_monitor.db.schema", "SCHEMA_SQL", test_id)
    assert isinstance(schema_sql, str) and schema_sql.strip(), (
        f"[{test_id}] SCHEMA_SQL must be a non-empty SQL string."
    )
    return schema_sql


def _setup_in_memory_db(test_id: str):
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.executescript(_load_schema_sql(test_id))
    return conn


def test_tp_db_001_watchlist_constraints():
    conn = _setup_in_memory_db("TP-DB-001")
    try:
        conn.execute(
            """
            INSERT INTO watchlist(stock_no, manual_fair_price, manual_cheap_price, enabled, created_at, updated_at)
            VALUES ('2330', 1500, 1000, 1, 1712710000, 1712710000)
            """
        )
        try:
            conn.execute(
                """
                INSERT INTO watchlist(stock_no, manual_fair_price, manual_cheap_price, enabled, created_at, updated_at)
                VALUES ('2331', 900, 1000, 1, 1712710000, 1712710000)
                """
            )
            assert False, "[TP-DB-001] Expected CHECK failure when cheap > fair."
        except sqlite3.IntegrityError:
            pass
    finally:
        conn.close()


def test_tp_db_002_single_enabled_method_version():
    conn = _setup_in_memory_db("TP-DB-002")
    try:
        conn.execute(
            """
            INSERT INTO valuation_methods(method_name, method_version, enabled, created_at, updated_at)
            VALUES ('pe_band', 'v1', 1, 1712710000, 1712710000)
            """
        )
        try:
            conn.execute(
                """
                INSERT INTO valuation_methods(method_name, method_version, enabled, created_at, updated_at)
                VALUES ('pe_band', 'v2', 1, 1712710000, 1712710000)
                """
            )
            assert False, "[TP-DB-002] Expected unique failure for multiple enabled versions."
        except sqlite3.IntegrityError:
            pass
    finally:
        conn.close()


def test_tp_db_003_message_constraints():
    conn = _setup_in_memory_db("TP-DB-003")
    try:
        conn.execute(
            """
            INSERT INTO watchlist(stock_no, manual_fair_price, manual_cheap_price, enabled, created_at, updated_at)
            VALUES ('2330', 1500, 1000, 1, 1712710000, 1712710000)
            """
        )
        conn.execute(
            """
            INSERT INTO message(stock_no, message, stock_status, methods_hit, minute_bucket, update_time)
            VALUES ('2330', 'ok', 1, '["manual_rule"]', '2026-04-10 10:21', 1712710000)
            """
        )

        try:
            conn.execute(
                """
                INSERT INTO message(stock_no, message, stock_status, methods_hit, minute_bucket, update_time)
                VALUES ('2330', 'dup', 2, '["pe_band"]', '2026-04-10 10:21', 1712710060)
                """
            )
            assert False, "[TP-DB-003] Expected unique conflict on stock_no + minute_bucket."
        except sqlite3.IntegrityError:
            pass

        try:
            conn.execute(
                """
                INSERT INTO message(stock_no, message, stock_status, methods_hit, minute_bucket, update_time)
                VALUES ('2330', 'bad-json', 1, '{bad}', '2026-04-10 10:22', 1712710120)
                """
            )
            assert False, "[TP-DB-003] Expected json_valid CHECK failure."
        except sqlite3.IntegrityError:
            pass
    finally:
        conn.close()


def test_tp_db_004_pending_delivery_ledger_schema():
    conn = _setup_in_memory_db("TP-DB-004")
    try:
        conn.execute(
            """
            INSERT INTO pending_delivery_ledger(minute_bucket, payload_json, status, retry_count, created_at, updated_at)
            VALUES ('2026-04-10 10:21', '{"events":[{"stock":"2330"}]}', 'PENDING', 0, 1712710000, 1712710000)
            """
        )
        count = conn.execute("SELECT COUNT(*) FROM pending_delivery_ledger").fetchone()[0]
        assert count == 1, "[TP-DB-004] Expected one row inserted into pending_delivery_ledger."
    finally:
        conn.close()


def test_tp_db_005_valuation_snapshot_unique_includes_method_version():
    conn = _setup_in_memory_db("TP-DB-005")
    try:
        conn.execute(
            """
            INSERT INTO watchlist(stock_no, manual_fair_price, manual_cheap_price, enabled, created_at, updated_at)
            VALUES ('2330', 1500, 1000, 1, 1712710000, 1712710000)
            """
        )
        conn.execute(
            """
            INSERT INTO valuation_methods(method_name, method_version, enabled, created_at, updated_at)
            VALUES ('pe_band', 'v1', 1, 1712710000, 1712710000)
            """
        )
        conn.execute(
            """
            INSERT INTO valuation_methods(method_name, method_version, enabled, created_at, updated_at)
            VALUES ('pe_band', 'v2', 0, 1712710000, 1712710000)
            """
        )
        conn.execute(
            """
            INSERT INTO valuation_snapshots(stock_no, trade_date, method_name, method_version, fair_price, cheap_price, created_at)
            VALUES ('2330', '2026-04-10', 'pe_band', 'v1', 1500, 1000, 1712710000)
            """
        )
        conn.execute(
            """
            INSERT INTO valuation_snapshots(stock_no, trade_date, method_name, method_version, fair_price, cheap_price, created_at)
            VALUES ('2330', '2026-04-10', 'pe_band', 'v2', 1510, 1010, 1712710000)
            """
        )

        try:
            conn.execute(
                """
                INSERT INTO valuation_snapshots(stock_no, trade_date, method_name, method_version, fair_price, cheap_price, created_at)
                VALUES ('2330', '2026-04-10', 'pe_band', 'v1', 1520, 1020, 1712710000)
                """
            )
            assert False, (
                "[TP-DB-005] Duplicate stock_no+trade_date+method_name+method_version "
                "must fail by unique constraint."
            )
        except sqlite3.IntegrityError:
            pass
    finally:
        conn.close()


def test_tp_db_006_watchlist_stock_name_column():
    """FR-18: watchlist must have stock_name TEXT NOT NULL DEFAULT '' column."""
    conn = _setup_in_memory_db("TP-DB-006")
    try:
        col_names = [row[1] for row in conn.execute("PRAGMA table_info(watchlist)").fetchall()]
        assert "stock_name" in col_names, "[TP-DB-006] watchlist must have stock_name column."

        # INSERT without stock_name → default '' applies
        conn.execute(
            """
            INSERT INTO watchlist(stock_no, manual_fair_price, manual_cheap_price, enabled, created_at, updated_at)
            VALUES ('2330', 1500, 1000, 1, 1712710000, 1712710000)
            """
        )
        row = conn.execute("SELECT stock_name FROM watchlist WHERE stock_no = '2330'").fetchone()
        assert row[0] == "", "[TP-DB-006] stock_name default must be empty string."

        # UPDATE stock_name to Chinese name
        conn.execute("UPDATE watchlist SET stock_name = '台積電' WHERE stock_no = '2330'")
        row = conn.execute("SELECT stock_name FROM watchlist WHERE stock_no = '2330'").fetchone()
        assert row[0] == "台積電", "[TP-DB-006] stock_name must be updatable."
    finally:
        conn.close()


def test_tp_db_006_migration_adds_stock_name_to_legacy_db():
    """TP-DB-006 Migration: apply_schema must add stock_name column to old DB lacking it."""
    from stock_monitor.adapters.sqlite_repo import apply_schema

    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")
    # Simulate a legacy watchlist that has no stock_name column
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS watchlist (
            stock_no TEXT PRIMARY KEY,
            manual_fair_price NUMERIC NOT NULL CHECK (manual_fair_price > 0),
            manual_cheap_price NUMERIC NOT NULL CHECK (manual_cheap_price > 0),
            enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            CHECK (manual_cheap_price <= manual_fair_price)
        );
    """)
    conn.execute(
        "INSERT INTO watchlist(stock_no, manual_fair_price, manual_cheap_price, enabled, created_at, updated_at)"
        " VALUES ('2330', 1500, 1000, 1, 1712710000, 1712710000)"
    )
    conn.commit()

    # Pre-condition: stock_name must be absent
    cols_before = {row[1] for row in conn.execute("PRAGMA table_info(watchlist)").fetchall()}
    assert "stock_name" not in cols_before, "[TP-DB-006] setup error: legacy DB already has stock_name"

    apply_schema(conn)

    # Post-condition: stock_name should now exist with default ''
    cols_after = {row[1] for row in conn.execute("PRAGMA table_info(watchlist)").fetchall()}
    assert "stock_name" in cols_after, "[TP-DB-006] apply_schema must add stock_name to legacy DB"

    row = conn.execute("SELECT stock_name FROM watchlist WHERE stock_no = '2330'").fetchone()
    assert row[0] == "", "[TP-DB-006] migrated stock_name must default to empty string"
    conn.close()

