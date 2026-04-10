from __future__ import annotations

import json
from pathlib import Path

from stock_monitor.adapters.sqlite_repo import (
    JsonlPendingFallback,
    SqliteLogger,
    SqliteMessageRepository,
    SqlitePendingRepository,
    SqliteValuationSnapshotRepository,
    SqliteWatchlistRepository,
    apply_schema,
    connect_sqlite,
)


def test_sqlite_repositories_end_to_end(tmp_path: Path):
    db_path = tmp_path / "stock.db"
    conn = connect_sqlite(str(db_path))
    try:
        apply_schema(conn)

        fk_value = conn.execute("PRAGMA foreign_keys;").fetchone()[0]
        assert fk_value == 1

        watchlist_repo = SqliteWatchlistRepository(conn)
        watchlist_repo.upsert_manual_threshold("2330", fair=1500, cheap=1000, enabled=1)
        watchlist_repo.upsert_manual_threshold("2317", fair=145, cheap=130, enabled=0)
        enabled = watchlist_repo.list_enabled()
        assert len(enabled) == 1
        assert enabled[0]["stock_no"] == "2330"

        msg_repo = SqliteMessageRepository(conn)
        msg_repo.save_batch(
            [
                {
                    "stock_no": "2330",
                    "message": "first",
                    "stock_status": 1,
                    "methods_hit": ["manual_rule"],
                    "minute_bucket": "2026-04-10 10:21",
                    "update_time": 1712710000,
                }
            ]
        )
        msg_repo.save_batch(
            [
                {
                    "stock_no": "2330",
                    "message": "second",
                    "stock_status": 2,
                    "methods_hit": "manual_rule,pe_band_v1",
                    "minute_bucket": "2026-04-10 10:21",
                    "update_time": 1712710060,
                }
            ]
        )
        msg_repo.save_batch(
            [
                {
                    "stock_no": "2330",
                    "message": "third-should-not-downgrade",
                    "stock_status": 1,
                    "methods_hit": ["manual_rule"],
                    "minute_bucket": "2026-04-10 10:21",
                    "update_time": 1712710100,
                }
            ]
        )
        rows = msg_repo.list_rows()
        assert len(rows) == 1
        assert rows[0]["stock_status"] == 2
        assert rows[0]["message"] == "second"
        assert msg_repo.get_last_sent_at("2330", 2) == 1712710060
        assert msg_repo.get_last_sent_at("2330", 1) is None

        pending_repo = SqlitePendingRepository(conn)
        pending_repo.enqueue(
            {
                "minute_bucket": "2026-04-10 10:21",
                "payload": "payload text",
                "rows": [{"stock_no": "2330"}],
                "error": "db failed",
            }
        )
        pendings = pending_repo.list_pending()
        assert len(pendings) == 1
        assert pendings[0]["payload"] == "payload text"
        assert pending_repo.get_last_pending_sent_at("2330", 2) is None
        pending_repo.mark_reconciled(pendings[0]["pending_id"])
        assert pending_repo.list_pending() == []

        pending_repo.enqueue(
            {
                "minute_bucket": "2026-04-10 10:22",
                "payload": "payload text",
                "rows": [{"stock_no": "2330", "stock_status": 1, "update_time": 1712710200}],
                "error": "db failed",
            }
        )
        assert pending_repo.get_last_pending_sent_at("2330", 1) == 1712710200
        assert pending_repo.get_last_pending_sent_at("9999", 1) is None

        valuation_repo = SqliteValuationSnapshotRepository(conn)
        valuation_repo.save_snapshots([])
        valuation_repo.save_snapshots(
            [
                {
                    "stock_no": "2330",
                    "trade_date": "2026-04-10",
                    "method_name": "manual_rule",
                    "method_version": "v1",
                    "fair_price": 1500,
                    "cheap_price": 1000,
                }
            ]
        )
        snapshot_count = conn.execute("SELECT COUNT(*) FROM valuation_snapshots").fetchone()[0]
        assert snapshot_count == 1
        try:
            valuation_repo.save_snapshots([{"bad": "payload"}])
            assert False, "Expected valuation snapshot save to fail on invalid payload."
        except Exception:
            pass

        logger = SqliteLogger(conn)
        logger.log("warn", "MARKET_TIMEOUT: source timeout")
        logger.log("NOPE", "custom message")
        events = logger.list_events()
        assert events[0]["level"] == "WARN"
        assert events[0]["event"] == "MARKET_TIMEOUT"
        assert events[1]["level"] == "INFO"
    finally:
        conn.close()


def test_message_repo_rollback_on_batch_failure(tmp_path: Path):
    conn = connect_sqlite(str(tmp_path / "rollback.db"))
    try:
        apply_schema(conn)
        watchlist_repo = SqliteWatchlistRepository(conn)
        watchlist_repo.upsert_manual_threshold("2330", fair=1500, cheap=1000, enabled=1)
        msg_repo = SqliteMessageRepository(conn)

        try:
            msg_repo.save_batch(
                [
                    {
                        "stock_no": "2330",
                        "message": "ok",
                        "stock_status": 1,
                        "methods_hit": [],
                        "minute_bucket": "BAD-MINUTE",
                        "update_time": 1712710000,
                    }
                ]
            )
            assert False, "Expected CHECK constraint failure on invalid minute_bucket."
        except Exception:
            pass

        count = conn.execute("SELECT COUNT(*) FROM message").fetchone()[0]
        assert count == 0
    finally:
        conn.close()


def test_jsonl_pending_fallback_appends_records(tmp_path: Path):
    path = tmp_path / "logs" / "pending_delivery.jsonl"
    fallback = JsonlPendingFallback(path)
    fallback.append({"minute_bucket": "2026-04-10 10:21", "status": "PENDING"})
    fallback.append({"minute_bucket": "2026-04-10 10:22", "status": "PENDING"})

    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["minute_bucket"] == "2026-04-10 10:21"


def test_jsonl_pending_fallback_last_pending_sent_at(tmp_path: Path):
    path = tmp_path / "logs" / "pending_delivery.jsonl"
    fallback = JsonlPendingFallback(path)
    fallback.append(
        {
            "minute_bucket": "2026-04-10 10:21",
            "rows": [{"stock_no": "2330", "stock_status": 1, "update_time": 1712710000}],
        }
    )
    fallback.append({"minute_bucket": "2026-04-10 10:22", "rows": [{"stock_no": "2330", "stock_status": 1, "update_time": 1712710060}]})
    fallback.append({"minute_bucket": "2026-04-10 10:23", "rows": [{"stock_no": "9999", "stock_status": 1, "update_time": 1712710080}]})
    assert fallback.get_last_pending_sent_at("2330", 1) == 1712710060


def test_jsonl_pending_fallback_last_pending_sent_at_handles_missing_and_bad_lines(tmp_path: Path):
    path = tmp_path / "logs" / "missing.jsonl"
    fallback = JsonlPendingFallback(path)
    assert fallback.get_last_pending_sent_at("2330", 1) is None

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n{bad-json}\n{\"rows\":[{\"stock_no\":\"2330\",\"stock_status\":\"x\",\"update_time\":\"bad\"}]}\n", encoding="utf-8")
    assert fallback.get_last_pending_sent_at("2330", 1) is None


def test_get_last_sent_at_handles_none_row():
    class _Cursor:
        def fetchone(self):
            return None

    class _Conn:
        def execute(self, sql, params):
            return _Cursor()

    repo = SqliteMessageRepository(conn=_Conn())  # type: ignore[arg-type]
    assert repo.get_last_sent_at("2330", 1) is None
