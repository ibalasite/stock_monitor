from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pytest
from pytest_bdd import given, parsers, then, when

from stock_monitor.adapters.sqlite_repo import (
    JsonlPendingFallback,
    SqliteLogger,
    SqliteMessageRepository,
    SqlitePendingRepository,
    SqliteWatchlistRepository,
    apply_schema,
    connect_sqlite,
)
from stock_monitor.application.runtime_service import run_minute_cycle, run_reconcile_cycle
from stock_monitor.domain.time_bucket import TimeBucketService


@dataclass
class _FakeLineClient:
    sent: list[str]

    def send(self, message: str):
        self.sent.append(message)
        return {"ok": True}


@dataclass
class _FakeMarketProvider:
    snapshot: dict
    quotes: dict[str, dict]

    def get_market_snapshot(self, now_epoch: int):
        return dict(self.snapshot)

    def get_realtime_quotes(self, stock_nos: list[str]):
        return {key: value for key, value in self.quotes.items() if key in stock_nos}


@pytest.fixture
def smoke_ctx(tmp_path) -> dict:
    conn = connect_sqlite(":memory:")
    apply_schema(conn)
    watchlist_repo = SqliteWatchlistRepository(conn)
    message_repo = SqliteMessageRepository(conn)
    pending_repo = SqlitePendingRepository(conn)
    logger = SqliteLogger(conn)
    line_client = _FakeLineClient(sent=[])
    market_provider = _FakeMarketProvider(snapshot={}, quotes={})
    fallback = JsonlPendingFallback(tmp_path / "pending_delivery.jsonl")
    ctx = {
        "conn": conn,
        "watchlist_repo": watchlist_repo,
        "message_repo": message_repo,
        "pending_repo": pending_repo,
        "logger": logger,
        "line_client": line_client,
        "market_provider": market_provider,
        "pending_fallback": fallback,
        "result": None,
        "reconcile_result": None,
        "now_dt": None,
    }
    try:
        yield ctx
    finally:
        conn.close()


@given(parsers.parse('runtime time is "{iso_ts}"'))
def given_runtime_time(smoke_ctx: dict, iso_ts: str):
    smoke_ctx["now_dt"] = datetime.fromisoformat(iso_ts)


@given("market snapshot is available")
def given_market_snapshot_available(smoke_ctx: dict):
    now_dt = smoke_ctx["now_dt"]
    assert now_dt is not None
    smoke_ctx["market_provider"].snapshot = {"index_tick_at": int(now_dt.timestamp())}


@given(parsers.parse('watchlist has stock "{stock_no}" fair {fair:g} cheap {cheap:g}'))
def given_watchlist_stock(smoke_ctx: dict, stock_no: str, fair: float, cheap: float):
    smoke_ctx["watchlist_repo"].upsert_manual_threshold(stock_no=stock_no, fair=fair, cheap=cheap, enabled=1)


@given(parsers.parse('realtime quote for "{stock_no}" is {price:g}'))
def given_realtime_quote(smoke_ctx: dict, stock_no: str, price: float):
    now_dt = smoke_ctx["now_dt"]
    assert now_dt is not None
    smoke_ctx["market_provider"].quotes[stock_no] = {"price": float(price), "tick_at": int(now_dt.timestamp())}


@given(parsers.parse('previous message for "{stock_no}" status {status:d} was sent {seconds:d} seconds ago'))
def given_previous_message(smoke_ctx: dict, stock_no: str, status: int, seconds: int):
    now_dt = smoke_ctx["now_dt"]
    assert now_dt is not None
    minute_bucket = TimeBucketService("Asia/Taipei").to_minute_bucket(now_dt)
    smoke_ctx["message_repo"].save_batch(
        [
            {
                "stock_no": stock_no,
                "message": "seed",
                "stock_status": status,
                "methods_hit": ["manual_rule"],
                "minute_bucket": minute_bucket,
                "update_time": int(now_dt.timestamp()) - seconds,
            }
        ]
    )


@given("a pending compensation item exists")
def given_pending_compensation(smoke_ctx: dict):
    now_dt = smoke_ctx["now_dt"]
    assert now_dt is not None
    minute_bucket = TimeBucketService("Asia/Taipei").to_minute_bucket(now_dt)
    smoke_ctx["watchlist_repo"].upsert_manual_threshold(stock_no="2330", fair=1500, cheap=1000, enabled=1)
    smoke_ctx["pending_repo"].enqueue(
        {
            "minute_bucket": minute_bucket,
            "payload": "pending-payload",
            "rows": [
                {
                    "stock_no": "2330",
                    "message": "reconcile",
                    "stock_status": 2,
                    "methods_hit": ["manual_rule"],
                    "minute_bucket": minute_bucket,
                    "update_time": int(now_dt.timestamp()),
                }
            ],
            "error": "db failed",
        }
    )


@when("I execute one monitor cycle")
def when_execute_monitor_cycle(smoke_ctx: dict):
    smoke_ctx["result"] = run_minute_cycle(
        now_dt=smoke_ctx["now_dt"],
        market_data_provider=smoke_ctx["market_provider"],
        line_client=smoke_ctx["line_client"],
        watchlist_repo=smoke_ctx["watchlist_repo"],
        message_repo=smoke_ctx["message_repo"],
        pending_repo=smoke_ctx["pending_repo"],
        pending_fallback=smoke_ctx["pending_fallback"],
        logger=smoke_ctx["logger"],
        cooldown_seconds=300,
        retry_count=3,
        timezone_name="Asia/Taipei",
    )


@when("I execute reconcile cycle")
def when_execute_reconcile_cycle(smoke_ctx: dict):
    smoke_ctx["reconcile_result"] = run_reconcile_cycle(
        line_client=smoke_ctx["line_client"],
        message_repo=smoke_ctx["message_repo"],
        pending_repo=smoke_ctx["pending_repo"],
        logger=smoke_ctx["logger"],
    )


@then(parsers.parse('cycle result status should be "{status}"'))
def then_cycle_status(smoke_ctx: dict, status: str):
    assert smoke_ctx["result"]["status"] == status


@then(parsers.parse('cycle reason should be "{reason}"'))
def then_cycle_reason(smoke_ctx: dict, reason: str):
    assert smoke_ctx["result"]["reason"] == reason


@then(parsers.parse("line push count should be {count:d}"))
def then_line_push_count(smoke_ctx: dict, count: int):
    assert len(smoke_ctx["line_client"].sent) == count


@then(parsers.parse("reconcile count should be {count:d}"))
def then_reconcile_count(smoke_ctx: dict, count: int):
    assert smoke_ctx["reconcile_result"]["reconciled"] == count
