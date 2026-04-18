from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import runpy
import sys

import pytest

from stock_monitor.application.runtime_service import (
    _already_sent_opening_summary,
    _build_opening_method_pairs,
    _build_opening_summary_message,
    _format_compact_price,
    _format_price,
    _send_opening_summary_if_needed,
    _to_epoch_seconds,
    build_minute_rows,
    evaluate_manual_threshold_hits,
    evaluate_valuation_snapshot_hits,
    run_minute_cycle,
    run_reconcile_cycle,
)
from stock_monitor.application.valuation_calculator import ManualValuationCalculator
from stock_monitor.app import _build_runtime, _resolve_timezone, _run_daemon_loop, main
from stock_monitor.application.valuation_calculator import ManualValuationCalculator as _ManualValuationCalculator


@dataclass
class _FakeLogger:
    events: list[tuple[str, str]]
    _summary_sent_dates: list[str] = field(default_factory=list)

    def log(self, level: str, message: str):
        self.events.append((level, message))

    def opening_summary_sent_for_date(self, trade_date: str) -> bool:
        return trade_date in self._summary_sent_dates

    def mark_opening_summary_sent(self, trade_date: str) -> None:
        if trade_date not in self._summary_sent_dates:
            self._summary_sent_dates.append(trade_date)


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
    fail: bool = False

    def get_market_snapshot(self, now_epoch: int):
        if self.fail:
            raise TimeoutError("market timeout")
        return dict(self.snapshot)

    def get_realtime_quotes(self, stock_nos: list[str]) -> dict[str, dict]:
        return {key: value for key, value in self.quotes.items() if key in stock_nos}


@dataclass
class _FakeWatchlistRepo:
    rows: list[dict]

    def list_enabled(self) -> list[dict]:
        return list(self.rows)


@dataclass
class _FakeMessageRepo:
    last_sent_map: dict[tuple[str, int], int | None]
    rows: list[dict]

    def get_last_sent_at(self, stock_no: str, stock_status: int) -> int | None:
        return self.last_sent_map.get((stock_no, stock_status))

    def save_batch(self, rows: list[dict]) -> None:
        self.rows.extend(rows)


@dataclass
class _FakePendingRepo:
    items: list[dict]

    def enqueue(self, item: dict):
        self.items.append(item)

    def list_pending(self):
        return list(self.items)

    def mark_reconciled(self, pending_id: str):
        for item in self.items:
            if item.get("pending_id") == pending_id:
                item["status"] = "RECONCILED"

    def get_last_pending_sent_at(self, stock_no: str, stock_status: int) -> int | None:
        latest: int | None = None
        for item in self.items:
            if item.get("status") != "PENDING":
                continue
            for row in item.get("rows", []):
                try:
                    row_stock = str(row.get("stock_no"))
                    row_status = int(row.get("stock_status"))
                    row_ts = int(row.get("update_time"))
                except (TypeError, ValueError):
                    continue
                if row_stock == str(stock_no) and row_status == int(stock_status):
                    latest = row_ts if latest is None else max(latest, row_ts)
        return latest


@dataclass
class _FakeFallback:
    rows: list[dict]

    def append(self, item: dict):
        self.rows.append(item)

    def get_last_pending_sent_at(self, stock_no: str, stock_status: int) -> int | None:
        latest: int | None = None
        for item in self.rows:
            for row in item.get("rows", []):
                try:
                    row_stock = str(row.get("stock_no"))
                    row_status = int(row.get("stock_status"))
                    row_ts = int(row.get("update_time"))
                except (TypeError, ValueError):
                    continue
                if row_stock == str(stock_no) and row_status == int(stock_status):
                    latest = row_ts if latest is None else max(latest, row_ts)
        return latest


def test_runtime_helpers_for_epoch_hits_and_rows():
    naive = datetime(2026, 4, 10, 10, 0, 0)
    aware = datetime(2026, 4, 10, 2, 0, 0, tzinfo=timezone.utc)
    assert _to_epoch_seconds(naive) == int(naive.timestamp())
    assert _to_epoch_seconds(aware) == int(aware.timestamp())

    watchlist = [{"stock_no": "2330", "manual_fair_price": 1500, "manual_cheap_price": 1000}]
    quotes = {"2330": {"price": 999, "tick_at": 1712710000}}
    hits = evaluate_manual_threshold_hits(watchlist_rows=watchlist, quotes=quotes)
    assert hits[0]["stock_status"] == 2

    no_hits = evaluate_manual_threshold_hits(
        watchlist_rows=watchlist, quotes={"2330": {"price": 1600, "tick_at": 1712710000}}
    )
    assert no_hits == []
    assert evaluate_manual_threshold_hits(watchlist_rows=watchlist, quotes={}) == []

    valuation_hits = evaluate_valuation_snapshot_hits(
        snapshot_rows=[
            {
                "stock_no": "2330",
                "method_name": "emily_composite",
                "method_version": "v1",
                "fair_price": 1800,
                "cheap_price": 1500,
            }
        ],
        quotes={"2330": {"price": 1499, "tick_at": 1712710000}},
    )
    assert len(valuation_hits) == 1
    assert valuation_hits[0]["stock_status"] == 2
    assert valuation_hits[0]["method"] == "emily_composite_v1"

    rows = build_minute_rows(
        now_dt=datetime(2026, 4, 10, 10, 21, 0),
        hits=hits,
        message_repo=_FakeMessageRepo(last_sent_map={}, rows=[]),
        pending_repo=_FakePendingRepo(items=[]),
        pending_fallback=_FakeFallback(rows=[]),
        cooldown_seconds=300,
    )
    assert len(rows) == 1
    assert rows[0]["stock_no"] == "2330"
    assert "低於便宜價1000" in rows[0]["message"]

    blocked_rows = build_minute_rows(
        now_dt=datetime(2026, 4, 10, 10, 21, 0),
        hits=[{"stock_no": "2330", "stock_status": 1, "method": "manual_rule", "price": 1490.0}],
        message_repo=_FakeMessageRepo(last_sent_map={("2330", 1): int(datetime(2026, 4, 10, 10, 20, 30).timestamp())}, rows=[]),
        pending_repo=_FakePendingRepo(items=[]),
        pending_fallback=_FakeFallback(rows=[]),
        cooldown_seconds=300,
    )
    assert blocked_rows == []

    class _MsgRepoNoPending:
        def get_last_sent_at(self, stock_no: str, stock_status: int):
            return None

    class _NoPendingLookup:
        pass

    class _NoFallbackLookup:
        pass

    rows_without_pending_lookup = build_minute_rows(
        now_dt=datetime(2026, 4, 10, 10, 21, 0),
        hits=[{"stock_no": "2330", "stock_status": 1, "method": "manual_rule", "price": 1490.0}],
        message_repo=_MsgRepoNoPending(),
        pending_repo=_NoPendingLookup(),
        pending_fallback=_NoFallbackLookup(),
        cooldown_seconds=300,
    )
    assert len(rows_without_pending_lookup) == 1

    # Force aggregate_stock_signals returning [] to hit defensive branch.
    class _MsgRepo:
        def get_last_sent_at(self, stock_no: str, stock_status: int):
            return None

    import stock_monitor.application.runtime_service as runtime_service

    original = runtime_service.aggregate_stock_signals
    runtime_service.aggregate_stock_signals = lambda stock_no, stock_hits: []
    try:
        assert (
            build_minute_rows(
                now_dt=datetime(2026, 4, 10, 10, 21, 0),
                hits=[{"stock_no": "2330", "stock_status": 1, "method": "manual_rule", "price": 1490.0}],
                message_repo=_MsgRepo(),
                pending_repo=_NoPendingLookup(),
                pending_fallback=_NoFallbackLookup(),
                cooldown_seconds=300,
            )
            == []
        )
    finally:
        runtime_service.aggregate_stock_signals = original


def test_run_minute_cycle_branches_and_reconcile():
    logger = _FakeLogger(events=[])
    line_client = _FakeLineClient(sent=[])
    message_repo = _FakeMessageRepo(last_sent_map={}, rows=[])
    pending_repo = _FakePendingRepo(items=[])
    fallback = _FakeFallback(rows=[])

    result_non_trading = run_minute_cycle(
        now_dt=datetime(2026, 4, 10, 13, 31, 0),
        market_data_provider=_FakeMarketProvider(snapshot={}, quotes={}),
        line_client=line_client,
        watchlist_repo=_FakeWatchlistRepo(rows=[]),
        message_repo=message_repo,
        pending_repo=pending_repo,
        pending_fallback=fallback,
        logger=logger,
    )
    assert result_non_trading["reason"] == "non_trading_session"

    result_fetch_failed = run_minute_cycle(
        now_dt=datetime(2026, 4, 10, 10, 0, 0),
        market_data_provider=_FakeMarketProvider(snapshot={}, quotes={}, fail=True),
        line_client=line_client,
        watchlist_repo=_FakeWatchlistRepo(rows=[]),
        message_repo=message_repo,
        pending_repo=pending_repo,
        pending_fallback=fallback,
        logger=logger,
    )
    assert result_fetch_failed["reason"] == "market_fetch_failed"

    stale_index = int(datetime(2026, 4, 9, 10, 0, 0).timestamp())
    result_market_closed = run_minute_cycle(
        now_dt=datetime(2026, 4, 10, 10, 0, 0),
        market_data_provider=_FakeMarketProvider(snapshot={"index_tick_at": stale_index}, quotes={}),
        line_client=line_client,
        watchlist_repo=_FakeWatchlistRepo(rows=[{"stock_no": "2330", "manual_fair_price": 1500, "manual_cheap_price": 1000}]),
        message_repo=message_repo,
        pending_repo=pending_repo,
        pending_fallback=fallback,
        logger=logger,
    )
    assert result_market_closed["status"] == "skipped"

    now_epoch = int(datetime(2026, 4, 10, 10, 0, 0).timestamp())
    result_empty = run_minute_cycle(
        now_dt=datetime(2026, 4, 10, 10, 0, 0),
        market_data_provider=_FakeMarketProvider(snapshot={"index_tick_at": now_epoch}, quotes={}),
        line_client=line_client,
        watchlist_repo=_FakeWatchlistRepo(rows=[]),
        message_repo=message_repo,
        pending_repo=pending_repo,
        pending_fallback=fallback,
        logger=logger,
    )
    assert result_empty["reason"] == "empty_watchlist"

    result_no_signal = run_minute_cycle(
        now_dt=datetime(2026, 4, 10, 10, 0, 0),
        market_data_provider=_FakeMarketProvider(
            snapshot={"index_tick_at": now_epoch},
            quotes={"2330": {"price": 1900.0, "tick_at": now_epoch}},
        ),
        line_client=line_client,
        watchlist_repo=_FakeWatchlistRepo(rows=[{"stock_no": "2330", "manual_fair_price": 1500, "manual_cheap_price": 1000}]),
        message_repo=message_repo,
        pending_repo=pending_repo,
        pending_fallback=fallback,
        logger=logger,
    )
    assert result_no_signal["status"] == "no_signal"

    result_sent = run_minute_cycle(
        now_dt=datetime(2026, 4, 10, 10, 0, 0),
        market_data_provider=_FakeMarketProvider(
            snapshot={"index_tick_at": now_epoch},
            quotes={"2330": {"price": 999.0, "tick_at": now_epoch}},
        ),
        line_client=line_client,
        watchlist_repo=_FakeWatchlistRepo(rows=[{"stock_no": "2330", "manual_fair_price": 1500, "manual_cheap_price": 1000}]),
        message_repo=message_repo,
        pending_repo=pending_repo,
        pending_fallback=fallback,
        logger=logger,
    )
    assert result_sent["count"] == 1
    # Opening summary triggered on first watchlist run (result_no_signal), plus trigger here.
    assert len(line_client.sent) == 2
    assert "低於便宜價1000" in line_client.sent[-1]

    result_stale_quote = run_minute_cycle(
        now_dt=datetime(2026, 4, 10, 10, 0, 0),
        market_data_provider=_FakeMarketProvider(
            snapshot={"index_tick_at": now_epoch},
            quotes={"2330": {"price": 999.0, "tick_at": now_epoch - 200}},
        ),
        line_client=line_client,
        watchlist_repo=_FakeWatchlistRepo(rows=[{"stock_no": "2330", "manual_fair_price": 1500, "manual_cheap_price": 1000}]),
        message_repo=message_repo,
        pending_repo=pending_repo,
        pending_fallback=fallback,
        logger=logger,
        stale_threshold_sec=90,
    )
    assert result_stale_quote["status"] == "no_signal"
    assert any("STALE_QUOTE:2330" in msg for _, msg in logger.events)

    result_conflict_quote = run_minute_cycle(
        now_dt=datetime(2026, 4, 10, 10, 0, 0),
        market_data_provider=_FakeMarketProvider(
            snapshot={"index_tick_at": now_epoch},
            quotes={"2330": {"price": 999.0, "tick_at": now_epoch, "conflict": True}},
        ),
        line_client=line_client,
        watchlist_repo=_FakeWatchlistRepo(rows=[{"stock_no": "2330", "manual_fair_price": 1500, "manual_cheap_price": 1000}]),
        message_repo=message_repo,
        pending_repo=pending_repo,
        pending_fallback=fallback,
        logger=logger,
        stale_threshold_sec=90,
    )
    assert result_conflict_quote["status"] == "no_signal"
    assert any("DATA_CONFLICT:2330" in msg for _, msg in logger.events)

    result_invalid_tick = run_minute_cycle(
        now_dt=datetime(2026, 4, 10, 10, 0, 0),
        market_data_provider=_FakeMarketProvider(
            snapshot={"index_tick_at": now_epoch},
            quotes={"2330": {"price": 999.0, "tick_at": "bad-int"}},
        ),
        line_client=line_client,
        watchlist_repo=_FakeWatchlistRepo(rows=[{"stock_no": "2330", "manual_fair_price": 1500, "manual_cheap_price": 1000}]),
        message_repo=message_repo,
        pending_repo=pending_repo,
        pending_fallback=fallback,
        logger=logger,
        stale_threshold_sec=90,
    )
    assert result_invalid_tick["status"] == "no_signal"

    recent_pending = int(datetime(2026, 4, 10, 9, 59, 30).timestamp())
    pending_repo.items = [
        {
            "pending_id": "P-COOL",
            "status": "PENDING",
            "rows": [
                {
                    "stock_no": "2330",
                    "stock_status": 1,
                    "update_time": recent_pending,
                }
            ],
        }
    ]
    result_pending_cooldown = run_minute_cycle(
        now_dt=datetime(2026, 4, 10, 10, 0, 0),
        market_data_provider=_FakeMarketProvider(
            snapshot={"index_tick_at": now_epoch},
            quotes={"2330": {"price": 1490.0, "tick_at": now_epoch}},
        ),
        line_client=line_client,
        watchlist_repo=_FakeWatchlistRepo(rows=[{"stock_no": "2330", "manual_fair_price": 1500, "manual_cheap_price": 1000}]),
        message_repo=_FakeMessageRepo(last_sent_map={}, rows=[]),
        pending_repo=pending_repo,
        pending_fallback=fallback,
        logger=logger,
        cooldown_seconds=300,
    )
    assert result_pending_cooldown["status"] == "no_signal"

    pending_repo.items = [
        {
            "pending_id": "1",
            "payload": "pending payload",
            "rows": [
                {
                    "stock_no": "2330",
                    "message": "from pending",
                    "stock_status": 2,
                    "methods_hit": ["manual_rule"],
                    "minute_bucket": "2026-04-10 10:00",
                    "update_time": now_epoch,
                }
            ],
            "status": "PENDING",
        }
    ]
    reconcile_result = run_reconcile_cycle(
        message_repo=message_repo,
        pending_repo=pending_repo,
        logger=logger,
    )
    assert reconcile_result["reconciled"] == 1


def test_run_minute_cycle_includes_manual_plus_three_valuation_methods():
    @dataclass
    class _FakeValuationSnapshotRepo:
        rows: list[dict]

        def list_latest_snapshots(self, stock_nos: list[str], as_of_date: str) -> list[dict]:
            _ = (stock_nos, as_of_date)
            return list(self.rows)

    logger = _FakeLogger(events=[])
    line_client = _FakeLineClient(sent=[])
    message_repo = _FakeMessageRepo(last_sent_map={}, rows=[])
    pending_repo = _FakePendingRepo(items=[])
    fallback = _FakeFallback(rows=[])

    now_dt = datetime(2026, 4, 10, 10, 0, 0)
    now_epoch = int(now_dt.timestamp())

    result = run_minute_cycle(
        now_dt=now_dt,
        market_data_provider=_FakeMarketProvider(
            snapshot={"index_tick_at": now_epoch},
            quotes={"2330": {"name": "台積電", "price": 900.0, "tick_at": now_epoch}},
        ),
        line_client=line_client,
        watchlist_repo=_FakeWatchlistRepo(rows=[{"stock_no": "2330", "manual_fair_price": 1500, "manual_cheap_price": 1000}]),
        message_repo=message_repo,
        pending_repo=pending_repo,
        valuation_snapshot_repo=_FakeValuationSnapshotRepo(
            rows=[
                {
                    "stock_no": "2330",
                    "trade_date": "2026-04-10",
                    "method_name": "emily_composite",
                    "method_version": "v1",
                    "fair_price": 1200,
                    "cheap_price": 1000,
                },
                {
                    "stock_no": "2330",
                    "trade_date": "2026-04-10",
                    "method_name": "oldbull_dividend_yield",
                    "method_version": "v1",
                    "fair_price": 1100,
                    "cheap_price": 950,
                },
                {
                    "stock_no": "2330",
                    "trade_date": "2026-04-10",
                    "method_name": "raysky_blended_margin",
                    "method_version": "v1",
                    "fair_price": 1000,
                    "cheap_price": 900,
                },
            ]
        ),
        pending_fallback=fallback,
        logger=logger,
        cooldown_seconds=300,
        retry_count=3,
        stale_threshold_sec=90,
    )

    assert result["status"] == "persisted"
    assert len(message_repo.rows) == 1
    methods = set(message_repo.rows[0]["methods_hit"])
    assert methods == {
        "manual_rule",
        "emily_composite_v1",
        "oldbull_dividend_yield_v1",
        "raysky_blended_margin_v1",
    }


def test_opening_summary_helpers_cover_dedupe_na_and_exception_paths():
    pairs = _build_opening_method_pairs(
        [
            {"method_name": "  ", "method_version": "v1"},
            {"method_name": "custom_method", "method_version": "v2"},
            {"method_name": "custom_method", "method_version": "v2"},
            {"method_name": "emily_composite", "method_version": "v1"},
        ]
    )
    assert pairs.count(("custom_method", "v2")) == 1
    assert pairs.count(("emily_composite", "v1")) == 1

    message = _build_opening_summary_message(
        trade_date="2026-04-14",
        watchlist_rows=[{"stock_no": "2330", "manual_fair_price": 2000.0, "manual_cheap_price": 1500.0}],
        method_pairs=[("custom_method", "v2"), ("missing_method", "v1")],
        snapshot_rows=[
            {
                "stock_no": "2330",
                "method_name": "custom_method",
                "method_version": "v2",
                "fair_price": 1800.0,
                "cheap_price": 1600.0,
            }
        ],
        stock_name_map={"2330": "台積電"},
    )
    assert "台積電(2330) 手動 2000/1500" in message
    assert "台積電(2330) custom_method_v2 1800/1600" in message
    assert "台積電(2330) missing_method_v1 N/A/N/A" in message

    class _NoSummaryStateLogger:
        def log(self, level: str, message: str):
            _ = (level, message)

    class _RaisesSummaryStateLogger:
        def log(self, level: str, message: str):
            _ = (level, message)

        def opening_summary_sent_for_date(self, trade_date: str) -> bool:
            _ = trade_date
            raise RuntimeError("state broken")

    assert _already_sent_opening_summary(_NoSummaryStateLogger(), "2026-04-14") is False
    assert _already_sent_opening_summary(_RaisesSummaryStateLogger(), "2026-04-14") is False
    assert _format_compact_price("N/A") == "N/A"

    empty_message = _build_opening_summary_message(
        trade_date="2026-04-14",
        watchlist_rows=[],
        method_pairs=[],
        snapshot_rows=[],
        stock_name_map={},
    )
    assert empty_message == "無監控資料"


def test_send_opening_summary_branch_coverage():
    @dataclass
    class _SummaryLogger:
        already_sent: bool
        events: list[tuple[str, str]]

        def log(self, level: str, message: str):
            self.events.append((level, message))

        def opening_summary_sent_for_date(self, trade_date: str) -> bool:
            _ = trade_date
            return self.already_sent

    @dataclass
    class _BrokenSnapshotRepo:
        def list_latest_snapshots(self, stock_nos: list[str], as_of_date: str):
            _ = (stock_nos, as_of_date)
            raise RuntimeError("snapshot broken")

    @dataclass
    class _FailLineClient:
        def send(self, message: str):
            _ = message
            raise RuntimeError("line send broken")

    watchlist_rows = [{"stock_no": "2330", "manual_fair_price": 2000.0, "manual_cheap_price": 1500.0}]

    line_client = _FakeLineClient(sent=[])
    logger = _SummaryLogger(already_sent=True, events=[])
    _send_opening_summary_if_needed(
        now_dt=datetime(2026, 4, 14, 8, 59, 0),
        watchlist_rows=watchlist_rows,
        valuation_snapshot_repo=None,
        line_client=line_client,
        logger=logger,
    )
    assert line_client.sent == []

    line_client = _FakeLineClient(sent=[])
    logger = _SummaryLogger(already_sent=True, events=[])
    _send_opening_summary_if_needed(
        now_dt=datetime(2026, 4, 14, 9, 0, 0),
        watchlist_rows=watchlist_rows,
        valuation_snapshot_repo=None,
        line_client=line_client,
        logger=logger,
    )
    assert line_client.sent == []

    line_client = _FakeLineClient(sent=[])
    logger = _SummaryLogger(already_sent=False, events=[])
    _send_opening_summary_if_needed(
        now_dt=datetime(2026, 4, 14, 9, 0, 0),
        watchlist_rows=watchlist_rows,
        valuation_snapshot_repo=None,
        line_client=line_client,
        logger=logger,
    )
    assert len(line_client.sent) == 1
    assert any("OPENING_SUMMARY_SENT:date=2026-04-14" in msg for _, msg in logger.events)

    logger = _SummaryLogger(already_sent=False, events=[])
    _send_opening_summary_if_needed(
        now_dt=datetime(2026, 4, 14, 9, 0, 0),
        watchlist_rows=watchlist_rows,
        valuation_snapshot_repo=_BrokenSnapshotRepo(),
        line_client=_FailLineClient(),
        logger=logger,
    )
    assert any("OPENING_SUMMARY_SNAPSHOT_FETCH_FAILED" in msg for _, msg in logger.events)
    assert any("OPENING_SUMMARY_SEND_FAILED" in msg for _, msg in logger.events)

    # Cover except-pass in mark_opening_summary_sent call
    @dataclass
    class _BrokenMarkLogger:
        already_sent: bool
        events: list[tuple[str, str]]

        def log(self, level: str, message: str):
            self.events.append((level, message))

        def opening_summary_sent_for_date(self, trade_date: str) -> bool:
            return self.already_sent

        def mark_opening_summary_sent(self, trade_date: str) -> None:
            raise RuntimeError("mark failed")

    line_client_mark = _FakeLineClient(sent=[])
    logger_mark = _BrokenMarkLogger(already_sent=False, events=[])
    _send_opening_summary_if_needed(
        now_dt=datetime(2026, 4, 14, 9, 0, 0),
        watchlist_rows=watchlist_rows,
        valuation_snapshot_repo=None,
        line_client=line_client_mark,
        logger=logger_mark,
    )
    assert len(line_client_mark.sent) == 1
    assert any("OPENING_SUMMARY_SENT" in msg for _, msg in logger_mark.events)


def test_evaluate_valuation_snapshot_hits_remaining_paths():
    hits = evaluate_valuation_snapshot_hits(
        snapshot_rows=[
            {
                "stock_no": "9999",
                "method_name": "emily_composite",
                "method_version": "v1",
                "fair_price": 120.0,
                "cheap_price": 100.0,
            },
            {
                "stock_no": "2330",
                "method_name": "oldbull_dividend_yield",
                "method_version": "v1",
                "fair_price": 200.0,
                "cheap_price": 100.0,
            },
            {
                "stock_no": "2348",
                "method_name": "raysky_blended_margin",
                "method_version": "v1",
                "fair_price": 80.0,
                "cheap_price": 70.0,
            },
            {
                "stock_no": "3293",
                "method_name": "custom_method",
                "method_version": "",
                "fair_price": 110.0,
                "cheap_price": 95.0,
            },
        ],
        quotes={
            "2330": {"price": 150.0, "tick_at": 1712710000},
            "2348": {"price": 100.0, "tick_at": 1712710000},
            "3293": {"price": 90.0, "tick_at": 1712710000},
        },
    )

    assert len(hits) == 2
    hit_2330 = [row for row in hits if row["stock_no"] == "2330"][0]
    hit_3293 = [row for row in hits if row["stock_no"] == "3293"][0]
    assert hit_2330["stock_status"] == 1
    assert hit_3293["stock_status"] == 2
    assert hit_3293["method"] == "custom_method"


def test_run_minute_cycle_logs_warning_when_valuation_snapshot_lookup_fails():
    @dataclass
    class _BrokenValuationSnapshotRepo:
        def list_latest_snapshots(self, stock_nos: list[str], as_of_date: str):
            _ = (stock_nos, as_of_date)
            raise RuntimeError("snapshot query failed")

    now_dt = datetime(2026, 4, 10, 10, 0, 0)
    now_epoch = int(now_dt.timestamp())
    logger = _FakeLogger(events=[])
    line_client = _FakeLineClient(sent=[])
    message_repo = _FakeMessageRepo(last_sent_map={}, rows=[])
    pending_repo = _FakePendingRepo(items=[])
    fallback = _FakeFallback(rows=[])

    result = run_minute_cycle(
        now_dt=now_dt,
        market_data_provider=_FakeMarketProvider(
            snapshot={"index_tick_at": now_epoch},
            quotes={"2330": {"name": "台積電", "price": 1499.0, "tick_at": now_epoch}},
        ),
        line_client=line_client,
        watchlist_repo=_FakeWatchlistRepo(rows=[{"stock_no": "2330", "manual_fair_price": 1500, "manual_cheap_price": 1000}]),
        message_repo=message_repo,
        pending_repo=pending_repo,
        valuation_snapshot_repo=_BrokenValuationSnapshotRepo(),
        pending_fallback=fallback,
        logger=logger,
        cooldown_seconds=300,
        retry_count=3,
        stale_threshold_sec=90,
    )
    assert result["status"] == "persisted"
    assert any("VALUATION_SNAPSHOT_FETCH_FAILED" in msg for _, msg in logger.events)


def test_app_main_init_db_run_once_and_reconcile(monkeypatch, tmp_path: Path, capsys):
    db_path = tmp_path / "runtime.db"

    exit_code = main(["--db-path", str(db_path), "init-db"])
    assert exit_code == 0
    init_output = json.loads(capsys.readouterr().out.strip())
    assert init_output["command"] == "init-db"

    class _FakeConn:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    fake_conn = _FakeConn()
    runtime = {
        "conn": fake_conn,
        "market_provider": object(),
        "line_client": object(),
        "watchlist_repo": object(),
        "message_repo": object(),
        "pending_repo": object(),
        "valuation_snapshot_repo": object(),
        "logger": _FakeLogger(events=[]),
        "pending_fallback": object(),
    }

    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "validtoken_12345")
    monkeypatch.setenv("LINE_TO_GROUP_ID", "C1234567890")
    monkeypatch.setattr("stock_monitor.app._build_runtime", lambda args: runtime)
    monkeypatch.setattr("stock_monitor.app.run_minute_cycle", lambda **kwargs: {"status": "persisted", "count": 1})

    run_code = main(["--db-path", str(db_path), "run-once"])
    assert run_code == 0
    run_output = json.loads(capsys.readouterr().out.strip())
    assert run_output["status"] == "persisted"
    assert fake_conn.closed is True

    fake_conn2 = _FakeConn()
    runtime2 = dict(runtime)
    runtime2["conn"] = fake_conn2
    monkeypatch.setattr("stock_monitor.app._build_runtime", lambda args: runtime2)
    monkeypatch.setattr("stock_monitor.app.run_reconcile_cycle", lambda **kwargs: {"reconciled": 2})

    reconcile_code = main(["--db-path", str(db_path), "reconcile-once"])
    assert reconcile_code == 0
    reconcile_output = json.loads(capsys.readouterr().out.strip())
    assert reconcile_output["reconciled"] == 2
    assert fake_conn2.closed is True

    fake_conn3 = _FakeConn()
    runtime3 = dict(runtime)
    runtime3["conn"] = fake_conn3
    monkeypatch.setattr("stock_monitor.app._build_runtime", lambda args: runtime3)
    monkeypatch.setattr("stock_monitor.app.run_daily_valuation_job", lambda **kwargs: {"status": "executed", "count": 1})
    valuation_code = main(["--db-path", str(db_path), "valuation-once"])
    assert valuation_code == 0
    valuation_output = json.loads(capsys.readouterr().out.strip())
    assert valuation_output["status"] == "executed"
    assert fake_conn3.closed is True

    fake_conn4 = _FakeConn()
    runtime4 = dict(runtime)
    runtime4["conn"] = fake_conn4
    monkeypatch.setattr("stock_monitor.app._build_runtime", lambda args: runtime4)
    monkeypatch.setattr(
        "stock_monitor.app._run_daemon_loop",
        lambda **kwargs: {"status": "stopped", "loops": 1, "minute_cycles": 0, "valuation_runs": 0, "reconcile_runs": 1},
    )

    daemon_code = main(["--db-path", str(db_path), "run-daemon", "--max-loops", "1"])
    assert daemon_code == 0
    daemon_output = json.loads(capsys.readouterr().out.strip())
    assert daemon_output["status"] == "stopped"
    assert daemon_output["loops"] == 1
    assert fake_conn4.closed is True


def test_app_main_scan_market(monkeypatch, tmp_path: Path, capsys):
    """TP-SCAN: CLI scan-market subcommand routes correctly and outputs JSON summary."""
    from stock_monitor.application.market_scan import MarketScanResult
    from stock_monitor.adapters.sqlite_repo import connect_sqlite, apply_schema

    db_path = tmp_path / "scan_test.db"
    output_dir = tmp_path / "scan_output"

    conn = connect_sqlite(str(db_path))
    apply_schema(conn)
    conn.execute(
        "INSERT INTO valuation_methods(method_name, method_version, enabled, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        ("emily_composite", "v1", 1, 1713000000, 1713000000),
    )
    conn.commit()
    conn.close()

    fake_result = MarketScanResult(
        scan_date="20260418",
        total_stocks=100,
        watchlist_upserted=5,
        watchlist_new=3,
        watchlist_updated=2,
        near_fair_count=10,
        uncalculable_count=85,
        above_fair_count=0,
        output_dir=str(output_dir),
    )
    monkeypatch.setattr(
        "stock_monitor.app.run_market_scan_job",
        lambda **kwargs: fake_result,
    )

    exit_code = main(["--db-path", str(db_path), "scan-market", "--output-dir", str(output_dir)])
    assert exit_code == 0
    output = json.loads(capsys.readouterr().out.strip())
    assert output["status"] == "ok"
    assert output["total_stocks"] == 100
    assert output["watchlist_upserted"] == 5
    assert output["near_fair_count"] == 10
    assert output["uncalculable_count"] == 85


def test_app_main_scan_market_injects_enabled_methods(monkeypatch, tmp_path: Path, capsys):
    """TP-SCAN-007: scan-market must inject enabled valuation methods from DB."""
    from stock_monitor.application.market_scan import MarketScanResult
    from stock_monitor.adapters.sqlite_repo import connect_sqlite, apply_schema

    db_path = tmp_path / "scan_methods.db"
    output_dir = tmp_path / "scan_output"

    conn = connect_sqlite(str(db_path))
    apply_schema(conn)
    conn.execute(
        "INSERT INTO valuation_methods(method_name, method_version, enabled, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        ("emily_composite", "v1", 1, 1713000000, 1713000000),
    )
    conn.execute(
        "INSERT INTO valuation_methods(method_name, method_version, enabled, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        ("oldbull_dividend_yield", "v1", 1, 1713000000, 1713000000),
    )
    conn.commit()
    conn.close()

    captured: dict = {}

    def _fake_scan(**kwargs):
        captured.update(kwargs)
        return MarketScanResult(
            scan_date="20260418",
            total_stocks=10,
            watchlist_upserted=1,
            watchlist_new=1,
            watchlist_updated=0,
            near_fair_count=2,
            uncalculable_count=7,
            above_fair_count=0,
            output_dir=str(output_dir),
        )

    monkeypatch.setattr("stock_monitor.app.run_market_scan_job", _fake_scan)

    exit_code = main(["--db-path", str(db_path), "scan-market", "--output-dir", str(output_dir)])
    assert exit_code == 0
    assert len(captured.get("valuation_methods", [])) == 2, (
        "[TP-SCAN-007] scan-market must inject DB enabled valuation methods, not empty list."
    )
    _ = json.loads(capsys.readouterr().out.strip())


def test_app_main_scan_market_fails_when_enabled_methods_empty(tmp_path: Path, capsys):
    """TP-SCAN-007: scan-market must fail-fast when no enabled methods are configured."""
    from stock_monitor.adapters.sqlite_repo import connect_sqlite, apply_schema

    db_path = tmp_path / "scan_methods_empty.db"
    output_dir = tmp_path / "scan_output"

    conn = connect_sqlite(str(db_path))
    apply_schema(conn)
    conn.close()

    exit_code = main(["--db-path", str(db_path), "scan-market", "--output-dir", str(output_dir)])
    assert exit_code != 0, "[TP-SCAN-007] Expected non-zero exit when enabled methods count is zero."
    out = capsys.readouterr().out
    assert "MARKET_SCAN_METHODS_EMPTY" in out, (
        "[TP-SCAN-007] Expected MARKET_SCAN_METHODS_EMPTY fail-fast message."
    )


def test_build_runtime_and_timezone_resolution(monkeypatch, tmp_path: Path):
    class _Args:
        db_path = str(tmp_path / "runtime.db")

    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "validtoken_12345")
    monkeypatch.setenv("LINE_TO_GROUP_ID", "C1234567890")
    runtime = _build_runtime(_Args())
    try:
        assert runtime["watchlist_repo"] is not None
        assert runtime["message_repo"] is not None
        assert runtime["pending_repo"] is not None
    finally:
        runtime["conn"].close()

    assert _resolve_timezone("Asia/Taipei") is not None
    with pytest.raises(ValueError):
        _resolve_timezone("Invalid/Timezone/Name")


def test_manual_valuation_calculator_from_watchlist():
    class _WatchlistRepo:
        def list_enabled(self):
            return [{"stock_no": "2330", "manual_fair_price": 1500, "manual_cheap_price": 1000}]

    calculator = _ManualValuationCalculator(
        watchlist_repo=_WatchlistRepo(),
        trade_date="2026-04-10",
    )
    snapshots = calculator.calculate()
    method_pairs = {(item["method_name"], item["method_version"]) for item in snapshots}
    assert method_pairs == {
        ("emily_composite", "v1"),
        ("oldbull_dividend_yield", "v1"),
        ("raysky_blended_margin", "v1"),
    }
    assert all(item["stock_no"] == "2330" for item in snapshots)
    assert all(item["trade_date"] == "2026-04-10" for item in snapshots)
    assert all(float(item["cheap_price"]) <= float(item["fair_price"]) for item in snapshots)


def test_message_format_for_fair_price_and_price_formatter():
    assert _format_price(1950.0) == "1950"
    assert _format_price(1950.5) == "1950.5"
    assert _format_price(1950.56) == "1950.56"

    rows = build_minute_rows(
        now_dt=datetime(2026, 4, 10, 10, 21, 0),
        hits=[
            {
                "stock_no": "2330",
                "stock_status": 1,
                "method": "manual_rule",
                "price": 1950.0,
                "stock_name": "台積電",
                "fair_price": 2000.0,
                "cheap_price": 1500.0,
            }
        ],
        message_repo=_FakeMessageRepo(last_sent_map={}, rows=[]),
        pending_repo=_FakePendingRepo(items=[]),
        pending_fallback=_FakeFallback(rows=[]),
        cooldown_seconds=300,
    )
    assert len(rows) == 1
    assert rows[0]["message"] == "台積電(2330)目前1950，低於合理價2000"

    rows_status2_no_redundant_fair = build_minute_rows(
        now_dt=datetime(2026, 4, 10, 10, 21, 0),
        hits=[
            {
                "stock_no": "2330",
                "stock_status": 2,
                "method": "manual_rule",
                "price": 1499.0,
                "stock_name": "台積電",
                "fair_price": 1500.0,
                "cheap_price": 1500.0,
            }
        ],
        message_repo=_FakeMessageRepo(last_sent_map={}, rows=[]),
        pending_repo=_FakePendingRepo(items=[]),
        pending_fallback=_FakeFallback(rows=[]),
        cooldown_seconds=300,
    )
    assert "低於便宜價1500" in rows_status2_no_redundant_fair[0]["message"]
    assert "合理價" not in rows_status2_no_redundant_fair[0]["message"]


def test_run_daemon_loop_trading_poll_and_valuation(monkeypatch):
    calls = {
        "minute": 0,
        "valuation": 0,
        "reconcile": 0,
    }

    def _fake_run_minute_cycle(**kwargs):
        _ = kwargs
        calls["minute"] += 1
        return {"status": "persisted", "count": 1}

    def _fake_run_daily_valuation_job(**kwargs):
        _ = kwargs
        calls["valuation"] += 1
        return {"status": "executed", "count": 1}

    def _fake_run_reconcile_cycle(**kwargs):
        _ = kwargs
        calls["reconcile"] += 1
        return {"reconciled": 0}

    monkeypatch.setattr("stock_monitor.application.daemon_runner.run_minute_cycle", _fake_run_minute_cycle)
    monkeypatch.setattr("stock_monitor.application.daemon_runner.run_daily_valuation_job", _fake_run_daily_valuation_job)
    monkeypatch.setattr("stock_monitor.application.daemon_runner.run_reconcile_cycle", _fake_run_reconcile_cycle)

    schedule = iter(
        [
            datetime(2026, 4, 10, 9, 0, 1),   # trading minute 09:00
            datetime(2026, 4, 10, 9, 0, 40),  # same minute, should not repoll
            datetime(2026, 4, 10, 9, 1, 1),   # next minute, should poll
            datetime(2026, 4, 10, 14, 0, 0),  # valuation time, once
            datetime(2026, 4, 10, 14, 0, 30), # same valuation minute, should not rerun
        ]
    )

    def _now_provider():
        return next(schedule)

    slept: list[int] = []

    runtime = {
        "market_provider": object(),
        "line_client": object(),
        "watchlist_repo": object(),
        "message_repo": object(),
        "pending_repo": object(),
        "valuation_snapshot_repo": object(),
        "logger": _FakeLogger(events=[]),
        "pending_fallback": object(),
        "db_path": ":memory:",
    }

    result = _run_daemon_loop(
        runtime=runtime,
        timezone_name="Asia/Taipei",
        poll_interval_sec=60,
        valuation_time="14:00",
        cooldown_seconds=300,
        retry_count=3,
        stale_threshold_sec=90,
        max_loops=5,
        now_provider=_now_provider,
        sleep_fn=lambda sec: slept.append(sec),
    )

    assert result["status"] == "stopped"
    assert result["loops"] == 5
    assert result["minute_cycles"] == 2
    assert result["valuation_runs"] == 1
    assert result["reconcile_runs"] == 5
    assert calls["minute"] == 2
    assert calls["valuation"] == 1
    assert calls["reconcile"] == 5
    assert slept == [60, 60, 60, 60, 60]


def test_run_daemon_loop_keyboard_interrupt(monkeypatch):
    monkeypatch.setattr("stock_monitor.application.daemon_runner.run_minute_cycle", lambda **kwargs: {"status": "no_signal", "count": 0})
    monkeypatch.setattr("stock_monitor.application.daemon_runner.run_daily_valuation_job", lambda **kwargs: {"status": "skipped"})
    monkeypatch.setattr("stock_monitor.application.daemon_runner.run_reconcile_cycle", lambda **kwargs: {"reconciled": 0})

    runtime = {
        "market_provider": object(),
        "line_client": object(),
        "watchlist_repo": object(),
        "message_repo": object(),
        "pending_repo": object(),
        "valuation_snapshot_repo": object(),
        "logger": _FakeLogger(events=[]),
        "pending_fallback": object(),
    }

    def _raise_interrupt(_sec: int):
        raise KeyboardInterrupt

    result = _run_daemon_loop(
        runtime=runtime,
        timezone_name="Asia/Taipei",
        poll_interval_sec=60,
        valuation_time="14:00",
        cooldown_seconds=300,
        retry_count=3,
        stale_threshold_sec=90,
        max_loops=None,
        now_provider=lambda: datetime(2026, 4, 10, 9, 0, 0),
        sleep_fn=_raise_interrupt,
    )

    assert result["status"] == "interrupted"
    assert result["loops"] == 1
    assert result["reconcile_runs"] == 1


def test_tp_daemon_001_loop_exception_logged_and_loop_continues(monkeypatch):
    """[TP-DAEMON-001] EDD §13.3 CR-DAEMON-01: when the daemon loop body raises an
    exception, the daemon must NOT crash. It must log DAEMON_LOOP_EXCEPTION at ERROR
    level and continue executing the next iteration."""
    from stock_monitor.application.daemon_runner import _run_daemon_loop

    logged_errors: list[str] = []

    class _FakeLogger:
        def log(self, level: str, message: str):
            if level == "ERROR":
                logged_errors.append(message)

    calls = {"minute": 0}

    def _raise_on_first(**kwargs):
        calls["minute"] += 1
        if calls["minute"] == 1:
            raise RuntimeError("transient market error")
        return {"status": "no_signal", "count": 0}

    monkeypatch.setattr("stock_monitor.application.daemon_runner.run_minute_cycle", _raise_on_first)
    monkeypatch.setattr("stock_monitor.application.daemon_runner.run_daily_valuation_job", lambda **kwargs: {"status": "skipped"})
    monkeypatch.setattr("stock_monitor.application.daemon_runner.run_reconcile_cycle", lambda **kwargs: {"reconciled": 0})

    runtime = {
        "market_provider": object(),
        "line_client": object(),
        "watchlist_repo": object(),
        "message_repo": object(),
        "pending_repo": object(),
        "valuation_snapshot_repo": object(),
        "logger": _FakeLogger(),
        "pending_fallback": object(),
    }

    schedule = iter([
        datetime(2026, 4, 10, 9, 0, 0),   # loop 1: minute cycle raises
        datetime(2026, 4, 10, 9, 1, 0),   # loop 2: minute cycle succeeds
    ])

    result = _run_daemon_loop(
        runtime=runtime,
        timezone_name="Asia/Taipei",
        poll_interval_sec=60,
        valuation_time="14:00",
        cooldown_seconds=300,
        retry_count=3,
        stale_threshold_sec=90,
        max_loops=2,
        now_provider=lambda: next(schedule),
        sleep_fn=lambda sec: None,
    )

    assert result["status"] == "stopped", (
        "[TP-DAEMON-001] Daemon must continue after exception and return 'stopped' after max_loops."
    )
    assert result["loops"] == 2, "[TP-DAEMON-001] Daemon must complete both loops."
    assert any("DAEMON_LOOP_EXCEPTION" in msg for msg in logged_errors), (
        "[TP-DAEMON-001] Daemon must log DAEMON_LOOP_EXCEPTION at ERROR level when loop body raises."
    )


def test_stock_monitor_dunder_main_invokes_app_main(monkeypatch):
    monkeypatch.setattr("stock_monitor.app.main", lambda argv=None: 0)
    monkeypatch.setattr(sys, "argv", ["python", "run-once"])
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("stock_monitor.__main__", run_name="__main__")
    assert exc.value.code == 0


def test_stock_monitor_dunder_main_module_import_path():
    import importlib

    module = importlib.import_module("stock_monitor.__main__")
    assert module is not None


def test_stock_monitor_app_script_entrypoint(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "script-entry.db"
    monkeypatch.setattr(sys, "argv", ["stock_monitor.app", "--db-path", str(db_path), "init-db"])
    sys.modules.pop("stock_monitor.app", None)
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("stock_monitor.app", run_name="__main__")
    assert exc.value.code == 0


def test_run_minute_cycle_uses_db_stock_name_not_quote_name():
    """FR-18: opening summary stock display must use watchlist stock_name, not quote name."""
    logger = _FakeLogger(events=[])
    line_client = _FakeLineClient(sent=[])
    message_repo = _FakeMessageRepo(last_sent_map={}, rows=[])
    pending_repo = _FakePendingRepo(items=[])
    fallback = _FakeFallback(rows=[])

    now_epoch = int(datetime(2026, 4, 10, 9, 10, 0).timestamp())

    # watchlist has stock_name from DB; quote has blank name (Yahoo win scenario)
    # price 2100 > fair_price 2000, so no hit → only opening summary is sent
    result = run_minute_cycle(
        now_dt=datetime(2026, 4, 10, 9, 10, 0),
        market_data_provider=_FakeMarketProvider(
            snapshot={"index_tick_at": now_epoch},
            quotes={"2330": {"price": 2100.0, "tick_at": now_epoch, "name": ""}},
        ),
        line_client=line_client,
        watchlist_repo=_FakeWatchlistRepo(rows=[
            {"stock_no": "2330", "manual_fair_price": 2000.0, "manual_cheap_price": 1500.0, "stock_name": "台積電"},
        ]),
        message_repo=message_repo,
        pending_repo=pending_repo,
        pending_fallback=fallback,
        logger=logger,
    )

    assert result.get("status") in ("ok", "no_signal", "skipped", "persisted"), f"unexpected: {result}"
    # The opening summary LINE message must contain 台積電(2330), NOT bare 2330
    assert any("台積電(2330)" in msg for msg in line_client.sent), (
        "FR-18: opening summary must use watchlist stock_name '台積電(2330)', got: " + str(line_client.sent)
    )


def test_evaluate_manual_threshold_hits_uses_watchlist_stock_name_not_quote_name():
    """[TP-NAME-001] FR-18: evaluate_manual_threshold_hits must use watchlist_row['stock_name']
    (from DB), NOT quote['name'] (from API). stock_name in hit must equal DB value."""
    watchlist_rows = [
        {
            "stock_no": "2330",
            "stock_name": "台積電_DB",
            "manual_fair_price": 2000.0,
            "manual_cheap_price": 1500.0,
        }
    ]
    # Quote has a name from API — this must NOT appear in the hit
    quotes = {"2330": {"price": 1900.0, "tick_at": 123456, "name": "台積電_QUOTE"}}
    hits = evaluate_manual_threshold_hits(watchlist_rows=watchlist_rows, quotes=quotes)
    assert len(hits) == 1
    assert hits[0]["stock_name"] == "台積電_DB", (
        f"[TP-NAME-001] stock_name in hit must come from DB watchlist row ('台積電_DB'), "
        f"not from quote ('台積電_QUOTE'). Got: '{hits[0]['stock_name']}'"
    )


def test_evaluate_valuation_snapshot_hits_uses_stock_name_map():
    """[TP-NAME-001] FR-18: evaluate_valuation_snapshot_hits must accept stock_name_map param
    and use it for stock_name in hits, NOT quote['name'] from API."""
    snapshot_rows = [
        {
            "stock_no": "2330",
            "method_name": "emily_composite",
            "method_version": "v1",
            "fair_price": 2000.0,
            "cheap_price": 1500.0,
        }
    ]
    quotes = {"2330": {"price": 1900.0, "tick_at": 123456, "name": "台積電_QUOTE"}}
    stock_name_map = {"2330": "台積電_DB"}
    hits = evaluate_valuation_snapshot_hits(
        snapshot_rows=snapshot_rows,
        quotes=quotes,
        stock_name_map=stock_name_map,
    )
    assert len(hits) == 1
    assert hits[0]["stock_name"] == "台積電_DB", (
        f"[TP-NAME-001] evaluate_valuation_snapshot_hits must accept stock_name_map "
        f"and use it for stock_name. Got: '{hits[0]['stock_name']}'"
    )


def test_build_minute_rows_uses_stock_name_map_not_hit_name():
    """[TP-NAME-002] FR-18: build_minute_rows must accept stock_name_map param and use
    it to build display_label, NOT use hit['stock_name'] derived from quote['name']."""
    now_dt = datetime(2026, 4, 10, 10, 21, 0)
    now_epoch = int(now_dt.timestamp())
    hits = [
        {
            "stock_no": "2330",
            "stock_status": 1,
            "method": "manual_rule",
            "price": 1900.0,
            "stock_name": "",   # empty — as if quote had no name
            "fair_price": 2000.0,
            "cheap_price": 1500.0,
        }
    ]
    stock_name_map = {"2330": "台積電_DB"}
    rows = build_minute_rows(
        now_dt=now_dt,
        hits=hits,
        message_repo=_FakeMessageRepo(last_sent_map={}, rows=[]),
        pending_repo=_FakePendingRepo(items=[]),
        pending_fallback=_FakeFallback(rows=[]),
        cooldown_seconds=300,
        stock_name_map=stock_name_map,
    )
    assert len(rows) == 1
    assert "台積電_DB(2330)" in rows[0]["message"], (
        f"[TP-NAME-002] build_minute_rows message must include DB stock_name '台積電_DB(2330)'. "
        f"Got: '{rows[0]['message']}'"
    )


def test_run_minute_cycle_trigger_row_uses_db_stock_name():
    """[TP-NAME-002] FR-18: when price hits threshold, the LINE trigger notification
    display_label must use watchlist.stock_name (DB), NOT quote name from API."""
    logger = _FakeLogger(events=[])
    # Pre-mark opening summary as sent so the only LINE messages are trigger notifications
    logger.mark_opening_summary_sent("2026-04-10")
    line_client = _FakeLineClient(sent=[])
    message_repo = _FakeMessageRepo(last_sent_map={}, rows=[])
    pending_repo = _FakePendingRepo(items=[])
    fallback = _FakeFallback(rows=[])

    now_epoch = int(datetime(2026, 4, 10, 10, 21, 0).timestamp())

    # Price 1900 < fair_price 2000 → triggers status=1 notification
    # Quote has no name — DB has stock_name="台積電_DB"
    # Trigger row display_label must use DB name: "台積電_DB(2330)"
    result = run_minute_cycle(
        now_dt=datetime(2026, 4, 10, 10, 21, 0),
        market_data_provider=_FakeMarketProvider(
            snapshot={"index_tick_at": now_epoch},
            quotes={"2330": {"price": 1900.0, "tick_at": now_epoch}},
        ),
        line_client=line_client,
        watchlist_repo=_FakeWatchlistRepo(rows=[
            {
                "stock_no": "2330",
                "manual_fair_price": 2000.0,
                "manual_cheap_price": 1500.0,
                "stock_name": "台積電_DB",
            }
        ]),
        message_repo=message_repo,
        pending_repo=pending_repo,
        pending_fallback=fallback,
        logger=logger,
    )
    assert result.get("status") not in (None,), f"unexpected: {result}"
    assert any("台積電_DB(2330)" in msg for msg in line_client.sent), (
        "[TP-NAME-002] Trigger row must use DB stock_name '台積電_DB(2330)'. "
        f"Got messages: {line_client.sent}"
    )


def test_build_opening_method_pairs_empty_version_skips_row() -> None:
    """Cover line 98: name non-empty but version empty → continue."""
    pairs = _build_opening_method_pairs(
        [
            {"method_name": "custom_method", "method_version": ""},
            {"method_name": "custom_method", "method_version": "v1"},
        ]
    )
    # custom_method with empty version must be skipped; with v1 it's added
    names_versions = [(n, v) for n, v in pairs]
    assert ("custom_method", "") not in names_versions
    assert ("custom_method", "v1") in names_versions


def test_manual_valuation_calculator_raysky_missing_fields() -> None:
    """Cover lines 110-116 and branch 148->139: raysky returns None when required fields missing."""

    class _FakeWatchlistRepo:
        def list_enabled(self):
            return [
                {
                    "stock_no": "2330",
                    "manual_fair_price": 100.0,
                    "manual_cheap_price": 80.0,
                }
            ]

    calc = ManualValuationCalculator(watchlist_repo=_FakeWatchlistRepo(), trade_date="2026-04-18")
    # Patch _build_primary_inputs to return inputs missing required raysky fields
    orig_build = calc._build_primary_inputs

    def _incomplete_inputs(row: dict) -> dict:
        inputs = orig_build(row)
        inputs["current_assets"] = None
        inputs["total_liabilities"] = None
        inputs["shares_outstanding"] = None
        return inputs

    calc._build_primary_inputs = _incomplete_inputs
    snapshots = calc.calculate()
    # Emily + Oldbull snapshots are still produced; raysky is skipped (returns None)
    methods = {s["method_name"] for s in snapshots}
    assert "emily_composite" in methods
    assert "oldbull_dividend_yield" in methods
    assert "raysky_blended_margin" not in methods
    # Events should include the VALUATION_SKIP_INSUFFICIENT_DATA log
    assert any("VALUATION_SKIP_INSUFFICIENT_DATA" in ev[1] for ev in calc.events)

