"""Standalone LINE push smoke test.

Usage:
  python scripts/test_line_push.py --dry-run
  python scripts/test_line_push.py --message "LINE 測試訊息"
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from stock_monitor.adapters.line_messaging import LinePushClient
from stock_monitor.bootstrap.runtime import validate_line_runtime_config


def _mask_token(token: str) -> str:
    text = str(token)
    if len(text) <= 8:
        return "***"
    return f"{text[:4]}...{text[-4:]}"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Send a standalone LINE push test message")
    parser.add_argument(
        "--message",
        default=f"[stock-monitor] line smoke test @ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        help="text message to send",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate env and print target/token mask without sending",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        line_cfg = validate_line_runtime_config(os.environ)
    except RuntimeError as exc:
        print(f"[ERROR] LINE runtime config invalid: {exc}")
        return 2

    print(f"[INFO] target_id={line_cfg['group_id']}")
    print(f"[INFO] token={_mask_token(line_cfg['channel_token'])}")
    print(f"[INFO] message={args.message}")

    if args.dry_run:
        print("[OK] dry-run completed (message not sent)")
        return 0

    client = LinePushClient(
        channel_access_token=line_cfg["channel_token"],
        to_group_id=line_cfg["group_id"],
    )
    try:
        result = client.send(args.message)
    except Exception as exc:
        print(f"[ERROR] LINE push failed: {exc}")
        return 1

    print(f"[OK] LINE push sent: {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
