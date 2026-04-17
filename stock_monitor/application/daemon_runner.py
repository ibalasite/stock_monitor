"""Daemon loop and DI assembly for stock monitor (CR-ARCH-04 SRP split).

Extracted from app.py so that app.py only handles CLI parsing and command
routing.  All business-level runtime logic lives here.
"""

from __future__ import annotations

import os
import signal
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from stock_monitor.adapters.line_messaging import LinePushClient
from stock_monitor.adapters.market_data_composite import CompositeMarketDataProvider
from stock_monitor.adapters.market_data_twse import TwseRealtimeMarketDataProvider
from stock_monitor.adapters.market_data_yahoo import YahooFinanceMarketDataProvider
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
from stock_monitor.application.valuation_calculator import RealValuationCalculator
from stock_monitor.application.valuation_scheduler import run_daily_valuation_job
from stock_monitor.bootstrap.runtime import assert_sqlite_prerequisites, validate_line_runtime_config
from stock_monitor.domain.time_bucket import TimeBucketService


def _install_signal_handlers(stop_event: threading.Event) -> None:
    """Install SIGTERM handler on Unix platforms only (CR-PLAT-02).

    On Windows, SIGTERM is not available as a signal constant, so the handler
    is skipped. KeyboardInterrupt (SIGINT) still works on both platforms via
    the normal Python exception mechanism in the daemon loop.
    """
    if sys.platform != "win32":
        def _handle_sigterm(signum: int, frame) -> None:  # type: ignore[type-arg]
            stop_event.set()

        signal.signal(signal.SIGTERM, _handle_sigterm)


def _resolve_timezone(timezone_name: str):
    try:
        return ZoneInfo(timezone_name)
    except Exception as exc:
        raise ValueError(f"Invalid timezone name: {timezone_name!r}") from exc


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
        "market_provider": CompositeMarketDataProvider(
            primary=TwseRealtimeMarketDataProvider(),
            secondary=YahooFinanceMarketDataProvider(),
        ),
        "watchlist_repo": SqliteWatchlistRepository(conn),
        "message_repo": SqliteMessageRepository(conn),
        "pending_repo": SqlitePendingRepository(conn),
        "valuation_snapshot_repo": SqliteValuationSnapshotRepository(conn),
        "logger": SqliteLogger(conn),
        "pending_fallback": JsonlPendingFallback(Path("logs/pending_delivery.jsonl")),
        "db_path": str(db_path),
    }
    return runtime


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
            try:
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
                        valuation_snapshot_repo=runtime["valuation_snapshot_repo"],
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
                if now_hhmm >= valuation_time and now_dt.weekday() < 5 and last_valuation_date != today:
                    calculator = RealValuationCalculator(
                        watchlist_repo=runtime["watchlist_repo"],
                        trade_date=today,
                        db_path=runtime["db_path"],
                    )
                    run_daily_valuation_job(
                        now_dt=now_dt,
                        is_trading_day=True,
                        calculator=calculator,
                        snapshot_repo=runtime["valuation_snapshot_repo"],
                        logger=runtime["logger"],
                        watchlist_repo=runtime["watchlist_repo"],
                        market_data_provider=runtime["market_provider"],
                    )
                    valuation_runs += 1
                    last_valuation_date = today

                run_reconcile_cycle(
                    message_repo=runtime["message_repo"],
                    pending_repo=runtime["pending_repo"],
                    logger=runtime["logger"],
                )
                reconcile_runs += 1
            except Exception as exc:
                runtime["logger"].log("ERROR", f"DAEMON_LOOP_EXCEPTION: {exc}")

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
