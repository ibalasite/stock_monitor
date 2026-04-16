"""Executable runtime entrypoint for stock monitor."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime

from stock_monitor.adapters.sqlite_repo import (
    apply_schema,
    connect_sqlite,
)
from stock_monitor.application.daemon_runner import (
    _build_runtime,
    _resolve_timezone,
    _run_daemon_loop,
)
from stock_monitor.application.message_template import render_line_template_message as _render_line_template_message  # noqa: F401
from stock_monitor.application.runtime_service import run_minute_cycle, run_reconcile_cycle
from stock_monitor.application.valuation_calculator import ManualValuationCalculator
from stock_monitor.application.valuation_scheduler import run_daily_valuation_job
from stock_monitor.adapters.all_listed_stocks_twse import TwseAllListedStocksProvider
from stock_monitor.application.market_scan import run_market_scan_job
from stock_monitor.application.market_scan_methods import load_enabled_scan_methods
from stock_monitor.bootstrap.runtime import assert_sqlite_prerequisites


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Stock monitoring runtime")
    parser.add_argument("--db-path", default="data/stock_monitor.db")
    parser.add_argument("--timezone", default=os.getenv("APP_TIMEZONE", "Asia/Taipei"))

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init-db")
    subparsers.add_parser("run-once")
    subparsers.add_parser("reconcile-once")
    subparsers.add_parser("valuation-once")
    scan = subparsers.add_parser("scan-market")
    scan.add_argument("--output-dir", default="./output")
    daemon = subparsers.add_parser("run-daemon")
    daemon.add_argument("--poll-interval-sec", type=int, default=int(os.getenv("POLL_INTERVAL_SEC", "60")))
    daemon.add_argument("--valuation-time", default=os.getenv("VALUATION_TIME", "14:00"))
    daemon.add_argument("--max-loops", type=int, default=None)
    return parser


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

    if args.command == "scan-market":
        conn = connect_sqlite(args.db_path)
        try:
            apply_schema(conn)
            scan_date_local = datetime.now(_resolve_timezone(args.timezone)).strftime("%Y-%m-%d")
            try:
                valuation_methods = load_enabled_scan_methods(conn, as_of_date=scan_date_local)
            except RuntimeError as exc:
                print(json.dumps({
                    "status": "error",
                    "error_code": str(exc),
                }, ensure_ascii=False))
                return 1

            # Methods hold a ref to conn — keep conn open through the scan job
            provider = TwseAllListedStocksProvider()
            result = run_market_scan_job(
                db_path=args.db_path,
                output_dir=args.output_dir,
                stocks_provider=provider,
                valuation_methods=valuation_methods,
            )
        finally:
            conn.close()

        print(json.dumps({
            "status": "ok",
            "scan_date": result.scan_date,
            "total_stocks": result.total_stocks,
            "watchlist_upserted": result.watchlist_upserted,
            "watchlist_new": result.watchlist_new,
            "watchlist_updated": result.watchlist_updated,
            "near_fair_count": result.near_fair_count,
            "uncalculable_count": result.uncalculable_count,
            "above_fair_count": result.above_fair_count,
            "output_dir": result.output_dir,
        }, ensure_ascii=False))
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
                valuation_snapshot_repo=runtime["valuation_snapshot_repo"],
                pending_fallback=runtime["pending_fallback"],
                logger=runtime["logger"],
                cooldown_seconds=int(os.getenv("COOLDOWN_SEC", "300")),
                retry_count=int(os.getenv("MAX_RETRY_COUNT", "3")),
                stale_threshold_sec=int(os.getenv("STALE_THRESHOLD_SEC", "90")),
                timezone_name=args.timezone,
            )
        elif args.command == "reconcile-once":
            result = run_reconcile_cycle(
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
            calculator = ManualValuationCalculator(
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

