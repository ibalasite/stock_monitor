from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ._contract import require_symbol


@dataclass
class _FakeLogger:
    events: list[tuple[str, str]]

    def log(self, level: str, message: str):
        self.events.append((level, message))


@dataclass
class _FakeSnapshotRepo:
    upserts: list[dict] | None = None

    def __post_init__(self):
        if self.upserts is None:
            self.upserts = []

    def save_snapshots(self, snapshots: list[dict]):
        self.upserts.extend(snapshots)


@dataclass
class _SuccessCalculator:
    def calculate(self):
        return [
            {"stock_no": "2330", "method_name": "manual_rule", "method_version": "v1", "fair_value": 1500, "cheap_value": 1000},
            {"stock_no": "2317", "method_name": "pe_band", "method_version": "v1", "fair_value": 145, "cheap_value": 130},
        ]


@dataclass
class _FailCalculator:
    def calculate(self):
        raise RuntimeError("valuation failed")


def test_tp_val_001_run_daily_valuation_at_1400_on_trading_day():
    run_daily_valuation_job = require_symbol(
        "stock_monitor.application.valuation_scheduler",
        "run_daily_valuation_job",
        "TP-VAL-001",
    )

    repo = _FakeSnapshotRepo()
    logger = _FakeLogger(events=[])
    result = run_daily_valuation_job(
        now_dt=datetime(2026, 4, 10, 14, 0, 0),
        is_trading_day=True,
        calculator=_SuccessCalculator(),
        snapshot_repo=repo,
        logger=logger,
    )

    assert result.get("status") == "executed", "[TP-VAL-001] 14:00 trading day must execute valuation job."
    assert len(repo.upserts) >= 1, "[TP-VAL-001] Executed valuation job must persist snapshots."


def test_tp_val_002_skip_daily_valuation_on_non_trading_day():
    run_daily_valuation_job = require_symbol(
        "stock_monitor.application.valuation_scheduler",
        "run_daily_valuation_job",
        "TP-VAL-002",
    )

    repo = _FakeSnapshotRepo()
    logger = _FakeLogger(events=[])
    result = run_daily_valuation_job(
        now_dt=datetime(2026, 4, 11, 14, 0, 0),
        is_trading_day=False,
        calculator=_SuccessCalculator(),
        snapshot_repo=repo,
        logger=logger,
    )

    assert result.get("status") == "skipped", "[TP-VAL-002] Non-trading day must skip valuation job."
    assert repo.upserts == [], "[TP-VAL-002] Skipped valuation must not write snapshots."


def test_tp_val_003_calculation_failure_does_not_overwrite_previous_snapshot():
    run_daily_valuation_job = require_symbol(
        "stock_monitor.application.valuation_scheduler",
        "run_daily_valuation_job",
        "TP-VAL-003",
    )

    repo = _FakeSnapshotRepo(
        upserts=[
            {
                "stock_no": "2330",
                "method_name": "manual_rule",
                "method_version": "v1",
                "valuation_date": "2026-04-09",
                "fair_value": 1490,
                "cheap_value": 990,
            }
        ]
    )
    logger = _FakeLogger(events=[])
    before = list(repo.upserts)

    result = run_daily_valuation_job(
        now_dt=datetime(2026, 4, 10, 14, 0, 0),
        is_trading_day=True,
        calculator=_FailCalculator(),
        snapshot_repo=repo,
        logger=logger,
    )

    assert result.get("status") == "failed", "[TP-VAL-003] Failed calculation should report failed status."
    assert repo.upserts == before, "[TP-VAL-003] Failed calculation must not overwrite previous snapshots."
    assert any(level == "ERROR" for level, _ in logger.events), (
        "[TP-VAL-003] Failed calculation must generate ERROR log."
    )


def test_cov_val_002_event_normalization_and_persist_failure():
    _iter_calculation_events = require_symbol(
        "stock_monitor.application.valuation_scheduler",
        "_iter_calculation_events",
        "COV-VAL-002",
    )
    run_daily_valuation_job = require_symbol(
        "stock_monitor.application.valuation_scheduler",
        "run_daily_valuation_job",
        "COV-VAL-002",
    )

    class _CalculatorWithGetEvents:
        def calculate(self):
            return [{"stock_no": "2330"}]

        def get_events(self):
            return [("warn", "tuple-event"), "plain-event"]

    normalized_events = _iter_calculation_events(_CalculatorWithGetEvents())
    assert normalized_events == [("WARN", "tuple-event"), ("INFO", "plain-event")], (
        "[COV-VAL-002] Event normalization must support get_events() fallback and non-tuple items."
    )

    class _FailSnapshotRepo:
        def save_snapshots(self, snapshots: list[dict]):
            _ = snapshots
            raise RuntimeError("persist down")

    logger = _FakeLogger(events=[])
    result = run_daily_valuation_job(
        now_dt=datetime(2026, 4, 10, 14, 0, 0),
        is_trading_day=True,
        calculator=_CalculatorWithGetEvents(),
        snapshot_repo=_FailSnapshotRepo(),
        logger=logger,
    )
    assert result.get("status") == "failed", "[COV-VAL-002] Snapshot persist failure must return failed."
    assert any("VALUATION_PERSIST_FAILED" in message for _, message in logger.events), (
        "[COV-VAL-002] Snapshot persist failure must be logged."
    )


def test_tp_val_007_valuation_job_saves_stock_names():
    """FR-18: run_daily_valuation_job must save stock names via watchlist_repo when market_data_provider provided."""
    run_daily_valuation_job = require_symbol(
        "stock_monitor.application.valuation_scheduler",
        "run_daily_valuation_job",
        "TP-VAL-007",
    )

    saved_names: dict[str, str] = {}

    class _FakeWatchlistRepo:
        def list_enabled(self):
            return [
                {"stock_no": "2330", "stock_name": ""},
                {"stock_no": "2317", "stock_name": ""},
            ]

        def update_stock_names(self, names: dict) -> None:
            saved_names.update(names)

    class _FakeMarketProvider:
        def get_realtime_quotes(self, stock_nos):
            return {
                "2330": {"price": 1900.0, "tick_at": 1776000000, "name": "台積電"},
                "2317": {"price": 140.0, "tick_at": 1776000000, "name": "鴻海"},
            }

    repo = _FakeSnapshotRepo()
    logger = _FakeLogger(events=[])

    result = run_daily_valuation_job(
        now_dt=datetime(2026, 4, 10, 14, 0, 0),
        is_trading_day=True,
        calculator=_SuccessCalculator(),
        snapshot_repo=repo,
        logger=logger,
        watchlist_repo=_FakeWatchlistRepo(),
        market_data_provider=_FakeMarketProvider(),
    )

    assert result.get("status") == "executed", "[TP-VAL-007] Must execute valuation job."
    assert saved_names.get("2330") == "台積電", "[TP-VAL-007] Must save 台積電 for 2330."
    assert saved_names.get("2317") == "鴻海", "[TP-VAL-007] Must save 鴻海 for 2317."

