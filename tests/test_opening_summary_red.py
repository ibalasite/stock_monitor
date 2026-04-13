from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

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


def test_red_opening_summary_should_be_sent_once_at_first_trading_minute():
    conn = connect_sqlite(":memory:")
    apply_schema(conn)
    try:
        watchlist_repo = SqliteWatchlistRepository(conn)
        watchlist_repo.upsert_manual_threshold("2330", fair=2000, cheap=1500, enabled=1)
        watchlist_repo.upsert_manual_threshold("2348", fair=72, cheap=68, enabled=1)
        watchlist_repo.upsert_manual_threshold("3293", fair=700, cheap=680, enabled=1)

        now_dt = datetime(2026, 4, 14, 9, 0, 0, tzinfo=ZoneInfo("Asia/Taipei"))
        now_epoch = int(now_dt.timestamp())
        market_provider = _FakeMarketProvider(
            snapshot={"index_tick_at": now_epoch},
            quotes={
                "2330": {"name": "台積電", "price": 2100.0, "tick_at": now_epoch},
                "2348": {"name": "海悅", "price": 80.0, "tick_at": now_epoch},
                "3293": {"name": "鈊象", "price": 750.0, "tick_at": now_epoch},
            },
        )
        line_client = _FakeLineClient(sent=[])

        _ = run_minute_cycle(
            now_dt=now_dt,
            market_data_provider=market_provider,
            line_client=line_client,
            watchlist_repo=watchlist_repo,
            message_repo=SqliteMessageRepository(conn),
            pending_repo=SqlitePendingRepository(conn),
            valuation_snapshot_repo=SqliteValuationSnapshotRepository(conn),
            pending_fallback=JsonlPendingFallback(__import__("pathlib").Path("logs/pending_delivery.jsonl")),
            logger=SqliteLogger(conn),
            cooldown_seconds=300,
            retry_count=3,
            stale_threshold_sec=90,
            timezone_name="Asia/Taipei",
        )

        assert len(line_client.sent) == 1, (
            "[RED-FR-13] Opening summary must be sent once at first trading minute "
            "even if no threshold-hit signal exists."
        )
    finally:
        conn.close()

