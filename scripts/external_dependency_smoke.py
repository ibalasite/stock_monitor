"""External dependency smoke checks for TWSE + LINE.

Usage examples:
  python scripts/external_dependency_smoke.py --stock-no 2330 --skip-line
  python scripts/external_dependency_smoke.py --line-send --require-line-config
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from stock_monitor.adapters.line_messaging import LinePushClient
from stock_monitor.adapters.market_data_twse import TwseRealtimeMarketDataProvider
from stock_monitor.bootstrap.runtime import validate_line_runtime_config


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="External dependency smoke (TWSE + LINE)")
    parser.add_argument("--stock-no", default="2330", help="stock symbol for quote probe")
    parser.add_argument("--timeout-sec", type=int, default=10)
    parser.add_argument("--skip-line", action="store_true", help="skip LINE config/channel check")
    parser.add_argument("--line-send", action="store_true", help="actually push one sandbox LINE message")
    parser.add_argument("--require-line-config", action="store_true", help="fail when LINE config is missing/invalid")
    parser.add_argument(
        "--line-message",
        default=f"[stock-monitor] nightly external smoke @ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        help="message text when --line-send is enabled",
    )
    return parser


def _check_twse(stock_no: str, timeout_sec: int) -> dict:
    provider = TwseRealtimeMarketDataProvider(timeout_sec=timeout_sec)
    now_epoch = int(time.time())
    snapshot = provider.get_market_snapshot(now_epoch)

    probe = {
        "stock_no": stock_no,
        "quote_available": False,
        "price": None,
        "tick_at": None,
    }
    quotes = provider.get_realtime_quotes([stock_no])
    quote = quotes.get(stock_no)
    if quote:
        probe["quote_available"] = True
        probe["price"] = quote.get("price")
        probe["tick_at"] = quote.get("tick_at")

    return {
        "ok": True,
        "snapshot": {
            "source": snapshot.get("source"),
            "index_tick_at": snapshot.get("index_tick_at"),
            "index_price": snapshot.get("index_price"),
        },
        "quote_probe": probe,
    }


def _check_line(*, skip_line: bool, require_line_config: bool, line_send: bool, line_message: str) -> dict:
    if skip_line:
        return {"ok": True, "skipped": True, "reason": "skip-line"}

    try:
        cfg = validate_line_runtime_config(os.environ)
    except RuntimeError as exc:
        if require_line_config:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "skipped": True, "reason": str(exc)}

    result = {
        "ok": True,
        "skipped": False,
        "send_attempted": bool(line_send),
        "target_group_id": cfg["group_id"],
    }

    if not line_send:
        result["mode"] = "config-only"
        return result

    client = LinePushClient(
        channel_access_token=cfg["channel_token"],
        to_group_id=cfg["group_id"],
    )
    try:
        send_result = client.send(line_message)
    except Exception as exc:
        return {"ok": False, "skipped": False, "send_attempted": True, "error": str(exc)}

    result["mode"] = "push"
    result["send_result"] = send_result
    return result


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    summary: dict[str, dict] = {}
    failures: list[str] = []

    try:
        summary["twse"] = _check_twse(stock_no=str(args.stock_no), timeout_sec=int(args.timeout_sec))
    except Exception as exc:
        summary["twse"] = {"ok": False, "error": str(exc)}
    if not summary["twse"].get("ok"):
        failures.append("twse")

    summary["line"] = _check_line(
        skip_line=bool(args.skip_line),
        require_line_config=bool(args.require_line_config),
        line_send=bool(args.line_send),
        line_message=str(args.line_message),
    )
    if not summary["line"].get("ok"):
        failures.append("line")

    output = {
        "ok": len(failures) == 0,
        "failures": failures,
        "summary": summary,
    }
    print(json.dumps(output, ensure_ascii=False))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
