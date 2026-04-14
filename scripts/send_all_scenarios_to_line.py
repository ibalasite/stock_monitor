"""Send one sample LINE message for every outbound message scenario.

Scenarios:
  1. 測試推播          (TEST_PUSH_TEMPLATE_KEY)
  2. 開盤監控設定摘要   (opening_summary.row.compact.v1) - from DB or demo data
  3. 分鐘彙總通知 status=1 - 低於合理價 trigger row
  4. 分鐘彙總通知 status=2 - 低於便宜價 trigger row

Usage:
  python scripts/send_all_scenarios_to_line.py --dry-run
  python scripts/send_all_scenarios_to_line.py --send
  python scripts/send_all_scenarios_to_line.py --send --db-path data/stock_monitor.db
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from stock_monitor.adapters.line_messaging import LinePushClient
from stock_monitor.adapters.market_data_twse import TwseRealtimeMarketDataProvider
from stock_monitor.application.message_template import render_line_template_message
from stock_monitor.application.monitoring_workflow import (
    MINUTE_DIGEST_TEMPLATE_KEY,
    aggregate_minute_notifications,
)
from stock_monitor.application.runtime_service import (
    TEST_PUSH_TEMPLATE_KEY,
    TRIGGER_ROW_TEMPLATE_KEY,
    _OPENING_SUMMARY_ROW_TEMPLATE,
)
from stock_monitor.bootstrap.runtime import validate_line_runtime_config

TZ = ZoneInfo("Asia/Taipei")


def _mask_token(token: str) -> str:
    text = str(token)
    if len(text) <= 8:
        return "***"
    return f"{text[:4]}...{text[-4:]}"


def _fetch_stock_name_map(stock_nos: list[str]) -> dict[str, str]:
    """Fetch stock names from TWSE, tolerating no-price rows (works outside trading hours)."""
    provider = TwseRealtimeMarketDataProvider()
    _, channels = provider._build_stock_channels(stock_nos)
    try:
        rows = provider._fetch_channels(channels)
    except Exception as exc:
        print(f"[WARN] stock name fetch failed ({exc}), using stock_no only")
        return {}
    name_map: dict[str, str] = {}
    for row in rows:
        code = str(row.get("c") or "").strip()
        name = str(row.get("n") or "").strip()
        if code and name and code in stock_nos:
            name_map[code] = name
    return name_map


# ── scenario builders ──────────────────────────────────────────────────────────

def build_test_push() -> str:
    now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
    return render_line_template_message(
        TEST_PUSH_TEMPLATE_KEY,
        {"message": f"手動驗證推播 @ {now}"},
    )


def build_opening_summary(db_path: str, trade_date: str) -> str:
    """Load watchlist + snapshots from DB and render opening summary.
    Falls back to demo data when DB is empty or not found."""
    try:
        from stock_monitor.adapters.sqlite_repo import (
            SqliteValuationSnapshotRepository,
            SqliteWatchlistRepository,
            connect_sqlite,
        )
        from stock_monitor.application.runtime_service import (
            _build_opening_method_pairs,
            _build_opening_summary_message,
        )

        conn = connect_sqlite(db_path)
        watchlist_rows = SqliteWatchlistRepository(conn).list_enabled()
        if not watchlist_rows:
            raise RuntimeError("watchlist empty")

        stock_nos = [str(r["stock_no"]) for r in watchlist_rows]
        snapshot_rows = SqliteValuationSnapshotRepository(conn).list_latest_snapshots(
            stock_nos=stock_nos, as_of_date=trade_date
        )
        method_pairs = _build_opening_method_pairs(snapshot_rows)
        stock_name_map = _fetch_stock_name_map(stock_nos)
        payload = _build_opening_summary_message(
            trade_date=trade_date,
            watchlist_rows=watchlist_rows,
            method_pairs=method_pairs,
            snapshot_rows=snapshot_rows,
            stock_name_map=stock_name_map,
        )
        conn.close()
        return payload
    except Exception as exc:
        print(f"[WARN] DB load failed ({exc}), using demo opening summary")

    # ── demo fallback ──
    rows = [
        render_line_template_message(
            _OPENING_SUMMARY_ROW_TEMPLATE,
            {"stock_display": "台積電(2330)", "method_label": "手動", "fair_price": "2000", "cheap_price": "1500"},
        ),
        render_line_template_message(
            _OPENING_SUMMARY_ROW_TEMPLATE,
            {"stock_display": "台積電(2330)", "method_label": "艾蜜", "fair_price": "1800", "cheap_price": "1400"},
        ),
        render_line_template_message(
            _OPENING_SUMMARY_ROW_TEMPLATE,
            {"stock_display": "台積電(2330)", "method_label": "老牛", "fair_price": "1750", "cheap_price": "1400"},
        ),
        render_line_template_message(
            _OPENING_SUMMARY_ROW_TEMPLATE,
            {"stock_display": "鴻海(2317)", "method_label": "手動", "fair_price": "145", "cheap_price": "130"},
        ),
        render_line_template_message(
            _OPENING_SUMMARY_ROW_TEMPLATE,
            {"stock_display": "鴻海(2317)", "method_label": "艾蜜", "fair_price": "N/A", "cheap_price": "N/A"},
        ),
    ]
    return "\n".join(rows)


def build_minute_digest_status1() -> str:
    """分鐘彙總 – 台積電 低於合理價 (status=1)."""
    minute_bucket = datetime.now(TZ).strftime("%Y-%m-%d %H:%M")
    trigger_msg = render_line_template_message(
        TRIGGER_ROW_TEMPLATE_KEY,
        {
            "display_label": "台積電(2330)",
            "current_price": "1950",
            "stock_status": 1,
            "fair_price": "2000",
            "cheap_price": "1500",
        },
    )
    rows = [
        {
            "stock_no": "2330",
            "stock_status": 1,
            "methods_hit": ["manual_rule", "emily_composite_v1"],
            "minute_bucket": minute_bucket,
            "message": trigger_msg,
        }
    ]
    return aggregate_minute_notifications(minute_bucket, rows)


def build_minute_digest_status2() -> str:
    """分鐘彙總 – 台積電 低於便宜價  (status=2, 含合理價備註)."""
    minute_bucket = datetime.now(TZ).strftime("%Y-%m-%d %H:%M")
    trigger_msg = render_line_template_message(
        TRIGGER_ROW_TEMPLATE_KEY,
        {
            "display_label": "台積電(2330)",
            "current_price": "1480",
            "stock_status": 2,
            "fair_price": "2000",
            "cheap_price": "1500",
        },
    )
    rows = [
        {
            "stock_no": "2330",
            "stock_status": 2,
            "methods_hit": ["manual_rule", "emily_composite_v1", "raysky_blended_margin_v1"],
            "minute_bucket": minute_bucket,
            "message": trigger_msg,
        }
    ]
    return aggregate_minute_notifications(minute_bucket, rows)


# ── main ───────────────────────────────────────────────────────────────────────

SCENARIOS: list[tuple[str, callable]] = []  # filled after arg parse


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Send one LINE message per scenario for manual UAT")
    p.add_argument("--db-path", default="data/stock_monitor.db")
    p.add_argument(
        "--trade-date",
        default=datetime.now(TZ).strftime("%Y-%m-%d"),
        help="YYYY-MM-DD for opening summary snapshot lookup",
    )
    p.add_argument(
        "--send",
        action="store_true",
        help="actually push to LINE (without this flag runs dry-run only)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="alias for no --send (print messages and exit)",
    )
    p.add_argument(
        "--delay",
        type=float,
        default=1.5,
        help="seconds between sends (default 1.5)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    actually_send = args.send and not args.dry_run

    now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[INFO] send_all_scenarios_to_line  date={args.trade_date}  send={actually_send}  @ {now}")

    client = None
    if actually_send:
        try:
            cfg = validate_line_runtime_config(os.environ)
        except RuntimeError as exc:
            print(f"[ERROR] LINE config invalid: {exc}")
            return 2
        print(f"[INFO] group_id={cfg['group_id']}  token={_mask_token(cfg['channel_token'])}")
        client = LinePushClient(
            channel_access_token=cfg["channel_token"],
            to_group_id=cfg["group_id"],
        )

    scenarios = [
        ("1. 測試推播 (TEST_PUSH_TEMPLATE_KEY)", lambda: build_test_push()),
        ("2. 開盤監控設定摘要 (opening_summary.row.compact.v1)", lambda: build_opening_summary(args.db_path, args.trade_date)),
        ("3. 分鐘彙總通知 status=1 低於合理價", lambda: build_minute_digest_status1()),
        ("4. 分鐘彙總通知 status=2 低於便宜價", lambda: build_minute_digest_status2()),
    ]

    separator = "─" * 50

    for label, builder in scenarios:
        print(f"\n{separator}")
        print(f"[SCENARIO] {label}")
        try:
            payload = builder()
        except Exception as exc:
            print(f"[ERROR] build failed: {exc}")
            continue

        print("[MESSAGE]")
        for line in payload.splitlines():
            print(f"  {line}")

        if actually_send:
            try:
                result = client.send(payload)
                print(f"[OK] sent → {result}")
            except Exception as exc:
                print(f"[ERROR] LINE send failed: {exc}")
            time.sleep(args.delay)
        else:
            print("[DRY-RUN] (not sent)")

    print(f"\n{separator}")
    if actually_send:
        print("[DONE] All scenarios pushed to LINE. Please verify in the group.")
    else:
        print("[DONE] dry-run complete. Re-run with --send to push to LINE.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
