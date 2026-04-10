"""SQLite repositories and helpers for production runtime."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from stock_monitor.db.schema import SCHEMA_SQL


def _now_epoch() -> int:
    return int(time.time())


def connect_sqlite(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def apply_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.commit()


@dataclass
class SqliteWatchlistRepository:
    conn: sqlite3.Connection

    def list_enabled(self) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT stock_no, manual_fair_price, manual_cheap_price
            FROM watchlist
            WHERE enabled = 1
            ORDER BY stock_no
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def upsert_manual_threshold(self, stock_no: str, fair: float, cheap: float, enabled: int = 1) -> None:
        now_epoch = _now_epoch()
        self.conn.execute(
            """
            INSERT INTO watchlist(stock_no, manual_fair_price, manual_cheap_price, enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(stock_no) DO UPDATE SET
              manual_fair_price = excluded.manual_fair_price,
              manual_cheap_price = excluded.manual_cheap_price,
              enabled = excluded.enabled,
              updated_at = excluded.updated_at
            """,
            (stock_no, fair, cheap, enabled, now_epoch, now_epoch),
        )
        self.conn.commit()


@dataclass
class SqliteMessageRepository:
    conn: sqlite3.Connection

    def begin(self) -> None:
        self.conn.execute("BEGIN")

    def insert_row(self, row: dict) -> None:
        methods_hit = row.get("methods_hit", [])
        if isinstance(methods_hit, str):
            methods_json = json.dumps([value.strip() for value in methods_hit.split(",") if value.strip()])
        else:
            methods_json = json.dumps([str(value).strip() for value in methods_hit if str(value).strip()])

        self.conn.execute(
            """
            INSERT INTO message(stock_no, message, stock_status, methods_hit, minute_bucket, update_time)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(stock_no, minute_bucket) DO UPDATE SET
              stock_status = excluded.stock_status,
              methods_hit = excluded.methods_hit,
              message = excluded.message,
              update_time = excluded.update_time
            WHERE excluded.stock_status > message.stock_status
               OR (
                    excluded.stock_status = message.stock_status
                AND (
                     excluded.methods_hit <> message.methods_hit
                  OR excluded.message <> message.message
                )
               )
            """,
            (
                row["stock_no"],
                row["message"],
                int(row["stock_status"]),
                methods_json,
                row["minute_bucket"],
                int(row["update_time"]),
            ),
        )

    def commit(self) -> None:
        self.conn.commit()

    def rollback(self) -> None:
        self.conn.rollback()

    def save_batch(self, rows: list[dict]) -> None:
        self.begin()
        try:
            for row in rows:
                self.insert_row(row)
            self.commit()
        except Exception:
            self.rollback()
            raise

    def get_last_sent_at(self, stock_no: str, stock_status: int) -> int | None:
        row = self.conn.execute(
            """
            SELECT MAX(update_time) AS ts
            FROM message
            WHERE stock_no = ? AND stock_status = ?
            """,
            (stock_no, int(stock_status)),
        ).fetchone()
        if row is None:
            return None
        return row["ts"]

    def list_rows(self) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT stock_no, message, stock_status, methods_hit, minute_bucket, update_time
            FROM message
            ORDER BY id
            """
        ).fetchall()
        return [dict(row) for row in rows]


@dataclass
class SqlitePendingRepository:
    conn: sqlite3.Connection

    def enqueue(self, item: dict) -> None:
        now_epoch = _now_epoch()
        payload_json = json.dumps(
            {
                "payload": item.get("payload", ""),
                "rows": item.get("rows", []),
                "error": item.get("error"),
            }
        )
        self.conn.execute(
            """
            INSERT INTO pending_delivery_ledger(minute_bucket, payload_json, status, retry_count, last_error, created_at, updated_at)
            VALUES (?, ?, 'PENDING', 0, ?, ?, ?)
            """,
            (item.get("minute_bucket", ""), payload_json, item.get("error"), now_epoch, now_epoch),
        )
        self.conn.commit()

    def list_pending(self, limit: int = 100) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT id, minute_bucket, payload_json, status, retry_count
            FROM pending_delivery_ledger
            WHERE status = 'PENDING'
            ORDER BY id ASC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        result: list[dict] = []
        for row in rows:
            payload_data = json.loads(row["payload_json"])
            result.append(
                {
                    "pending_id": str(row["id"]),
                    "minute_bucket": row["minute_bucket"],
                    "payload": payload_data.get("payload", ""),
                    "rows": payload_data.get("rows", []),
                    "status": row["status"],
                    "retry_count": row["retry_count"],
                }
            )
        return result

    def mark_reconciled(self, pending_id: str) -> None:
        self.conn.execute(
            """
            UPDATE pending_delivery_ledger
            SET status = 'RECONCILED', updated_at = ?
            WHERE id = ?
            """,
            (_now_epoch(), int(pending_id)),
        )
        self.conn.commit()

    def get_last_pending_sent_at(self, stock_no: str, stock_status: int) -> int | None:
        latest: int | None = None
        for item in self.list_pending(limit=500):
            for row in item.get("rows", []):
                try:
                    row_stock_no = str(row.get("stock_no"))
                    row_status = int(row.get("stock_status"))
                    row_update_time = int(row.get("update_time"))
                except (TypeError, ValueError):
                    continue
                if row_stock_no == str(stock_no) and row_status == int(stock_status):
                    latest = row_update_time if latest is None else max(latest, row_update_time)
        return latest


@dataclass
class JsonlPendingFallback:
    path: Path

    def append(self, item: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")

    def get_last_pending_sent_at(self, stock_no: str, stock_status: int) -> int | None:
        if not self.path.exists():
            return None

        latest: int | None = None
        for raw_line in self.path.read_text(encoding="utf-8").splitlines():
            if not raw_line.strip():
                continue
            try:
                item = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            for row in item.get("rows", []):
                try:
                    row_stock_no = str(row.get("stock_no"))
                    row_status = int(row.get("stock_status"))
                    row_update_time = int(row.get("update_time"))
                except (TypeError, ValueError):
                    continue
                if row_stock_no == str(stock_no) and row_status == int(stock_status):
                    latest = row_update_time if latest is None else max(latest, row_update_time)
        return latest


@dataclass
class SqliteValuationSnapshotRepository:
    conn: sqlite3.Connection

    def save_snapshots(self, snapshots: list[dict]) -> None:
        if not snapshots:
            return
        now_epoch = _now_epoch()
        self.conn.execute("BEGIN")
        try:
            for snapshot in snapshots:
                method_name = str(snapshot["method_name"])
                method_version = str(snapshot["method_version"])
                self.conn.execute(
                    """
                    INSERT INTO valuation_methods(method_name, method_version, enabled, created_at, updated_at)
                    VALUES (?, ?, 1, ?, ?)
                    ON CONFLICT(method_name, method_version) DO UPDATE SET
                      updated_at = excluded.updated_at
                    """,
                    (method_name, method_version, now_epoch, now_epoch),
                )
                self.conn.execute(
                    """
                    INSERT INTO valuation_snapshots(
                      stock_no, trade_date, method_name, method_version, fair_price, cheap_price, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(stock_no, trade_date, method_name, method_version) DO UPDATE SET
                      fair_price = excluded.fair_price,
                      cheap_price = excluded.cheap_price,
                      created_at = excluded.created_at
                    """,
                    (
                        str(snapshot["stock_no"]),
                        str(snapshot["trade_date"]),
                        method_name,
                        method_version,
                        float(snapshot["fair_price"]),
                        float(snapshot["cheap_price"]),
                        now_epoch,
                    ),
                )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise


@dataclass
class SqliteLogger:
    conn: sqlite3.Connection

    def log(self, level: str, message: str) -> None:
        normalized_level = str(level).upper()
        if normalized_level not in {"INFO", "WARN", "ERROR"}:
            normalized_level = "INFO"
        text = str(message)
        event = text.split(":", 1)[0][:120]
        self.conn.execute(
            """
            INSERT INTO system_logs(level, event, detail, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (normalized_level, event, text, _now_epoch()),
        )
        self.conn.commit()

    def list_events(self) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT level, event, detail
            FROM system_logs
            ORDER BY id ASC
            """
        ).fetchall()
        return [dict(row) for row in rows]
