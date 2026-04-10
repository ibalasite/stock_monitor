"""Executable runtime entrypoint for stock monitor."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from stock_monitor.adapters.line_messaging import LinePushClient
from stock_monitor.adapters.market_data_twse import TwseRealtimeMarketDataProvider
from stock_monitor.adapters.sqlite_repo import (
    JsonlPendingFallback,
    SqliteLogger,
    SqliteMessageRepository,
    SqlitePendingRepository,
    SqliteWatchlistRepository,
    apply_schema,
    connect_sqlite,
)
from stock_monitor.application.runtime_service import run_minute_cycle, run_reconcile_cycle
from stock_monitor.bootstrap.runtime import assert_sqlite_prerequisites, validate_line_runtime_config


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Stock monitoring runtime")
    parser.add_argument("--db-path", default="data/stock_monitor.db")
    parser.add_argument("--timezone", default=os.getenv("APP_TIMEZONE", "Asia/Taipei"))

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init-db")
    subparsers.add_parser("run-once")
    subparsers.add_parser("reconcile-once")
    return parser


def _build_runtime(args) -> dict:
    db_path = Path(args.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect_sqlite(str(db_path))
    apply_schema(conn)
    assert_sqlite_prerequisites(conn)

    line_cfg = validate_line_runtime_config(os.environ)
    runtime = {
        "conn": conn,
        "line_client": LinePushClient(
            channel_access_token=line_cfg["channel_token"],
            to_group_id=line_cfg["group_id"],
        ),
        "market_provider": TwseRealtimeMarketDataProvider(),
        "watchlist_repo": SqliteWatchlistRepository(conn),
        "message_repo": SqliteMessageRepository(conn),
        "pending_repo": SqlitePendingRepository(conn),
        "logger": SqliteLogger(conn),
        "pending_fallback": JsonlPendingFallback(Path("logs/pending_delivery.jsonl")),
    }
    return runtime


def _resolve_timezone(timezone_name: str):
    try:
        return ZoneInfo(timezone_name)
    except Exception:
        return timezone.utc


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "init-db":
        conn = connect_sqlite(args.db_path)
        try:
            apply_schema(conn)
            assert_sqlite_prerequisites(conn)
        finally:
            conn.close()
        print(json.dumps({"status": "ok", "command": "init-db"}))
        return 0

    runtime = _build_runtime(args)
    conn = runtime["conn"]
    try:
        tz = _resolve_timezone(args.timezone)
        now_dt = datetime.now(tz)
        if args.command == "run-once":
            result = run_minute_cycle(
                now_dt=now_dt,
                market_data_provider=runtime["market_provider"],
                line_client=runtime["line_client"],
                watchlist_repo=runtime["watchlist_repo"],
                message_repo=runtime["message_repo"],
                pending_repo=runtime["pending_repo"],
                pending_fallback=runtime["pending_fallback"],
                logger=runtime["logger"],
                cooldown_seconds=int(os.getenv("COOLDOWN_SEC", "300")),
                retry_count=int(os.getenv("MAX_RETRY_COUNT", "3")),
                timezone_name=args.timezone,
            )
        else:
            result = run_reconcile_cycle(
                line_client=runtime["line_client"],
                message_repo=runtime["message_repo"],
                pending_repo=runtime["pending_repo"],
                logger=runtime["logger"],
            )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
