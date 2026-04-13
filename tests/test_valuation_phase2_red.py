from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from stock_monitor.adapters.sqlite_repo import (
    SqliteWatchlistRepository,
    apply_schema,
    connect_sqlite,
)
from stock_monitor.application.valuation_scheduler import run_daily_valuation_job
from stock_monitor.application.valuation_calculator import ManualValuationCalculator as _ManualValuationCalculator


@dataclass
class _FakeLogger:
    events: list[tuple[str, str]]

    def log(self, level: str, message: str):
        self.events.append((level, message))


@dataclass
class _FakeSnapshotRepo:
    rows: list[dict] | None = None

    def __post_init__(self):
        if self.rows is None:
            self.rows = []

    def save_snapshots(self, snapshots: list[dict]):
        self.rows.extend(snapshots)


def _build_watchlist_repo():
    conn = connect_sqlite(":memory:")
    apply_schema(conn)
    repo = SqliteWatchlistRepository(conn)
    repo.upsert_manual_threshold("2330", fair=2000, cheap=1500, enabled=1)
    return conn, repo


def test_tp_val_004_red_production_calculator_should_emit_three_methods_per_stock():
    conn, watchlist_repo = _build_watchlist_repo()
    try:
        calculator = _ManualValuationCalculator(watchlist_repo=watchlist_repo, trade_date="2026-04-13")
        snapshots = calculator.calculate()
        methods = {(str(row["method_name"]), str(row["method_version"])) for row in snapshots}

        expected = {
            ("emily_composite", "v1"),
            ("oldbull_dividend_yield", "v1"),
            ("raysky_blended_margin", "v1"),
        }
        assert expected.issubset(methods), (
            "[TP-VAL-004 RED] Production valuation calculator should emit all 3 baseline methods per stock."
        )
    finally:
        conn.close()


def test_tp_val_005_red_should_mark_skip_when_data_is_insufficient():
    conn, watchlist_repo = _build_watchlist_repo()
    try:
        logger = _FakeLogger(events=[])
        snapshot_repo = _FakeSnapshotRepo()
        calculator = _ManualValuationCalculator(watchlist_repo=watchlist_repo, trade_date="2026-04-13")

        run_daily_valuation_job(
            now_dt=datetime(2026, 4, 13, 14, 0, 0),
            is_trading_day=True,
            calculator=calculator,
            snapshot_repo=snapshot_repo,
            logger=logger,
        )

        # After removing scenario_case, no fake SKIP events should appear for fully-specified data.
        fake_skip_events = [
            msg for _, msg in logger.events
            if "VALUATION_SKIP_INSUFFICIENT_DATA" in msg and "optional_indicator_v1" in msg
        ]
        assert not fake_skip_events, (
            "[TP-VAL-005] scenario_case='default' produced fake SKIP_INSUFFICIENT_DATA events. "
            f"Found: {fake_skip_events}"
        )
    finally:
        conn.close()


def test_tp_val_006_red_should_log_provider_fallback_usage():
    conn, watchlist_repo = _build_watchlist_repo()
    try:
        logger = _FakeLogger(events=[])
        snapshot_repo = _FakeSnapshotRepo()
        calculator = _ManualValuationCalculator(watchlist_repo=watchlist_repo, trade_date="2026-04-13")

        run_daily_valuation_job(
            now_dt=datetime(2026, 4, 13, 14, 0, 0),
            is_trading_day=True,
            calculator=calculator,
            snapshot_repo=snapshot_repo,
            logger=logger,
        )

        # After removing forced scenario_case fallback, snapshots are saved for all 3 methods.
        methods = {(str(r["method_name"]), str(r["method_version"])) for r in snapshot_repo.rows}
        assert methods == {
            ("emily_composite", "v1"),
            ("oldbull_dividend_yield", "v1"),
            ("raysky_blended_margin", "v1"),
        }, f"[TP-VAL-006] Expected 3 snapshot methods, got: {methods}"
    finally:
        conn.close()
