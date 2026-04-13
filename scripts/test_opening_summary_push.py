"""Standalone opening-summary LINE push test.

Usage:
  python scripts/test_opening_summary_push.py --dry-run
  python scripts/test_opening_summary_push.py --send
  python scripts/test_opening_summary_push.py --send --db-path data/stock_monitor.db --trade-date 2026-04-13
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from stock_monitor.adapters.line_messaging import LinePushClient
from stock_monitor.adapters.market_data_twse import TwseRealtimeMarketDataProvider
from stock_monitor.adapters.sqlite_repo import SqliteValuationSnapshotRepository, SqliteWatchlistRepository, connect_sqlite
from stock_monitor.application.runtime_service import _build_opening_method_pairs, _build_opening_summary_message
from stock_monitor.bootstrap.runtime import validate_line_runtime_config


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build and optionally push opening summary message to LINE")
    parser.add_argument("--db-path", default="data/stock_monitor.db", help="sqlite db path")
    parser.add_argument(
        "--trade-date",
        default=datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d"),
        help="trade date for snapshot lookup (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--timezone",
        default="Asia/Taipei",
        help="timezone for timestamp banner (for display only)",
    )
    parser.add_argument(
        "--send",
        action="store_true",
        help="actually send to LINE (without this flag, script runs dry-run only)",
    )
    parser.add_argument(
        "--print-message-only",
        action="store_true",
        help="print message and exit (skip LINE env validation)",
    )
    return parser


def _mask_token(token: str) -> str:
    text = str(token)
    if len(text) <= 8:
        return "***"
    return f"{text[:4]}...{text[-4:]}"


def _load_stock_name_map(stock_nos: list[str]) -> dict[str, str]:
    try:
        quotes = TwseRealtimeMarketDataProvider().get_realtime_quotes(stock_nos)
    except Exception:
        return {}
    return {str(stock_no): str((quote or {}).get("name") or "").strip() for stock_no, quote in quotes.items()}


def _build_summary_message(db_path: str, trade_date: str) -> str:
    conn = connect_sqlite(db_path)
    try:
        watchlist_repo = SqliteWatchlistRepository(conn)
        snapshot_repo = SqliteValuationSnapshotRepository(conn)

        watchlist_rows = watchlist_repo.list_enabled()
        if not watchlist_rows:
            raise RuntimeError("watchlist is empty, cannot build opening summary")

        stock_nos = [str(item["stock_no"]) for item in watchlist_rows]
        snapshot_rows = snapshot_repo.list_latest_snapshots(stock_nos=stock_nos, as_of_date=trade_date)
        method_pairs = _build_opening_method_pairs(snapshot_rows)
        stock_name_map = _load_stock_name_map(stock_nos)
        return _build_opening_summary_message(
            trade_date=trade_date,
            watchlist_rows=watchlist_rows,
            method_pairs=method_pairs,
            snapshot_rows=snapshot_rows,
            stock_name_map=stock_name_map,
        )
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        tz = ZoneInfo(args.timezone)
    except Exception:
        tz = ZoneInfo("Asia/Taipei")
    now_text = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")

    try:
        payload = _build_summary_message(db_path=args.db_path, trade_date=args.trade_date)
    except Exception as exc:
        print(f"[ERROR] failed to build opening summary: {exc}")
        return 2

    print(f"[INFO] now={now_text}")
    print(f"[INFO] trade_date={args.trade_date}")
    print(f"[INFO] db_path={args.db_path}")
    print("[INFO] opening summary payload preview:")
    print("-" * 60)
    print(payload)
    print("-" * 60)

    if args.print_message_only:
        print("[OK] print-message-only completed (message not sent)")
        return 0

    try:
        line_cfg = validate_line_runtime_config(os.environ)
    except RuntimeError as exc:
        print(f"[ERROR] LINE runtime config invalid: {exc}")
        return 3

    print(f"[INFO] target_id={line_cfg['group_id']}")
    print(f"[INFO] token={_mask_token(line_cfg['channel_token'])}")

    if not args.send:
        print("[OK] dry-run completed (add --send to push this opening summary)")
        return 0

    client = LinePushClient(
        channel_access_token=line_cfg["channel_token"],
        to_group_id=line_cfg["group_id"],
    )
    try:
        result = client.send(payload)
    except Exception as exc:
        print(f"[ERROR] LINE push failed: {exc}")
        return 1

    print(f"[OK] LINE opening summary sent: {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
