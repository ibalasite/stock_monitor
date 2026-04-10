from __future__ import annotations

from dataclasses import dataclass

from ._contract import require_symbol


@dataclass
class _FakeLogger:
    events: list[tuple[str, str]]

    def log(self, level: str, message: str):
        self.events.append((level, message))


@dataclass
class _FakeLineClient:
    should_fail: bool = False
    sent_payloads: list[str] | None = None

    def __post_init__(self):
        if self.sent_payloads is None:
            self.sent_payloads = []

    def send(self, payload: str):
        if self.should_fail:
            raise RuntimeError("line-500")
        self.sent_payloads.append(payload)
        return {"ok": True, "provider_id": "mock-line-id"}


@dataclass
class _FakeMessageRepo:
    should_fail: bool = False
    rows: list[dict] | None = None

    def __post_init__(self):
        if self.rows is None:
            self.rows = []

    def save_batch(self, rows: list[dict]):
        if self.should_fail:
            raise RuntimeError("db-write-failed")
        self.rows.extend(rows)


@dataclass
class _FakePendingRepo:
    should_fail: bool = False
    rows: list[dict] | None = None

    def __post_init__(self):
        if self.rows is None:
            self.rows = []

    def enqueue(self, item: dict):
        if self.should_fail:
            raise RuntimeError("ledger-write-failed")
        self.rows.append(item)

    def list_pending(self):
        return [row for row in self.rows if row.get("status") == "PENDING"]

    def mark_reconciled(self, pending_id: str):
        for row in self.rows:
            if row.get("pending_id") == pending_id:
                row["status"] = "RECONCILED"


@dataclass
class _FakeJsonlFallback:
    rows: list[dict] | None = None

    def __post_init__(self):
        if self.rows is None:
            self.rows = []

    def append(self, item: dict):
        self.rows.append(item)


@dataclass
class _TimeoutMarketProvider:
    def get_market_snapshot(self, now_epoch: int):
        raise TimeoutError("market timeout")


@dataclass
class _FlakyMarketProvider:
    fail_times: int
    calls: int = 0

    def get_market_snapshot(self, now_epoch: int):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise TimeoutError("transient timeout")
        return {"index_tick_at": now_epoch}


@dataclass
class _FakeTransactionalMessageRepo:
    committed_rows: list[dict] | None = None
    working_rows: list[dict] | None = None
    rolled_back: bool = False

    def __post_init__(self):
        if self.committed_rows is None:
            self.committed_rows = []
        if self.working_rows is None:
            self.working_rows = []

    def begin(self):
        self.working_rows = []

    def insert_row(self, row: dict):
        self.working_rows.append(row)
        if len(self.working_rows) == 2:
            raise RuntimeError("db-second-row-failed")

    def commit(self):
        self.committed_rows.extend(self.working_rows)
        self.working_rows = []

    def rollback(self):
        self.rolled_back = True
        self.working_rows = []


def test_tp_int_001_one_line_message_per_minute_with_multi_stock_hits():
    aggregate_minute_notifications = require_symbol(
        "stock_monitor.application.monitoring_workflow",
        "aggregate_minute_notifications",
        "TP-INT-001",
    )
    signals = [
        {"stock_no": "2330", "stock_status": 2, "methods_hit": ["manual_rule"]},
        {"stock_no": "2317", "stock_status": 1, "methods_hit": ["pe_band"]},
    ]
    payload = aggregate_minute_notifications("2026-04-10 10:21", signals)
    assert isinstance(payload, str), "[TP-INT-001] Aggregator must return a single string payload."
    assert "2330" in payload and "2317" in payload, (
        "[TP-INT-001] Single payload must include all stocks hit in same minute."
    )


def test_tp_int_002_same_minute_status_1_upgrades_to_2():
    merge_minute_message = require_symbol(
        "stock_monitor.application.monitoring_workflow",
        "merge_minute_message",
        "TP-INT-002",
    )
    existing = {
        "stock_no": "2330",
        "minute_bucket": "2026-04-10 10:21",
        "stock_status": 1,
        "methods_hit": ["manual_rule"],
        "message": "hit fair price",
    }
    incoming = {
        "stock_no": "2330",
        "minute_bucket": "2026-04-10 10:21",
        "stock_status": 2,
        "methods_hit": ["pe_band"],
        "message": "hit cheap price",
    }
    merged = merge_minute_message(existing, incoming)
    assert merged["stock_status"] == 2, "[TP-INT-002] Same-minute record must upgrade status 1 -> 2."
    assert "manual_rule" in merged["methods_hit"] and "pe_band" in merged["methods_hit"], (
        "[TP-INT-002] methods_hit should preserve multi-method context after upgrade."
    )


def test_tp_int_003_same_status_content_can_update():
    merge_minute_message = require_symbol(
        "stock_monitor.application.monitoring_workflow",
        "merge_minute_message",
        "TP-INT-003",
    )
    existing = {
        "stock_no": "2330",
        "minute_bucket": "2026-04-10 10:21",
        "stock_status": 1,
        "methods_hit": ["manual_rule"],
        "message": "v1",
    }
    incoming = {
        "stock_no": "2330",
        "minute_bucket": "2026-04-10 10:21",
        "stock_status": 1,
        "methods_hit": ["manual_rule", "pb_band"],
        "message": "v2",
    }
    merged = merge_minute_message(existing, incoming)
    assert merged["stock_status"] == 1, "[TP-INT-003] Same status should remain unchanged."
    assert merged["message"] == "v2", "[TP-INT-003] Message content should update to final aggregate."
    assert "pb_band" in merged["methods_hit"], "[TP-INT-003] methods_hit should include new matched method."


def test_tp_int_004_line_failure_no_message_insert_and_error_logged():
    dispatch_and_persist_minute = require_symbol(
        "stock_monitor.application.monitoring_workflow",
        "dispatch_and_persist_minute",
        "TP-INT-004",
    )

    line = _FakeLineClient(should_fail=True)
    message_repo = _FakeMessageRepo()
    pending_repo = _FakePendingRepo()
    fallback = _FakeJsonlFallback()
    logger = _FakeLogger(events=[])
    rows = [{"stock_no": "2330", "stock_status": 1, "minute_bucket": "2026-04-10 10:21"}]

    dispatch_and_persist_minute(
        minute_bucket="2026-04-10 10:21",
        rows=rows,
        line_client=line,
        message_repo=message_repo,
        pending_repo=pending_repo,
        pending_fallback=fallback,
        logger=logger,
    )

    assert message_repo.rows == [], "[TP-INT-004] On LINE failure, message table must not be written."
    assert any(level == "ERROR" for level, _ in logger.events), (
        "[TP-INT-004] On LINE failure, system must log ERROR."
    )


def test_tp_int_005_line_success_but_db_fail_creates_pending_ledger():
    dispatch_and_persist_minute = require_symbol(
        "stock_monitor.application.monitoring_workflow",
        "dispatch_and_persist_minute",
        "TP-INT-005",
    )

    line = _FakeLineClient(should_fail=False)
    message_repo = _FakeMessageRepo(should_fail=True)
    pending_repo = _FakePendingRepo(should_fail=False)
    fallback = _FakeJsonlFallback()
    logger = _FakeLogger(events=[])
    rows = [{"stock_no": "2330", "stock_status": 2, "minute_bucket": "2026-04-10 10:21"}]

    dispatch_and_persist_minute(
        minute_bucket="2026-04-10 10:21",
        rows=rows,
        line_client=line,
        message_repo=message_repo,
        pending_repo=pending_repo,
        pending_fallback=fallback,
        logger=logger,
    )

    assert len(pending_repo.rows) == 1, (
        "[TP-INT-005] LINE success + DB fail must enqueue one pending compensation item."
    )
    assert pending_repo.rows[0]["status"] == "PENDING", (
        "[TP-INT-005] Compensation item status must be PENDING."
    )


def test_tp_int_006_reconcile_pending_marks_reconciled_and_avoids_duplicate_send():
    reconcile_pending_once = require_symbol(
        "stock_monitor.application.monitoring_workflow",
        "reconcile_pending_once",
        "TP-INT-006",
    )

    line = _FakeLineClient()
    message_repo = _FakeMessageRepo()
    pending_repo = _FakePendingRepo(
        rows=[
            {
                "pending_id": "P1",
                "status": "PENDING",
                "payload": "minute=2026-04-10 10:21;stock=2330",
                "rows": [{"stock_no": "2330", "stock_status": 2, "minute_bucket": "2026-04-10 10:21"}],
            }
        ]
    )
    logger = _FakeLogger(events=[])

    reconcile_pending_once(line_client=line, message_repo=message_repo, pending_repo=pending_repo, logger=logger)
    reconcile_pending_once(line_client=line, message_repo=message_repo, pending_repo=pending_repo, logger=logger)

    assert line.sent_payloads == ["minute=2026-04-10 10:21;stock=2330"], (
        "[TP-INT-006] Reconciled item must not be re-sent on second reconciliation run."
    )
    assert pending_repo.rows[0]["status"] == "RECONCILED", (
        "[TP-INT-006] Pending item must be marked as RECONCILED after successful compensation."
    )


def test_tp_int_007_market_timeout_skips_minute_and_logs_warn():
    guard_minute_execution = require_symbol(
        "stock_monitor.application.monitoring_workflow",
        "guard_minute_execution",
        "TP-INT-007",
    )

    logger = _FakeLogger(events=[])
    decision = guard_minute_execution(
        now_epoch=1_712_710_600,
        market_data_provider=_TimeoutMarketProvider(),
        logger=logger,
    )

    assert decision.get("should_run") is False, (
        "[TP-INT-007] Market timeout must skip this minute notification run."
    )
    assert any(level == "WARN" for level, _ in logger.events), (
        "[TP-INT-007] Market timeout must produce WARN log."
    )


def test_tp_int_008_when_ledger_unavailable_fallback_jsonl_is_written():
    dispatch_and_persist_minute = require_symbol(
        "stock_monitor.application.monitoring_workflow",
        "dispatch_and_persist_minute",
        "TP-INT-008",
    )

    line = _FakeLineClient(should_fail=False)
    message_repo = _FakeMessageRepo(should_fail=True)
    pending_repo = _FakePendingRepo(should_fail=True)
    fallback = _FakeJsonlFallback()
    logger = _FakeLogger(events=[])
    rows = [{"stock_no": "2317", "stock_status": 1, "minute_bucket": "2026-04-10 10:21"}]

    dispatch_and_persist_minute(
        minute_bucket="2026-04-10 10:21",
        rows=rows,
        line_client=line,
        message_repo=message_repo,
        pending_repo=pending_repo,
        pending_fallback=fallback,
        logger=logger,
    )

    assert len(fallback.rows) == 1, (
        "[TP-INT-008] When DB is not writable, fallback pending_delivery.jsonl must receive one item."
    )


def test_tp_int_009_batch_message_write_failure_must_rollback_all():
    persist_message_rows_transactional = require_symbol(
        "stock_monitor.application.monitoring_workflow",
        "persist_message_rows_transactional",
        "TP-INT-009",
    )

    repo = _FakeTransactionalMessageRepo()
    rows = [
        {"stock_no": "2330", "stock_status": 2, "minute_bucket": "2026-04-10 10:21"},
        {"stock_no": "2317", "stock_status": 1, "minute_bucket": "2026-04-10 10:21"},
    ]

    try:
        persist_message_rows_transactional(repo=repo, rows=rows)
    except RuntimeError:
        pass

    assert repo.rolled_back is True, (
        "[TP-INT-009] Batch write failure must trigger rollback."
    )
    assert repo.committed_rows == [], (
        "[TP-INT-009] After rollback, no partial message rows should be committed."
    )


def test_tp_int_010_market_retry_succeeds_within_retry_budget():
    fetch_market_with_retry = require_symbol(
        "stock_monitor.application.monitoring_workflow",
        "fetch_market_with_retry",
        "TP-INT-010",
    )
    provider = _FlakyMarketProvider(fail_times=1)
    logger = _FakeLogger(events=[])

    result = fetch_market_with_retry(
        now_epoch=1_712_710_600,
        market_data_provider=provider,
        max_retries=2,
        logger=logger,
    )

    assert result.get("ok") is True, (
        "[TP-INT-010] Transient market fetch failure should recover within retry budget."
    )
    assert provider.calls == 2, "[TP-INT-010] Provider should be retried and succeed on second call."
    assert any("retry" in message.lower() for _, message in logger.events), (
        "[TP-INT-010] Retry attempts should be logged."
    )


def test_tp_int_011_market_retry_exhausted_skips_minute_without_backfill():
    fetch_market_with_retry = require_symbol(
        "stock_monitor.application.monitoring_workflow",
        "fetch_market_with_retry",
        "TP-INT-011",
    )
    provider = _FlakyMarketProvider(fail_times=3)
    logger = _FakeLogger(events=[])

    result = fetch_market_with_retry(
        now_epoch=1_712_710_600,
        market_data_provider=provider,
        max_retries=2,
        logger=logger,
    )

    assert result.get("ok") is False, (
        "[TP-INT-011] Market fetch should fail when retry budget is exhausted."
    )
    assert result.get("skip_minute") is True, (
        "[TP-INT-011] Exhausted retries should skip this minute run."
    )
    assert result.get("backfill_allowed") is False, (
        "[TP-INT-011] Exhausted retries should not allow backfill for stale minute."
    )
