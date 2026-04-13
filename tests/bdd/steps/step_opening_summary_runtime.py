from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from pytest_bdd import given, parsers, then, when

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
from stock_monitor.application.runtime_service import run_minute_cycle


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
        _ = now_epoch
        return dict(self.snapshot)

    def get_realtime_quotes(self, stock_nos: list[str]):
        return {stock_no: row for stock_no, row in self.quotes.items() if stock_no in stock_nos}


@pytest.fixture
def opening_ctx(tmp_path: Path):
    conn = connect_sqlite(":memory:")
    apply_schema(conn)
    ctx = {
        "conn": conn,
        "now_dt": datetime(2026, 4, 14, 9, 0, 0, tzinfo=ZoneInfo("Asia/Taipei")),
        "watchlist_repo": SqliteWatchlistRepository(conn),
        "message_repo": SqliteMessageRepository(conn),
        "pending_repo": SqlitePendingRepository(conn),
        "snapshot_repo": SqliteValuationSnapshotRepository(conn),
        "logger": SqliteLogger(conn),
        "pending_fallback": JsonlPendingFallback(tmp_path / "pending_delivery.jsonl"),
        "line_client": _FakeLineClient(sent=[]),
        "market_quotes": {},
        "last_result": None,
    }
    try:
        yield ctx
    finally:
        conn.close()


@given(parsers.parse('trading day opening minute at "{dt_text}"'))
def given_opening_minute(opening_ctx: dict, dt_text: str):
    opening_ctx["now_dt"] = datetime.strptime(dt_text, "%Y-%m-%d %H:%M").replace(tzinfo=ZoneInfo("Asia/Taipei"))


@given(parsers.parse('watchlist has stocks "{stock_csv}"'))
def given_watchlist(opening_ctx: dict, stock_csv: str):
    defaults = {
        "2330": (2000.0, 1500.0),
        "2348": (72.0, 68.0),
        "3293": (700.0, 680.0),
    }
    for stock_no in [item.strip() for item in stock_csv.split(",") if item.strip()]:
        fair, cheap = defaults.get(stock_no, (1500.0, 1000.0))
        opening_ctx["watchlist_repo"].upsert_manual_threshold(stock_no, fair=fair, cheap=cheap, enabled=1)


@given("market quotes do not hit manual thresholds")
def given_non_hit_quotes(opening_ctx: dict):
    now_epoch = int(opening_ctx["now_dt"].timestamp())
    opening_ctx["market_quotes"] = {
        "2330": {"name": "台積電", "price": 2100.0, "tick_at": now_epoch},
        "2348": {"name": "海悅", "price": 80.0, "tick_at": now_epoch},
        "3293": {"name": "鈊象", "price": 750.0, "tick_at": now_epoch},
    }


@when("execute one minute monitoring cycle with valuation snapshots enabled")
def when_execute_cycle(opening_ctx: dict):
    now_epoch = int(opening_ctx["now_dt"].timestamp())
    market_provider = _FakeMarketProvider(
        snapshot={"index_tick_at": now_epoch},
        quotes=opening_ctx["market_quotes"],
    )
    opening_ctx["last_result"] = run_minute_cycle(
        now_dt=opening_ctx["now_dt"],
        market_data_provider=market_provider,
        line_client=opening_ctx["line_client"],
        watchlist_repo=opening_ctx["watchlist_repo"],
        message_repo=opening_ctx["message_repo"],
        pending_repo=opening_ctx["pending_repo"],
        valuation_snapshot_repo=opening_ctx["snapshot_repo"],
        pending_fallback=opening_ctx["pending_fallback"],
        logger=opening_ctx["logger"],
        cooldown_seconds=300,
        retry_count=3,
        stale_threshold_sec=90,
        timezone_name="Asia/Taipei",
    )


@then("line client should receive one opening summary message")
def then_one_opening_summary(opening_ctx: dict):
    assert len(opening_ctx["line_client"].sent) == 1, (
        "[RED-FR-13] Opening summary should be sent once at the first trading minute, "
        "even when there are no threshold-hit signals."
    )


@then("opening summary should include stock list and method list")
def then_summary_payload(opening_ctx: dict):
    payload = opening_ctx["line_client"].sent[-1]
    assert "台積電(2330) 手動" in payload and "海悅(2348) 手動" in payload and "鈊象(3293) 手動" in payload
    assert "艾蜜" in payload and "/" in payload
