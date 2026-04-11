"""Executable runtime entrypoint for stock monitor."""

from __future__ import annotations

import argparse
import json
import os
import time
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
    SqliteValuationSnapshotRepository,
    SqliteWatchlistRepository,
    apply_schema,
    connect_sqlite,
)
from stock_monitor.application.runtime_service import run_minute_cycle, run_reconcile_cycle
from stock_monitor.application.trading_session import is_in_trading_session
from stock_monitor.application.valuation_scheduler import run_daily_valuation_job
from stock_monitor.bootstrap.runtime import assert_sqlite_prerequisites, validate_line_runtime_config
from stock_monitor.domain.time_bucket import TimeBucketService


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Stock monitoring runtime")
    parser.add_argument("--db-path", default="data/stock_monitor.db")
    parser.add_argument("--timezone", default=os.getenv("APP_TIMEZONE", "Asia/Taipei"))

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init-db")
    subparsers.add_parser("run-once")
    subparsers.add_parser("reconcile-once")
    subparsers.add_parser("valuation-once")
    daemon = subparsers.add_parser("run-daemon")
    daemon.add_argument("--poll-interval-sec", type=int, default=int(os.getenv("POLL_INTERVAL_SEC", "60")))
    daemon.add_argument("--valuation-time", default=os.getenv("VALUATION_TIME", "14:00"))
    daemon.add_argument("--max-loops", type=int, default=None)
    return parser


class _ManualValuationCalculator:
    """Phase-1 valuation source based on manual watchlist thresholds."""

    def __init__(self, watchlist_repo, trade_date: str):
        self.watchlist_repo = watchlist_repo
        self.trade_date = trade_date

    def calculate(self) -> list[dict]:
        rows = self.watchlist_repo.list_enabled()
        snapshots: list[dict] = []
        for row in rows:
            snapshots.append(
                {
                    "stock_no": str(row["stock_no"]),
                    "trade_date": self.trade_date,
                    "method_name": "manual_rule",
                    "method_version": "v1",
                    "fair_price": float(row["manual_fair_price"]),
                    "cheap_price": float(row["manual_cheap_price"]),
                }
            )
        return snapshots


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
        "valuation_snapshot_repo": SqliteValuationSnapshotRepository(conn),
        "logger": SqliteLogger(conn),
        "pending_fallback": JsonlPendingFallback(Path("logs/pending_delivery.jsonl")),
    }
    return runtime


def _resolve_timezone(timezone_name: str):
    try:
        return ZoneInfo(timezone_name)
    except Exception:
        return timezone.utc


def _run_daemon_loop(
    *,
    runtime: dict,
    timezone_name: str,
    poll_interval_sec: int,
    valuation_time: str,
    cooldown_seconds: int,
    retry_count: int,
    stale_threshold_sec: int,
    max_loops: int | None = None,
    now_provider=None,
    sleep_fn=None,
) -> dict:
    tz = _resolve_timezone(timezone_name)
    now_provider = now_provider or (lambda: datetime.now(tz))
    sleep_fn = sleep_fn or time.sleep
    poll_interval_sec = max(1, int(poll_interval_sec))

    bucket_service = TimeBucketService(timezone_name)
    last_polled_bucket: str | None = None
    last_valuation_date: str | None = None

    loops = 0
    minute_cycles = 0
    valuation_runs = 0
    reconcile_runs = 0

    try:
        while True:
            if max_loops is not None and loops >= max_loops:
                break

            loops += 1
            now_dt = now_provider()
            current_bucket = bucket_service.to_minute_bucket(now_dt)

            if is_in_trading_session(now_dt) and current_bucket != last_polled_bucket:
                run_minute_cycle(
                    now_dt=now_dt,
                    market_data_provider=runtime["market_provider"],
                    line_client=runtime["line_client"],
                    watchlist_repo=runtime["watchlist_repo"],
                    message_repo=runtime["message_repo"],
                    pending_repo=runtime["pending_repo"],
                    pending_fallback=runtime["pending_fallback"],
                    logger=runtime["logger"],
                    cooldown_seconds=cooldown_seconds,
                    retry_count=retry_count,
                    stale_threshold_sec=stale_threshold_sec,
                    timezone_name=timezone_name,
                )
                minute_cycles += 1
                last_polled_bucket = current_bucket

            now_hhmm = now_dt.strftime("%H:%M")
            today = now_dt.strftime("%Y-%m-%d")
            if now_hhmm == valuation_time and now_dt.weekday() < 5 and last_valuation_date != today:
                calculator = _ManualValuationCalculator(
                    watchlist_repo=runtime["watchlist_repo"],
                    trade_date=today,
                )
                run_daily_valuation_job(
                    now_dt=now_dt,
                    is_trading_day=True,
                    calculator=calculator,
                    snapshot_repo=runtime["valuation_snapshot_repo"],
                    logger=runtime["logger"],
                )
                valuation_runs += 1
                last_valuation_date = today

            run_reconcile_cycle(
                line_client=runtime["line_client"],
                message_repo=runtime["message_repo"],
                pending_repo=runtime["pending_repo"],
                logger=runtime["logger"],
            )
            reconcile_runs += 1

            sleep_fn(poll_interval_sec)
    except KeyboardInterrupt:
        return {
            "status": "interrupted",
            "loops": loops,
            "minute_cycles": minute_cycles,
            "valuation_runs": valuation_runs,
            "reconcile_runs": reconcile_runs,
        }

    return {
        "status": "stopped",
        "loops": loops,
        "minute_cycles": minute_cycles,
        "valuation_runs": valuation_runs,
        "reconcile_runs": reconcile_runs,
    }


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
                stale_threshold_sec=int(os.getenv("STALE_THRESHOLD_SEC", "90")),
                timezone_name=args.timezone,
            )
        elif args.command == "reconcile-once":
            result = run_reconcile_cycle(
                line_client=runtime["line_client"],
                message_repo=runtime["message_repo"],
                pending_repo=runtime["pending_repo"],
                logger=runtime["logger"],
            )
        elif args.command == "run-daemon":
            result = _run_daemon_loop(
                runtime=runtime,
                timezone_name=args.timezone,
                poll_interval_sec=args.poll_interval_sec,
                valuation_time=args.valuation_time,
                cooldown_seconds=int(os.getenv("COOLDOWN_SEC", "300")),
                retry_count=int(os.getenv("MAX_RETRY_COUNT", "3")),
                stale_threshold_sec=int(os.getenv("STALE_THRESHOLD_SEC", "90")),
                max_loops=args.max_loops,
            )
        else:
            calculator = _ManualValuationCalculator(
                watchlist_repo=runtime["watchlist_repo"],
                trade_date=now_dt.strftime("%Y-%m-%d"),
            )
            result = run_daily_valuation_job(
                now_dt=now_dt,
                is_trading_day=now_dt.weekday() < 5,
                calculator=calculator,
                snapshot_repo=runtime["valuation_snapshot_repo"],
                logger=runtime["logger"],
            )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
