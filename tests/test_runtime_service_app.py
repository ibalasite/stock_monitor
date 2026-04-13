from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import runpy
import sys

import pytest

from stock_monitor.application.runtime_service import (
    _format_price,
    _to_epoch_seconds,
    build_minute_rows,
    evaluate_manual_threshold_hits,
    run_minute_cycle,
    run_reconcile_cycle,
)
from stock_monitor.app import _ManualValuationCalculator, _build_runtime, _resolve_timezone, _run_daemon_loop, main


@dataclass
class _FakeLogger:
    events: list[tuple[str, str]]

    def log(self, level: str, message: str):
        self.events.append((level, message))


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
    assert len(line_client.sent) == 1
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
        line_client=line_client,
        message_repo=message_repo,
        pending_repo=pending_repo,
        logger=logger,
    )
    assert reconcile_result["reconciled"] == 1


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
        "logger": object(),
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
    assert _resolve_timezone("Invalid/Timezone/Name") is not None


def test_manual_valuation_calculator_from_watchlist():
    class _WatchlistRepo:
        def list_enabled(self):
            return [{"stock_no": "2330", "manual_fair_price": 1500, "manual_cheap_price": 1000}]

    calculator = _ManualValuationCalculator(
        watchlist_repo=_WatchlistRepo(),
        trade_date="2026-04-10",
    )
    snapshots = calculator.calculate()
    assert snapshots == [
        {
            "stock_no": "2330",
            "trade_date": "2026-04-10",
            "method_name": "manual_rule",
            "method_version": "v1",
            "fair_price": 1500.0,
            "cheap_price": 1000.0,
        }
    ]


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

    monkeypatch.setattr("stock_monitor.app.run_minute_cycle", _fake_run_minute_cycle)
    monkeypatch.setattr("stock_monitor.app.run_daily_valuation_job", _fake_run_daily_valuation_job)
    monkeypatch.setattr("stock_monitor.app.run_reconcile_cycle", _fake_run_reconcile_cycle)

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
        "logger": object(),
        "pending_fallback": object(),
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
    monkeypatch.setattr("stock_monitor.app.run_minute_cycle", lambda **kwargs: {"status": "no_signal", "count": 0})
    monkeypatch.setattr("stock_monitor.app.run_daily_valuation_job", lambda **kwargs: {"status": "skipped"})
    monkeypatch.setattr("stock_monitor.app.run_reconcile_cycle", lambda **kwargs: {"reconciled": 0})

    runtime = {
        "market_provider": object(),
        "line_client": object(),
        "watchlist_repo": object(),
        "message_repo": object(),
        "pending_repo": object(),
        "valuation_snapshot_repo": object(),
        "logger": object(),
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
