from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import sqlite3

from ._contract import require_symbol


@dataclass
class _RecorderLogger:
    events: list[tuple[str, str]]

    def log(self, level: str, message: str):
        self.events.append((level, message))


@dataclass
class _OkLineClient:
    sent: list[str] | None = None

    def __post_init__(self):
        if self.sent is None:
            self.sent = []

    def send(self, payload: str):
        self.sent.append(payload)


@dataclass
class _FailLineClient:
    def send(self, payload: str):
        raise RuntimeError("line-down")


@dataclass
class _OkMessageRepo:
    rows: list[dict] | None = None

    def __post_init__(self):
        if self.rows is None:
            self.rows = []

    def save_batch(self, rows: list[dict]):
        self.rows.extend(rows)


@dataclass
class _NoopPendingRepo:
    items: list[dict] | None = None

    def __post_init__(self):
        if self.items is None:
            self.items = []

    def enqueue(self, item: dict):
        self.items.append(item)

    def list_pending(self):
        return list(self.items)

    def mark_reconciled(self, pending_id: str):
        for item in self.items:
            if item.get("pending_id") == pending_id:
                item["status"] = "RECONCILED"


@dataclass
class _NoopFallback:
    rows: list[dict] | None = None

    def __post_init__(self):
        if self.rows is None:
            self.rows = []

    def append(self, item: dict):
        self.rows.append(item)


@dataclass
class _ReconcileMessageRepo:
    saved: list[dict] | None = None

    def __post_init__(self):
        if self.saved is None:
            self.saved = []

    def save_batch(self, rows: list[dict]):
        self.saved.extend(rows)


@dataclass
class _SnapshotProviderOk:
    def get_market_snapshot(self, now_epoch: int):
        return {"index_tick_at": now_epoch}


@dataclass
class _SnapshotProviderError:
    def get_market_snapshot(self, now_epoch: int):
        raise RuntimeError("market-generic-error")


@dataclass
class _ProviderGenericError:
    def get_market_snapshot(self, now_epoch: int):
        raise ValueError("bad-response")


@dataclass
class _RepoCommitOnly:
    began: bool = False
    committed: bool = False
    rolled_back: bool = False
    rows: list[dict] | None = None

    def __post_init__(self):
        if self.rows is None:
            self.rows = []

    def begin(self):
        self.began = True

    def insert_row(self, row: dict):
        self.rows.append(row)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


class _CursorOne:
    def __init__(self, value: int):
        self._value = value

    def fetchone(self):
        return (self._value,)


class _JsonInvalidButAvailableConnection:
    def execute(self, sql: str):
        lower = sql.lower()
        if "pragma foreign_keys" in lower:
            return _CursorOne(1)
        if "json_valid" in lower:
            return _CursorOne(0)
        return _CursorOne(1)


def test_cov_workflow_string_normalize_dispatch_success_and_reconcile_error_path():
    _normalize_methods = require_symbol(
        "stock_monitor.application.monitoring_workflow",
        "_normalize_methods",
        "COV-MWF-001",
    )
    dispatch_and_persist_minute = require_symbol(
        "stock_monitor.application.monitoring_workflow",
        "dispatch_and_persist_minute",
        "COV-MWF-001",
    )
    reconcile_pending_once = require_symbol(
        "stock_monitor.application.monitoring_workflow",
        "reconcile_pending_once",
        "COV-MWF-001",
    )

    methods = _normalize_methods(" pe_band , manual_rule , pe_band ,, ")
    assert methods == ["manual_rule", "pe_band"], (
        "[COV-MWF-001] String methods list must be split, deduplicated, and normalized."
    )

    line = _OkLineClient()
    message_repo = _OkMessageRepo()
    pending_repo = _NoopPendingRepo()
    logger = _RecorderLogger(events=[])
    fallback = _NoopFallback()
    rows = [{"stock_no": "2330", "stock_status": 1, "minute_bucket": "2026-04-10 10:21"}]

    result = dispatch_and_persist_minute(
        minute_bucket="2026-04-10 10:21",
        rows=rows,
        line_client=line,
        message_repo=message_repo,
        pending_repo=pending_repo,
        pending_fallback=fallback,
        logger=logger,
    )
    assert result.get("status") == "persisted", (
        "[COV-MWF-001] Successful LINE + DB path must persist rows immediately."
    )
    assert len(message_repo.rows) == 1, "[COV-MWF-001] Persisted rows must be written to message repo."

    pending_repo.items = [
        {
            "pending_id": "P-1",
            "status": "PENDING",
            "payload": "payload",
            "rows": [{"stock_no": "2330"}],
        }
    ]
    reconcile_result = reconcile_pending_once(
        line_client=_FailLineClient(),
        message_repo=_ReconcileMessageRepo(),
        pending_repo=pending_repo,
        logger=logger,
    )
    assert reconcile_result.get("reconciled") == 0, (
        "[COV-MWF-001] Failed reconcile send must not increase reconciled count."
    )
    assert any("RECONCILE_FAILED" in message for _, message in logger.events), (
        "[COV-MWF-001] Reconcile failure must be logged."
    )


def test_cov_guard_and_retry_generic_error_paths_and_terminal_return():
    guard_minute_execution = require_symbol(
        "stock_monitor.application.monitoring_workflow",
        "guard_minute_execution",
        "COV-MWF-002",
    )
    fetch_market_with_retry = require_symbol(
        "stock_monitor.application.monitoring_workflow",
        "fetch_market_with_retry",
        "COV-MWF-002",
    )

    logger = _RecorderLogger(events=[])

    ok_decision = guard_minute_execution(
        now_epoch=1_712_710_600,
        market_data_provider=_SnapshotProviderOk(),
        logger=logger,
    )
    assert ok_decision.get("should_run") is True, (
        "[COV-MWF-002] Guard should allow run when market snapshot is returned."
    )

    err_decision = guard_minute_execution(
        now_epoch=1_712_710_600,
        market_data_provider=_SnapshotProviderError(),
        logger=logger,
    )
    assert err_decision.get("reason") == "MARKET_ERROR", (
        "[COV-MWF-002] Generic provider exception must map to MARKET_ERROR."
    )

    retry_result = fetch_market_with_retry(
        now_epoch=1_712_710_600,
        market_data_provider=_ProviderGenericError(),
        max_retries=2,
        logger=logger,
    )
    assert retry_result.get("ok") is False, (
        "[COV-MWF-002] Generic fetch error should fail current minute run."
    )
    assert any("MARKET_FETCH_FAILED" in message for _, message in logger.events), (
        "[COV-MWF-002] Generic fetch failure should be logged."
    )

    terminal_result = fetch_market_with_retry(
        now_epoch=1_712_710_600,
        market_data_provider=_SnapshotProviderOk(),
        max_retries=-1,
        logger=logger,
    )
    assert terminal_result.get("ok") is False, (
        "[COV-MWF-002] Negative retry budget must return terminal fail-safe result."
    )


def test_cov_persist_message_rows_transactional_commit_path():
    persist_message_rows_transactional = require_symbol(
        "stock_monitor.application.monitoring_workflow",
        "persist_message_rows_transactional",
        "COV-MWF-003",
    )
    repo = _RepoCommitOnly()

    persist_message_rows_transactional(
        repo=repo,
        rows=[
            {"stock_no": "2330", "stock_status": 1},
            {"stock_no": "2317", "stock_status": 2},
        ],
    )

    assert repo.began is True, "[COV-MWF-003] Transaction must begin before row inserts."
    assert repo.committed is True, "[COV-MWF-003] Success path must commit transaction."
    assert repo.rolled_back is False, "[COV-MWF-003] Success path must not rollback transaction."


def test_cov_trading_session_remaining_branches():
    is_in_trading_session = require_symbol(
        "stock_monitor.application.trading_session",
        "is_in_trading_session",
        "COV-TRD-001",
    )
    evaluate_market_open_status = require_symbol(
        "stock_monitor.application.trading_session",
        "evaluate_market_open_status",
        "COV-TRD-001",
    )

    assert is_in_trading_session(datetime(2026, 4, 10, 10, 0, 0)) is True, (
        "[COV-TRD-001] Weekday in-range time should be trading session."
    )
    assert is_in_trading_session(datetime(2026, 4, 12, 10, 0, 0)) is False, (
        "[COV-TRD-001] Weekend should never be trading session."
    )

    weekend = evaluate_market_open_status(
        now_dt=datetime(2026, 4, 12, 9, 5, 0),
        latest_index_tick_dt=None,
    )
    assert weekend.get("reason") == "weekend", (
        "[COV-TRD-001] Weekend branch must classify market as closed."
    )

    before_check = evaluate_market_open_status(
        now_dt=datetime(2026, 4, 10, 8, 44, 59),
        latest_index_tick_dt=None,
    )
    assert before_check.get("reason") == "before_open_check", (
        "[COV-TRD-001] Before 08:45 should return before_open_check."
    )

    after_close = evaluate_market_open_status(
        now_dt=datetime(2026, 4, 10, 13, 31, 0),
        latest_index_tick_dt=datetime(2026, 4, 10, 13, 0, 0),
    )
    assert after_close.get("reason") == "after_close", (
        "[COV-TRD-001] After trading end should return after_close."
    )


def test_cov_valuation_scheduler_skip_when_not_1400():
    run_daily_valuation_job = require_symbol(
        "stock_monitor.application.valuation_scheduler",
        "run_daily_valuation_job",
        "COV-VAL-001",
    )
    logger = _RecorderLogger(events=[])

    class _Calculator:
        def calculate(self):
            return [{"stock_no": "2330"}]

    class _SnapshotRepo:
        def save_snapshots(self, snapshots: list[dict]):
            raise AssertionError("[COV-VAL-001] save_snapshots should not be called when skipped.")

    result = run_daily_valuation_job(
        now_dt=datetime(2026, 4, 10, 13, 59, 0),
        is_trading_day=True,
        calculator=_Calculator(),
        snapshot_repo=_SnapshotRepo(),
        logger=logger,
    )
    assert result.get("status") == "skipped", (
        "[COV-VAL-001] Trading day but non-14:00 time must skip valuation."
    )
    assert any("NOT_SCHEDULED_TIME" in message for _, message in logger.events), (
        "[COV-VAL-001] Non-scheduled skip reason should be logged."
    )


def test_cov_runtime_health_metrics_policy_and_bucket_remaining_paths():
    assert_sqlite_prerequisites = require_symbol(
        "stock_monitor.bootstrap.runtime",
        "assert_sqlite_prerequisites",
        "COV-ENV-001",
    )
    validate_line_runtime_config = require_symbol(
        "stock_monitor.bootstrap.runtime",
        "validate_line_runtime_config",
        "COV-ENV-001",
    )
    health_check = require_symbol(
        "stock_monitor.bootstrap.health",
        "health_check",
        "COV-ENV-001",
    )
    compute_notification_accuracy = require_symbol(
        "stock_monitor.domain.metrics",
        "compute_notification_accuracy",
        "COV-MET-001",
    )
    PriorityPolicy = require_symbol(
        "stock_monitor.domain.policies",
        "PriorityPolicy",
        "COV-POL-001",
    )
    aggregate_stock_signals = require_symbol(
        "stock_monitor.domain.policies",
        "aggregate_stock_signals",
        "COV-POL-001",
    )
    TimeBucketService = require_symbol(
        "stock_monitor.domain.time_bucket",
        "TimeBucketService",
        "COV-BKT-001",
    )

    conn = sqlite3.connect(":memory:")
    try:
        try:
            assert_sqlite_prerequisites(conn)
            assert False, "[COV-ENV-001] foreign_keys=OFF should fail-fast."
        except RuntimeError as exc:
            assert "foreign_keys" in str(exc)
    finally:
        conn.close()

    try:
        assert_sqlite_prerequisites(_JsonInvalidButAvailableConnection())
        assert False, "[COV-ENV-001] json_valid() returning 0 should fail-fast."
    except RuntimeError as exc:
        assert "JSON1 unavailable" in str(exc)

    conn2 = sqlite3.connect(":memory:")
    try:
        health = health_check(conn2)
        assert health.get("status") == "error", (
            "[COV-ENV-001] Health check should return error when prerequisites fail."
        )
    finally:
        conn2.close()

    valid_cfg = validate_line_runtime_config(
        {
            "LINE_CHANNEL_ACCESS_TOKEN": "validtoken_12345",
            "LINE_TO_GROUP_ID": "C1234567890",
        }
    )
    assert valid_cfg.get("channel_token") == "validtoken_12345", (
        "[COV-ENV-001] Valid runtime config should return normalized token."
    )
    assert valid_cfg.get("group_id") == "C1234567890", (
        "[COV-ENV-001] Valid runtime config should return normalized group id."
    )

    zero_denominator = compute_notification_accuracy(
        total_signal_minutes=100,
        outage_minutes=100,
        correct_notified_minutes=0,
    )
    assert zero_denominator.get("accuracy") == 0.0, (
        "[COV-MET-001] Zero effective denominator must produce 0 accuracy."
    )
    assert zero_denominator.get("pass") is False, (
        "[COV-MET-001] 0 accuracy must not pass KPI threshold."
    )

    policy = PriorityPolicy()
    assert policy.resolve_status([]) is None, (
        "[COV-POL-001] Empty statuses should return None."
    )
    assert aggregate_stock_signals("2330", []) == [], (
        "[COV-POL-001] Empty method hits should aggregate to empty event list."
    )

    service = TimeBucketService("Asia/Taipei")
    # Force tz-aware path even on environments without IANA tz database.
    service._tz = timezone.utc
    aware_dt = datetime(2026, 4, 10, 2, 21, 30, tzinfo=timezone.utc)
    bucket = service.to_minute_bucket(aware_dt)
    assert bucket == "2026-04-10 02:21", (
        "[COV-BKT-001] Aware datetime should go through timezone-aware conversion path."
    )
