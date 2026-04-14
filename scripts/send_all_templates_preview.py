"""Send all LINE message templates using the SAME production format for verification.

Each template type is sent as a separate LINE message, identical to what
the monitoring system actually sends. No wrappers or labels added.

Usage:
    python scripts/send_all_templates_preview.py
    python scripts/send_all_templates_preview.py --dry-run

Prerequisites:
    LINE_CHANNEL_ACCESS_TOKEN (or CHANNEL_ACCESS_TOKEN)
    LINE_TO_GROUP_ID (or TARGET_GROUP_ID)
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
from stock_monitor.application.message_template import render_line_template_message
from stock_monitor.application.monitoring_workflow import (
    MINUTE_DIGEST_TEMPLATE_KEY,
    TRIGGER_ROW_DIGEST_TEMPLATE_KEY,
    aggregate_minute_notifications,
)
from stock_monitor.application.runtime_service import (
    TEST_PUSH_TEMPLATE_KEY,
    _OPENING_SUMMARY_ROW_TEMPLATE,
    _format_compact_price,
    _method_label,
)
from stock_monitor.bootstrap.runtime import validate_line_runtime_config

TZ = ZoneInfo("Asia/Taipei")
NOW = datetime.now(TZ)
MINUTE_BUCKET = NOW.strftime("%Y-%m-%d %H:%M")
TIMESTAMP = NOW.strftime("%Y-%m-%d %H:%M:%S")


def _build_messages() -> list[tuple[str, str]]:
    """Return list of (label, message_text) tuples — same text as production sends."""
    messages: list[tuple[str, str]] = []

    # ── 1. 測試推播（line_test_push_v1）────────────────────────────────────
    messages.append((
        "① test_push_v1",
        render_line_template_message(TEST_PUSH_TEMPLATE_KEY, {
            "message": f"手動驗證推播 @ {TIMESTAMP}",
        }),
    ))

    # ── 2. 開盤摘要（line_opening_summary_row_compact_v1）──────────────────
    # Matches _build_opening_summary_message() output format
    opening_rows = []
    stocks = [
        ("2330", "台積電", 2000, 1500),
        ("2348", "海悅",   72,   68),
        ("3293", "鈦象",   700,  680),
    ]
    methods = [
        ("emily_composite_v1", "emily_composite"),
        ("oldbull_dividend_yield_v1", "oldbull_dividend_yield"),
        ("raysky_blended_margin_v1", "raysky_blended_margin"),
    ]
    method_prices = {
        ("2330", "emily_composite_v1"):       (1800, 1500),
        ("2330", "oldbull_dividend_yield_v1"): (1750, 1400),
        ("2330", "raysky_blended_margin_v1"):  (1284, 1091),
        ("2348", "emily_composite_v1"):        (70,   63),
        ("2348", "oldbull_dividend_yield_v1"): (70,   56),
        ("2348", "raysky_blended_margin_v1"):  (51,   43),
        ("3293", "emily_composite_v1"):        (692,  622),
        ("3293", "oldbull_dividend_yield_v1"): (690,  552),
        ("3293", "raysky_blended_margin_v1"):  (506,  430),
    }
    for stock_no, name, fair, cheap in stocks:
        display = f"{name}({stock_no})"
        opening_rows.append(render_line_template_message(
            _OPENING_SUMMARY_ROW_TEMPLATE,
            {"stock_display": display, "method_label": _method_label("manual_rule"),
             "fair_price": _format_compact_price(fair), "cheap_price": _format_compact_price(cheap)},
        ))
        for method_key, method_name in methods:
            fp, cp = method_prices.get((stock_no, method_key), (None, None))
            opening_rows.append(render_line_template_message(
                _OPENING_SUMMARY_ROW_TEMPLATE,
                {"stock_display": display, "method_label": _method_label(method_key),
                 "fair_price": _format_compact_price(fp) if fp else "N/A",
                 "cheap_price": _format_compact_price(cp) if cp else "N/A"},
            ))
    messages.append(("② opening_summary_row_compact_v1",
                     "\n".join(opening_rows)))

    # ── 3. 監控通知 status=1（合理價觸發，多方法）──────────────────────────
    # Production flow: build_minute_rows → line_trigger_row_v1.j2 → message
    #                  aggregate_minute_notifications → line_trigger_row_digest_v1.j2 → final
    msg_status1 = render_line_template_message("line_trigger_row_v1", {
        "display_label": "台積電(2330)",
        "current_price": "1950",
        "stock_status": 1,
        "fair_price": "2000",
        "cheap_price": None,
    })
    messages.append((
        "③ monitoring status=1",
        aggregate_minute_notifications(MINUTE_BUCKET, [
            {
                "message": msg_status1,
                "methods_hit": ["emily_composite_v1", "manual_rule"],
                "stock_status": 1,
            },
        ]),
    ))

    # ── 4. 監控通知 status=2（便宜價觸發，多股票多方法）───────────────────
    msg_status2_tsmc = render_line_template_message("line_trigger_row_v1", {
        "display_label": "台積電(2330)",
        "current_price": "1480",
        "stock_status": 2,
        "fair_price": "2000",
        "cheap_price": "1500",
    })
    msg_status2_haiyu = render_line_template_message("line_trigger_row_v1", {
        "display_label": "海悅(2348)",
        "current_price": "65",
        "stock_status": 2,
        "fair_price": "72",
        "cheap_price": "68",
    })
    messages.append((
        "④ monitoring status=2, multi-stock",
        aggregate_minute_notifications(MINUTE_BUCKET, [
            {
                "message": msg_status2_tsmc,
                "methods_hit": ["emily_composite_v1", "manual_rule", "raysky_blended_margin_v1"],
                "stock_status": 2,
            },
            {
                "message": msg_status2_haiyu,
                "methods_hit": ["emily_composite_v1"],
                "stock_status": 2,
            },
        ]),
    ))

    return messages


def _mask_token(token: str) -> str:
    text = str(token)
    if len(text) <= 8:
        return "***"
    return f"{text[:4]}...{text[-4:]}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Send all LINE template previews in production format"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="print messages without sending")
    args = parser.parse_args(argv)

    messages = _build_messages()

    print("=" * 60)
    for label, text in messages:
        print(f"\n[{label}]")
        print(text)
    print("\n" + "=" * 60)

    if args.dry_run:
        print("\n[OK] dry-run: messages printed above, not sent.")
        return 0

    try:
        line_cfg = validate_line_runtime_config(os.environ)
    except RuntimeError as exc:
        print(f"\n[ERROR] LINE runtime config invalid: {exc}")
        return 2

    print(f"\n[INFO] target_id={line_cfg['group_id']}")
    print(f"[INFO] token={_mask_token(line_cfg['channel_token'])}")

    client = LinePushClient(
        channel_access_token=line_cfg["channel_token"],
        to_group_id=line_cfg["group_id"],
    )

    for label, text in messages:
        try:
            result = client.send(text)
            print(f"[OK] [{label}] sent. status={result.get('status')}")
        except Exception as exc:
            print(f"[ERROR] [{label}] failed: {exc}")
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
