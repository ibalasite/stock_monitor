from __future__ import annotations

from datetime import datetime

import pytest

from ._contract import require_symbol


def test_tp_trd_001_market_is_open_after_0845_when_index_has_new_data():
    evaluate_market_open_status = require_symbol(
        "stock_monitor.application.trading_session",
        "evaluate_market_open_status",
        "TP-TRD-001",
    )

    decision = evaluate_market_open_status(
        now_dt=datetime(2026, 4, 10, 8, 46, 0),
        latest_index_tick_dt=datetime(2026, 4, 10, 8, 45, 0),
    )

    assert decision.get("is_open") is True, (
        "[TP-TRD-001] After 08:45, index having same-day new data should be treated as open."
    )


def test_tp_trd_002_market_is_closed_after_0900_when_index_has_no_new_data():
    evaluate_market_open_status = require_symbol(
        "stock_monitor.application.trading_session",
        "evaluate_market_open_status",
        "TP-TRD-002",
    )

    decision = evaluate_market_open_status(
        now_dt=datetime(2026, 4, 10, 9, 1, 0),
        latest_index_tick_dt=None,
    )

    assert decision.get("is_open") is False, (
        "[TP-TRD-002] After 09:00, missing same-day index data should be treated as closed."
    )


def _call_bucket_service(service, dt: datetime) -> str:
    for method_name in ("to_minute_bucket", "build_minute_bucket", "generate", "from_datetime"):
        method = getattr(service, method_name, None)
        if callable(method):
            return method(dt)
    raise AssertionError(
        "[TP-BKT-001] TimeBucketService must provide a minute bucket generation method."
    )


def test_tp_bkt_001_time_bucket_must_be_generated_by_time_bucket_service():
    TimeBucketService = require_symbol(
        "stock_monitor.domain.time_bucket",
        "TimeBucketService",
        "TP-BKT-001",
    )
    guard_bucket_source = require_symbol(
        "stock_monitor.domain.time_bucket",
        "guard_bucket_source",
        "TP-BKT-001",
    )

    service = TimeBucketService("Asia/Taipei")
    bucket = _call_bucket_service(service, datetime(2026, 4, 10, 10, 21, 37))

    assert bucket == "2026-04-10 10:21", (
        "[TP-BKT-001] TimeBucketService should normalize to YYYY-MM-DD HH:mm."
    )
    assert guard_bucket_source("TimeBucketService") is True, (
        "[TP-BKT-001] Source guard should accept TimeBucketService."
    )
    with pytest.raises(ValueError):
        guard_bucket_source("repository_inline_concat")


def test_tp_kpi_001_notification_accuracy_excludes_outage_minutes():
    compute_notification_accuracy = require_symbol(
        "stock_monitor.domain.metrics",
        "compute_notification_accuracy",
        "TP-KPI-001",
    )

    result = compute_notification_accuracy(
        total_signal_minutes=1000,
        outage_minutes=20,
        correct_notified_minutes=972,
    )

    assert result.get("effective_denominator") == 980, (
        "[TP-KPI-001] Effective denominator must exclude outage minutes."
    )
    assert abs(result.get("accuracy", 0.0) - 0.9918) < 1e-4, (
        "[TP-KPI-001] Accuracy should be 972/980 (=99.18%)."
    )
    assert result.get("pass") is True, (
        "[TP-KPI-001] Accuracy result should pass 99% threshold."
    )


def test_tp_trd_003_market_closed_after_trading_end_1330():
    """UAT-004 After 13:30 (post-market) – trading session must be closed."""
    is_in_trading_session = require_symbol(
        "stock_monitor.application.trading_session",
        "is_in_trading_session",
        "TP-TRD-003",
    )
    # 13:31 is past TRADING_END=13:30 – should not be in trading session
    result = is_in_trading_session(now_dt=datetime(2026, 4, 10, 13, 31, 0))
    assert result is False, (
        "[TP-TRD-003] After TRADING_END (13:30), is_in_trading_session must return False."
    )
    # 13:29 is within TRADING_END – should still be in session (boundary sanity check)
    result_within = is_in_trading_session(now_dt=datetime(2026, 4, 10, 13, 29, 0))
    assert result_within is True, (
        "[TP-TRD-003] Before TRADING_END (13:30), is_in_trading_session must return True."
    )
