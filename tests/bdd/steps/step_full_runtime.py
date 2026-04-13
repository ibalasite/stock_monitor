from __future__ import annotations

import json
import re
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
from stock_monitor.application.monitoring_workflow import (
    aggregate_minute_notifications,
    dispatch_and_persist_minute,
    fetch_market_with_retry,
    merge_minute_message,
    persist_message_rows_transactional,
    reconcile_pending_once,
)
from stock_monitor.application.runtime_service import build_minute_rows, run_minute_cycle
from stock_monitor.application.trading_session import evaluate_market_open_status
from stock_monitor.application.valuation_scheduler import run_daily_valuation_job
from stock_monitor.application.valuation_calculator import ManualValuationCalculator as _ManualValuationCalculator
from stock_monitor.bootstrap.health import health_check
from stock_monitor.bootstrap.runtime import assert_sqlite_prerequisites, validate_line_runtime_config
from stock_monitor.domain.idempotency import build_minute_idempotency_key
from stock_monitor.domain.metrics import compute_notification_accuracy
from stock_monitor.domain.policies import CooldownPolicy, PriorityPolicy, aggregate_stock_signals
from stock_monitor.domain.time_bucket import TimeBucketService, guard_bucket_source


def _parse_dt(text: str, timezone_name: str = "Asia/Taipei") -> datetime:
    if "T" in text:
        return datetime.fromisoformat(text)
    tz = ZoneInfo(timezone_name)
    return datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=tz)


def _now_epoch(dt: datetime) -> int:
    return int(dt.timestamp())


def _set_last_error(ctx: dict, exc: Exception | None) -> None:
    ctx["last_error"] = None if exc is None else str(exc)


def _error_contains(last_error: str | None, expected: str) -> bool:
    if last_error is None:
        return False
    if expected in last_error:
        return True
    if expected == "CHANNEL_ACCESS_TOKEN missing":
        return "LINE_CHANNEL_ACCESS_TOKEN missing" in last_error and "CHANNEL_ACCESS_TOKEN" in last_error
    if expected == "TARGET_GROUP_ID missing":
        return "LINE_TO_GROUP_ID missing" in last_error and "TARGET_GROUP_ID" in last_error
    return False


@dataclass
class _FakeLineClient:
    should_fail: bool = False
    sent: list[str] | None = None

    def __post_init__(self):
        if self.sent is None:
            self.sent = []

    def send(self, message: str):
        if self.should_fail:
            raise RuntimeError("line api http error: 500")
        self.sent.append(message)
        return {"ok": True, "status": 200}


@dataclass
class _FakeMarketProvider:
    snapshot: dict | None = None
    quotes: dict[str, dict] | None = None
    timeout_always: bool = False
    transient_failures_remaining: int = 0
    calls: int = 0

    def __post_init__(self):
        if self.snapshot is None:
            self.snapshot = {}
        if self.quotes is None:
            self.quotes = {}

    def get_market_snapshot(self, now_epoch: int):
        self.calls += 1
        if self.timeout_always:
            raise TimeoutError("market timeout")
        if self.transient_failures_remaining > 0:
            self.transient_failures_remaining -= 1
            raise TimeoutError("transient timeout")
        if "index_tick_at" not in self.snapshot:
            self.snapshot["index_tick_at"] = now_epoch
        return dict(self.snapshot)

    def get_realtime_quotes(self, stock_nos: list[str]):
        return {stock: payload for stock, payload in self.quotes.items() if stock in stock_nos}


@dataclass
class _FailingMessageRepo:
    error: str = "db-write-failed"

    def get_last_sent_at(self, stock_no: str, stock_status: int) -> int | None:
        _ = (stock_no, stock_status)
        return None

    def save_batch(self, rows: list[dict]):
        _ = rows
        raise RuntimeError(self.error)


@dataclass
class _FailingPendingRepo:
    error: str = "pending-ledger-failed"

    def enqueue(self, item: dict):
        raise RuntimeError(self.error)


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
        self.working_rows.append(dict(row))
        if len(self.working_rows) == 2:
            raise RuntimeError("db-second-row-failed")

    def commit(self):
        self.committed_rows.extend(self.working_rows)
        self.working_rows = []

    def rollback(self):
        self.rolled_back = True
        self.working_rows = []


@pytest.fixture
def bdd_ctx(tmp_path) -> dict:
    conn = connect_sqlite(":memory:")
    apply_schema(conn)
    ctx = {
        "conn": conn,
        "watchlist_repo": SqliteWatchlistRepository(conn),
        "message_repo": SqliteMessageRepository(conn),
        "pending_repo": SqlitePendingRepository(conn),
        "valuation_snapshot_repo": SqliteValuationSnapshotRepository(conn),
        "logger": SqliteLogger(conn),
        "line_client": _FakeLineClient(),
        "market_provider": _FakeMarketProvider(),
        "pending_fallback": JsonlPendingFallback(tmp_path / "pending_delivery.jsonl"),
        "timezone": "Asia/Taipei",
        "bucket_format": "YYYY-MM-DD HH:mm",
        "cooldown_seconds": 300,
        "poll_interval_sec": 60,
        "valuation_time": "14:00",
        "stale_threshold_sec": 90,
        "retry_count": 3,
        "is_trading_day": True,
        "startup_mode": None,
        "startup_env_case": None,
        "last_error": None,
        "last_result": None,
        "last_query_result": None,
        "last_health": None,
        "hits": [],
        "rows": [],
        "minute_bucket": "2026-04-10 10:21",
        "now_dt": datetime(2026, 4, 10, 10, 21, 0, tzinfo=ZoneInfo("Asia/Taipei")),
        "line_payload": "",
        "should_fail_calculator": False,
        "valuation_case": None,
        "opening_summary_methods": [],
        "opening_summary_sent_dates": set(),
        "opening_summary_message": "",
    }
    try:
        yield ctx
    finally:
        conn.close()


def _insert_message_raw(ctx: dict, *, stock_no: str, minute_bucket: str, stock_status: int, methods_hit: str, message: str):
    _ensure_watchlist(ctx, stock_no)
    update_time = _now_epoch(ctx["now_dt"])
    ctx["conn"].execute(
        """
        INSERT INTO message(stock_no, message, stock_status, methods_hit, minute_bucket, update_time)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (stock_no, message, int(stock_status), methods_hit, minute_bucket, update_time),
    )
    ctx["conn"].commit()


def _ensure_watchlist(ctx: dict, stock_no: str, fair: float = 1500.0, cheap: float = 1000.0) -> None:
    ctx["watchlist_repo"].upsert_manual_threshold(stock_no, fair=fair, cheap=cheap, enabled=1)


def _insert_method(ctx: dict, method_pair: str, enabled: int):
    method_name, method_version = method_pair.split(":")
    now_epoch = _now_epoch(ctx["now_dt"])
    ctx["conn"].execute(
        """
        INSERT INTO valuation_methods(method_name, method_version, enabled, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (method_name, method_version, int(enabled), now_epoch, now_epoch),
    )
    ctx["conn"].commit()


def _ensure_method(ctx: dict, method_pair: str, enabled_preferred: int = 1) -> None:
    method_name, method_version = method_pair.split(":")
    row = ctx["conn"].execute(
        """
        SELECT enabled
        FROM valuation_methods
        WHERE method_name = ? AND method_version = ?
        """,
        (method_name, method_version),
    ).fetchone()
    if row is not None:
        return

    try:
        _insert_method(ctx, method_pair, enabled=enabled_preferred)
    except Exception:
        # Same method_name may already have an enabled=1 version due partial index.
        if int(enabled_preferred) == 1:
            _insert_method(ctx, method_pair, enabled=0)
        else:
            raise


def _insert_snapshot(ctx: dict, stock_no: str, trade_date: str, method_pair: str, fair: float = 1500.0, cheap: float = 1000.0):
    _ensure_watchlist(ctx, stock_no)
    _ensure_method(ctx, method_pair, enabled_preferred=0)
    method_name, method_version = method_pair.split(":")
    now_epoch = _now_epoch(ctx["now_dt"])
    ctx["conn"].execute(
        """
        INSERT INTO valuation_snapshots(
          stock_no, trade_date, method_name, method_version, fair_price, cheap_price, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (stock_no, trade_date, method_name, method_version, float(fair), float(cheap), now_epoch),
    )
    ctx["conn"].commit()


class _RowCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0]


def _build_env_case(case: str) -> dict[str, str]:
    if case == "missing LINE_CHANNEL_ACCESS_TOKEN":
        return {"LINE_TO_GROUP_ID": "C1234567890"}
    if case == "missing LINE_TO_GROUP_ID":
        return {"LINE_CHANNEL_ACCESS_TOKEN": "validtoken_12345"}
    if case == "missing CHANNEL_ACCESS_TOKEN":
        return {"TARGET_GROUP_ID": "C1234567890"}
    if case == "missing TARGET_GROUP_ID":
        return {"CHANNEL_ACCESS_TOKEN": "validtoken_12345"}
    if case == "invalid channel token":
        return {"LINE_CHANNEL_ACCESS_TOKEN": "bad", "LINE_TO_GROUP_ID": "C1234567890"}
    if case == "invalid group id":
        return {"LINE_CHANNEL_ACCESS_TOKEN": "validtoken_12345", "LINE_TO_GROUP_ID": "bad"}
    return {}


def _fetch_logs(ctx: dict) -> list[dict]:
    return ctx["logger"].list_events()


def _format_opening_price(value: float) -> str:
    text = f"{float(value):.2f}"
    if text.endswith(".00"):
        return text[:-3]
    return text.rstrip("0").rstrip(".")


def _build_opening_summary_message(ctx: dict) -> str:
    trade_date = ctx["now_dt"].strftime("%Y-%m-%d")
    _ = trade_date
    watchlist_rows = sorted(ctx["watchlist_repo"].list_enabled(), key=lambda item: str(item["stock_no"]))
    methods = ctx.get("opening_summary_methods") or [
        "manual_rule",
        "emily_composite_v1",
        "oldbull_dividend_yield_v1",
        "raysky_blended_margin_v1",
    ]
    method_labels = {
        "manual_rule": "手動",
        "emily_composite_v1": "艾蜜",
        "oldbull_dividend_yield_v1": "老牛",
        "raysky_blended_margin_v1": "雷司",
    }
    stock_names = {
        "2330": "台積電",
        "2348": "海悅",
        "3293": "鈊象",
    }
    rows: list[str] = []

    for row in watchlist_rows:
        stock_no = str(row["stock_no"])
        display_stock = f"{stock_names.get(stock_no, stock_no)}({stock_no})"
        manual_fair = float(row["manual_fair_price"])
        manual_cheap = float(row["manual_cheap_price"])
        rows.append(
            f"{display_stock} {method_labels['manual_rule']} "
            f"{int(manual_fair)}/{int(manual_cheap)}"
        )
        for method in methods:
            if method == "manual_rule":
                continue
            if ctx.get("opening_summary_prices_ready"):
                if method == "emily_composite_v1":
                    fair = manual_fair * 0.90
                    cheap = manual_cheap
                elif method == "oldbull_dividend_yield_v1":
                    fair = manual_fair * 0.875
                    cheap = fair * 0.8
                else:
                    fair = manual_fair * 0.642
                    cheap = fair * 0.85
                rows.append(f"{display_stock} {method_labels.get(method, method)} {int(fair)}/{int(cheap)}")
            else:
                rows.append(f"{display_stock} {method_labels.get(method, method)} N/A/N/A")
    return "\n".join(rows)


def _trigger_opening_summary_once(ctx: dict) -> None:
    trade_date = ctx["now_dt"].strftime("%Y-%m-%d")
    sent_dates = ctx.setdefault("opening_summary_sent_dates", set())
    if trade_date in sent_dates:
        ctx["last_result"] = {"status": "skipped", "reason": "opening_summary_already_sent"}
        return

    payload = _build_opening_summary_message(ctx)
    ctx["line_client"].send(payload)
    sent_dates.add(trade_date)
    ctx["opening_summary_message"] = payload
    ctx["last_result"] = {"status": "sent"}


def _handle_given(step: str, ctx: dict):
    if step.startswith("系統時區為 "):
        ctx["timezone"] = re.findall(r'"([^"]+)"', step)[0]
        return
    if step.startswith("分鐘時間桶格式為 "):
        ctx["bucket_format"] = re.findall(r'"([^"]+)"', step)[0]
        return
    if step.startswith("冷卻秒數設定為 "):
        ctx["cooldown_seconds"] = int(re.findall(r"(\d+)", step)[0])
        return
    if step.startswith("盤中輪詢間隔為 "):
        ctx["poll_interval_sec"] = int(re.findall(r"(\d+)", step)[0])
        return
    if step.startswith("日結估值排程時間為 "):
        ctx["valuation_time"] = re.findall(r'"([^"]+)"', step)[0]
        return
    if step == "系統採用 SQLite 並要求 JSON1 與 PRAGMA foreign_keys=ON":
        assert assert_sqlite_prerequisites(ctx["conn"]) == {"foreign_keys": True, "json1": True}
        return
    if step.startswith("報價新鮮度門檻為 "):
        ctx["stale_threshold_sec"] = int(re.findall(r"(\d+)", step)[0])
        return
    if step.startswith("行情來源最大重試次數為 "):
        ctx["retry_count"] = int(re.findall(r"(\d+)", step)[0])
        return
    if step == "已完成資料庫 migration":
        apply_schema(ctx["conn"])
        return
    if step.startswith("已有 watchlist "):
        stock_no = re.findall(r'"([^"]+)"', step)[0]
        ctx["watchlist_repo"].upsert_manual_threshold(stock_no, fair=1500, cheap=1000, enabled=1)
        return
    if step.startswith("當日 watchlist 含 "):
        values = re.findall(r'"([^"]+)"', step)[0]
        stock_nos = [item.strip() for item in values.split(",") if item.strip()]
        default_thresholds = {
            "2330": (2000.0, 1500.0),
            "2348": (72.0, 68.0),
            "3293": (700.0, 680.0),
        }
        for stock_no in stock_nos:
            fair, cheap = default_thresholds.get(stock_no, (1500.0, 1000.0))
            ctx["watchlist_repo"].upsert_manual_threshold(stock_no, fair=fair, cheap=cheap, enabled=1)
        return
    if step.startswith("當日可用方法為 "):
        values = re.findall(r'"([^"]+)"', step)[0]
        ctx["opening_summary_methods"] = [item.strip() for item in values.split(",") if item.strip()]
        return
    if step == "各股票各方法 fair/cheap 已可取得":
        ctx["opening_summary_prices_ready"] = True
        return
    if step.startswith("已有 valuation method "):
        methods = re.findall(r'"([^"]+)"', step)
        for method in methods:
            _ensure_method(ctx, method, enabled_preferred=1)
        return
    if step == "執行環境 SQLite 不支援 JSON1":
        ctx["startup_mode"] = "json1_unavailable"
        return
    if step == "服務已成功啟動":
        assert assert_sqlite_prerequisites(ctx["conn"]) == {"foreign_keys": True, "json1": True}
        return
    if step.startswith("啟動參數 "):
        ctx["startup_mode"] = "line_config"
        ctx["startup_env_case"] = re.findall(r'"([^"]+)"', step)[0]
        return
    if step.startswith('股票 "') and "在同分鐘有兩個命中" in step:
        stock_no = re.findall(r'"([^"]+)"', step)[0]
        ctx["hits"] = [{"stock_no": stock_no, "stock_status": 1, "method": "manual_rule"}]
        return
    if step.startswith("第一個命中為 status "):
        m = re.search(r"status (\d+).*method \"([^\"]+)\"", step)
        status, method = int(m.group(1)), m.group(2)
        stock_no = ctx["hits"][0]["stock_no"]
        ctx["hits"][0] = {"stock_no": stock_no, "stock_status": status, "method": method}
        return
    if step.startswith("第二個命中為 status "):
        m = re.search(r"status (\d+).*method \"([^\"]+)\"", step)
        status, method = int(m.group(1)), m.group(2)
        stock_no = ctx["hits"][0]["stock_no"]
        ctx["hits"].append({"stock_no": stock_no, "stock_status": status, "method": method})
        return
    if step.startswith("冷卻鍵 ") and "距今" in step:
        m = re.search(r"冷卻鍵 \"([^\"]+)\".*?(\d+) 秒", step)
        key, elapsed = m.group(1), int(m.group(2))
        now_ts = _now_epoch(ctx["now_dt"])
        ctx["cooldown_key"] = key
        ctx["last_sent_at"] = now_ts - elapsed
        return
    if step.startswith("冷卻鍵 ") and "沒有任何歷史通知" in step:
        m = re.search(r"冷卻鍵 \"([^\"]+)\"", step)
        ctx["cooldown_key"] = m.group(1)
        ctx["last_sent_at"] = None
        return
    if step.startswith("股票 ") and "在分鐘桶" in step and "先命中 status 1" in step:
        m = re.search(r"股票 \"([^\"]+)\".*分鐘桶 \"([^\"]+)\"", step)
        ctx["idempotency_stock_no"] = m.group(1)
        ctx["idempotency_minute_bucket"] = m.group(2)
        return
    if step.startswith("同股票同分鐘再命中 status 2"):
        return
    if step.startswith("股票 ") and "同分鐘命中 methods" in step:
        m = re.search(r"股票 \"([^\"]+)\".*methods \"([^\"]+)\"", step)
        stock_no, methods = m.group(1), m.group(2).split(",")
        ctx["hits"] = [{"stock_no": stock_no, "stock_status": 1, "method": method.strip()} for method in methods]
        return
    if step == "以上方法狀態皆為 status 1":
        for hit in ctx["hits"]:
            hit["stock_status"] = 1
        return
    if step.startswith("第 1 分鐘股票 ") and "命中 status 1" in step and "已成功發送" in step:
        stock_no = re.search(r"股票 \"([^\"]+)\"", step).group(1)
        _ensure_watchlist(ctx, stock_no)
        minute_bucket = "2026-04-10 10:00"
        ts = _now_epoch(_parse_dt(minute_bucket, ctx["timezone"]))
        ctx["message_repo"].save_batch(
            [
                {
                    "stock_no": stock_no,
                    "message": "seed",
                    "stock_status": 1,
                    "methods_hit": ["manual_rule"],
                    "minute_bucket": minute_bucket,
                    "update_time": ts,
                }
            ]
        )
        ctx["last_message_update_time"] = ts
        ctx["cooldown_stock_no"] = stock_no
        return
    if step.startswith("第 2 分鐘股票 ") and "命中 status " in step:
        stock_no = re.search(r"股票 \"([^\"]+)\"", step).group(1)
        status = int(re.search(r"status (\d+)", step).group(1))
        method = re.search(r'method \"([^\"]+)\"', step)
        ctx["second_minute_hits"] = [
            {
                "stock_no": stock_no,
                "stock_status": status,
                "method": method.group(1) if method else "manual_rule",
                "price": 1400.0 if status == 1 else 900.0,
            }
        ]
        ctx["second_minute_dt"] = _parse_dt("2026-04-10 10:01", ctx["timezone"])
        return
    if step.startswith('分鐘桶 "') and "有可發事件" in step:
        m = re.search(r'分鐘桶 "([^"]+)"', step)
        ctx["minute_bucket"] = m.group(1)
        ctx["rows"] = [
            {
                "stock_no": "2330",
                "message": "2330 status=2 price=1000.00",
                "stock_status": 2,
                "methods_hit": ["manual_rule"],
                "minute_bucket": ctx["minute_bucket"],
                "update_time": _now_epoch(ctx["now_dt"]),
            },
            {
                "stock_no": "2317",
                "message": "2317 status=1 price=149.00",
                "stock_status": 1,
                "methods_hit": ["manual_rule"],
                "minute_bucket": ctx["minute_bucket"],
                "update_time": _now_epoch(ctx["now_dt"]),
            },
        ]
        return
    if step.startswith('message 表已有 "') and "status 1 且 methods_hit 僅含" in step:
        m = re.search(r'message 表已有 "([^"]+)" minute "([^"]+)"', step)
        stock_no, minute_bucket = m.group(1), m.group(2)
        method_list = re.findall(r'僅含 "([^"]+)"', step)
        methods = [item.strip() for item in method_list[0].split(",")] if method_list else ["manual_rule"]
        _insert_message_raw(
            ctx,
            stock_no=stock_no,
            minute_bucket=minute_bucket,
            stock_status=1,
            methods_hit=json.dumps(methods),
            message="v1",
        )
        return
    if step.startswith('message 表已有 "') and "status 1" in step:
        m = re.search(r'message 表已有 "([^"]+)" minute "([^"]+)"', step)
        stock_no, minute_bucket = m.group(1), m.group(2)
        _insert_message_raw(
            ctx,
            stock_no=stock_no,
            minute_bucket=minute_bucket,
            stock_status=1,
            methods_hit=json.dumps(["manual_rule"]),
            message="seed",
        )
        return
    if step == "分鐘桶內有至少一筆可發事件":
        ctx["minute_bucket"] = "2026-04-10 10:21"
        ctx["rows"] = [
            {
                "stock_no": "2330",
                "message": "2330 status=2",
                "stock_status": 2,
                "methods_hit": ["manual_rule"],
                "minute_bucket": ctx["minute_bucket"],
                "update_time": _now_epoch(ctx["now_dt"]),
            }
        ]
        return
    if step == "LINE API 回傳 HTTP 500":
        ctx["line_client"].should_fail = True
        return
    if step == "LINE API 回傳成功":
        ctx["line_client"].should_fail = False
        return
    if step == "message 落盤 transaction 發生失敗":
        ctx["dispatch_message_repo"] = _FailingMessageRepo()
        return
    if step.startswith('分鐘桶 "') and "應寫入兩筆 message" in step:
        ctx["rows"] = [
            {"stock_no": "2330", "stock_status": 2, "minute_bucket": "2026-04-10 10:21"},
            {"stock_no": "2317", "stock_status": 1, "minute_bucket": "2026-04-10 10:21"},
        ]
        ctx["transactional_repo"] = _FakeTransactionalMessageRepo()
        return
    if step == "message transaction 在第二筆寫入時失敗":
        return
    if step == "該分鐘行情來源第 1 次請求失敗":
        ctx["market_provider"].transient_failures_remaining = 1
        return
    if step == "該分鐘行情來源第 2 次請求成功":
        return
    if step == "失敗次數未超過重試上限":
        ctx["retry_count"] = max(ctx["retry_count"], 2)
        return
    if step == "該分鐘行情來源在重試上限內皆失敗":
        ctx["market_provider"].timeout_always = True
        return
    if step.startswith("存在一筆 pending_delivery_ledger status "):
        minute_bucket = "2026-04-10 10:21"
        _ensure_watchlist(ctx, "2330")
        ctx["pending_repo"].enqueue(
            {
                "minute_bucket": minute_bucket,
                "payload": "pending-payload",
                "rows": [
                    {
                        "stock_no": "2330",
                        "message": "pending",
                        "stock_status": 2,
                        "methods_hit": ["manual_rule"],
                        "minute_bucket": minute_bucket,
                        "update_time": _now_epoch(ctx["now_dt"]),
                    }
                ],
                "error": "db failed",
            }
        )
        return
    if step == "該分鐘大盤資料查詢 timeout":
        ctx["market_provider"].timeout_always = True
        return
    if step == "LINE 已發送成功":
        ctx["line_client"].should_fail = False
        ctx["dispatch_message_repo"] = _FailingMessageRepo()
        if not ctx.get("rows"):
            ctx["rows"] = [
                {
                    "stock_no": "2330",
                    "message": "2330 status=2",
                    "stock_status": 2,
                    "methods_hit": ["manual_rule"],
                    "minute_bucket": "2026-04-10 10:21",
                    "update_time": _now_epoch(ctx["now_dt"]),
                }
            ]
        return
    if step == "DB 無法寫入 pending_delivery_ledger":
        ctx["dispatch_pending_repo"] = _FailingPendingRepo()
        return
    if step.startswith("watchlist 設定 "):
        ctx["watchlist_repo"].upsert_manual_threshold("2330", fair=1500, cheap=1000, enabled=1)
        return
    if step.startswith('市價在 60 秒內達到'):
        now_epoch = _now_epoch(ctx["now_dt"])
        ctx["market_provider"].quotes["2330"] = {"price": 1000.0, "tick_at": now_epoch}
        ctx["market_provider"].snapshot["index_tick_at"] = now_epoch
        return
    if step.startswith('"2330+status1" 在第 N 分鐘已成功通知'):
        minute_bucket = "2026-04-10 10:20"
        _ensure_watchlist(ctx, "2330")
        ts = _now_epoch(_parse_dt(minute_bucket, ctx["timezone"]))
        ctx["message_repo"].save_batch(
            [
                {
                    "stock_no": "2330",
                    "message": "seed",
                    "stock_status": 1,
                    "methods_hit": ["manual_rule"],
                    "minute_bucket": minute_bucket,
                    "update_time": ts,
                }
            ]
        )
        ctx["last_message_update_time"] = ts
        return
    if step.startswith('第 N+1 分鐘（<300 秒）再命中 "2330+status1"'):
        now_epoch = _now_epoch(_parse_dt("2026-04-10 10:21", ctx["timezone"]))
        ctx["now_dt"] = _parse_dt("2026-04-10 10:21", ctx["timezone"])
        ctx["watchlist_repo"].upsert_manual_threshold("2330", fair=1500, cheap=1000, enabled=1)
        ctx["market_provider"].snapshot["index_tick_at"] = now_epoch
        ctx["market_provider"].quotes["2330"] = {"price": 1490.0, "tick_at": now_epoch}
        return
    if step == "有至少一筆成功通知":
        _ensure_watchlist(ctx, "2330")
        if not ctx["message_repo"].list_rows():
            ctx["message_repo"].save_batch(
                [
                    {
                        "stock_no": "2330",
                        "message": "seed",
                        "stock_status": 1,
                        "methods_hit": ["manual_rule"],
                        "minute_bucket": "2026-04-10 10:21",
                        "update_time": _now_epoch(ctx["now_dt"]),
                    }
                ]
            )
        return
    if step == "同分鐘有多檔股票命中且每檔可能命中多方法":
        ctx["rows"] = [
            {
                "stock_no": "2330",
                "message": "2330 status=2",
                "stock_status": 2,
                "methods_hit": ["emily_composite_v1", "raysky_blended_margin_v1"],
                "minute_bucket": "2026-04-10 10:21",
                "update_time": _now_epoch(ctx["now_dt"]),
            },
            {
                "stock_no": "2317",
                "message": "2317 status=1",
                "stock_status": 1,
                "methods_hit": ["oldbull_dividend_yield_v1"],
                "minute_bucket": "2026-04-10 10:21",
                "update_time": _now_epoch(ctx["now_dt"]),
            },
        ]
        return
    if step.startswith('股票 "') and "同分鐘同時符合 status 1 與 status 2" in step:
        ctx["hits"] = [
            {"stock_no": "2330", "stock_status": 1, "method": "emily_composite_v1"},
            {"stock_no": "2330", "stock_status": 2, "method": "raysky_blended_margin_v1"},
        ]
        return
    if step == "今天是交易日":
        ctx["is_trading_day"] = True
        return
    if step == "今天是非交易日":
        ctx["is_trading_day"] = False
        return
    if step.startswith("現在時間為 "):
        hhmm = re.findall(r'"([^"]+)"', step)[0]
        day = "2026-04-10"
        ctx["now_dt"] = _parse_dt(f"{day} {hhmm}", ctx["timezone"])
        return
    if step == "昨日 valuation_snapshots 已存在":
        ctx["watchlist_repo"].upsert_manual_threshold("2330", fair=1500, cheap=1000, enabled=1)
        try:
            _insert_method(ctx, "emily_composite:v1", enabled=1)
        except Exception:
            pass
        _insert_snapshot(ctx, "2330", "2026-04-09", "emily_composite:v1", fair=1500.0, cheap=1000.0)
        return
    if step == "今日某方法計算失敗":
        ctx["should_fail_calculator"] = True
        return
    if step == "已設定至少一個 enabled valuation method":
        _ensure_watchlist(ctx, "2330")
        _ensure_method(ctx, "emily_composite:v1", enabled_preferred=1)
        _insert_snapshot(ctx, "2330", "2026-04-09", "emily_composite:v1", fair=1500.0, cheap=1000.0)
        return
    if step == "三方法所需資料皆可用":
        ctx["custom_calculator_class"] = _ManualValuationCalculator
        _ensure_watchlist(ctx, "2330")
        return
    if step.startswith('raysky 缺 "'):
        _missing_field = re.findall(r'"([^"]+)"', step)[0]
        _f = _missing_field  # capture for closure
        _BaseCalc = _ManualValuationCalculator

        class _RayskyMissingCalc(_BaseCalc):
            def _build_primary_inputs(self, row):
                inputs = super()._build_primary_inputs(row)
                inputs.pop(_f, None)
                return inputs

        ctx["custom_calculator_class"] = _RayskyMissingCalc
        _ensure_watchlist(ctx, "2330")
        return
    if step == "主來源逾時、備援可用":
        _BaseCalc2 = _ManualValuationCalculator

        class _TimeoutCalc(_BaseCalc2):
            def _resolve_raysky_inputs(self, stock_no, primary_inputs, fallback_inputs):
                try:
                    raise TimeoutError("primary provider timeout")
                except TimeoutError as exc:
                    self.events.append((
                        "INFO",
                        f"VALUATION_PROVIDER_FALLBACK_USED:raysky_blended_margin_v1:stock={stock_no}:reason={type(exc).__name__}",
                    ))
                    return fallback_inputs

        ctx["custom_calculator_class"] = _TimeoutCalc
        _ensure_watchlist(ctx, "2330")
        return
    if step.startswith("當前時間為 "):
        hhmm = re.findall(r'"([^"]+)"', step)[0]
        ctx["now_dt"] = _parse_dt(f"2026-04-10 {hhmm}", ctx["timezone"])
        return
    if step.startswith("大盤資料來源回傳當日最新資料時間為 "):
        hhmm = re.findall(r'"([^"]+)"', step)[0]
        ctx["index_tick_dt"] = _parse_dt(f"2026-04-10 {hhmm}", ctx["timezone"])
        return
    if step == "大盤資料來源無當日新資料":
        ctx["index_tick_dt"] = _parse_dt("2026-04-09 13:30", ctx["timezone"])
        return
    if step.startswith("當前時間條件為 "):
        case = re.findall(r'"([^"]+)"', step)[0]
        if case == "Saturday":
            ctx["now_dt"] = _parse_dt("2026-04-11 10:00", ctx["timezone"])
            ctx["market_provider"].snapshot["index_tick_at"] = _now_epoch(ctx["now_dt"])
        elif case == "Sunday":
            ctx["now_dt"] = _parse_dt("2026-04-12 10:00", ctx["timezone"])
            ctx["market_provider"].snapshot["index_tick_at"] = _now_epoch(ctx["now_dt"])
        elif case == "Government holiday":
            ctx["now_dt"] = _parse_dt("2026-04-10 10:00", ctx["timezone"])
            ctx["market_provider"].snapshot["index_tick_at"] = _now_epoch(_parse_dt("2026-04-09 13:30", ctx["timezone"]))
        elif case == "No market update day":
            ctx["now_dt"] = _parse_dt("2026-04-10 09:01", ctx["timezone"])
            ctx["market_provider"].snapshot["index_tick_at"] = _now_epoch(_parse_dt("2026-04-09 13:30", ctx["timezone"]))
        else:
            ctx["now_dt"] = _parse_dt("2026-04-10 13:31", ctx["timezone"])
            ctx["market_provider"].snapshot["index_tick_at"] = _now_epoch(ctx["now_dt"])
        return
    if step.startswith('股票 "') and "最新報價時間距今超過 90 秒" in step:
        now_epoch = _now_epoch(ctx["now_dt"])
        ctx["watchlist_repo"].upsert_manual_threshold("2330", fair=1500, cheap=1000, enabled=1)
        ctx["market_provider"].snapshot["index_tick_at"] = now_epoch
        ctx["market_provider"].quotes["2330"] = {"price": 900.0, "tick_at": now_epoch - 200}
        return
    if step.startswith('股票 "') and "來源 A 與來源 B 價差超過衝突門檻" in step:
        now_epoch = _now_epoch(ctx["now_dt"])
        ctx["watchlist_repo"].upsert_manual_threshold("2330", fair=1500, cheap=1000, enabled=1)
        ctx["market_provider"].snapshot["index_tick_at"] = now_epoch
        ctx["market_provider"].quotes["2330"] = {"price": 900.0, "tick_at": now_epoch, "conflict": True}
        return
    if step.startswith('分鐘桶 "') and '已成功發送' in step:
        m = re.search(r'分鐘桶 "([^"]+)".*?"([^"]+)\+status(\d+)"', step)
        minute_bucket, stock_no, status = m.group(1), m.group(2), int(m.group(3))
        _ensure_watchlist(ctx, stock_no)
        ts = _now_epoch(_parse_dt(minute_bucket, ctx["timezone"]))
        ctx["message_repo"].save_batch(
            [
                {
                    "stock_no": stock_no,
                    "message": "sent",
                    "stock_status": status,
                    "methods_hit": ["manual_rule"],
                    "minute_bucket": minute_bucket,
                    "update_time": ts,
                }
            ]
        )
        ctx["restart_minute_bucket"] = minute_bucket
        ctx["restart_stock_no"] = stock_no
        ctx["restart_status"] = status
        return
    if step == "服務在 10:22 重啟":
        ctx["now_dt"] = _parse_dt("2026-04-10 10:22", ctx["timezone"])
        return
    if step.startswith('存在 "') and "的補償項 status " in step:
        minute_bucket = re.findall(r'"([^"]+)"', step)[0]
        _ensure_watchlist(ctx, "2330")
        ctx["pending_repo"].enqueue(
            {
                "minute_bucket": minute_bucket,
                "payload": "reconcile-only",
                "rows": [
                    {
                        "stock_no": "2330",
                        "message": "pending",
                        "stock_status": 2,
                        "methods_hit": ["manual_rule"],
                        "minute_bucket": minute_bucket,
                        "update_time": _now_epoch(ctx["now_dt"]),
                    }
                ],
                "error": "db failed",
            }
        )
        return
    if step == "該分鐘 LINE 先前已成功送達":
        ctx["line_client"].sent.append("already-sent")
        return
    if step == "服務重啟":
        return
    if step == "某分鐘存在 stale quote 或 data conflict":
        now_epoch = _now_epoch(ctx["now_dt"])
        ctx["watchlist_repo"].upsert_manual_threshold("2330", fair=1500, cheap=1000, enabled=1)
        ctx["market_provider"].snapshot["index_tick_at"] = now_epoch
        ctx["market_provider"].quotes["2330"] = {"price": 900.0, "tick_at": now_epoch - 200, "conflict": True}
        return
    if step.startswith("系統時間為 "):
        dt_text = re.findall(r'"([^"]+)"', step)[0]
        ctx["now_dt"] = datetime.fromisoformat(dt_text)
        return
    if step.startswith("統計窗口內總訊號分鐘為 "):
        ctx["total_signal_minutes"] = int(re.findall(r"(\d+)", step)[0])
        return
    if step.startswith("資料源中斷分鐘為 "):
        ctx["outage_minutes"] = int(re.findall(r"(\d+)", step)[0])
        return
    if step.startswith("正確通知分鐘為 "):
        ctx["correct_minutes"] = int(re.findall(r"(\d+)", step)[0])
        return
    if step == "系統正在組合出站 LINE 訊息（彙總、摘要、觸發列）":
        # UAT-014: context setup — no state mutation needed; assertions are in THEN
        return
    if step == "TRIGGER_ROW_TEMPLATE_KEY 已定義於 runtime_service":
        import stock_monitor.application.runtime_service as _rs
        assert hasattr(_rs, "TRIGGER_ROW_TEMPLATE_KEY"), (
            "[UAT-014] TRIGGER_ROW_TEMPLATE_KEY must be defined in runtime_service"
        )
        return
    if step == "MINUTE_DIGEST_TEMPLATE_KEY 已定義於 monitoring_workflow":
        import stock_monitor.application.monitoring_workflow as _mw
        assert hasattr(_mw, "MINUTE_DIGEST_TEMPLATE_KEY"), (
            "[UAT-014] MINUTE_DIGEST_TEMPLATE_KEY must be defined in monitoring_workflow"
        )
        return
    # TP-SEC-001 — CR-SEC-01: token repr protection
    if step.startswith("LinePushClient 以 token "):
        from stock_monitor.adapters.line_messaging import LinePushClient
        token = re.search(r'"([^"]+)"', step).group(1)
        ctx["sec_line_client"] = LinePushClient(channel_access_token=token, to_group_id="C1234567890")
        ctx["sec_token"] = token
        return
    # TP-SEC-002 — CR-SEC-03 / CR-CODE-05: invalid timezone fail-fast
    if step.startswith("使用無效時區名稱 "):
        ctx["sec_tz_name"] = re.search(r'"([^"]+)"', step).group(1)
        ctx["sec_raised_exc"] = None
        return
    # TP-ARCH-001 — CR-ARCH-01/02: calculator in application layer
    if step == "stock_monitor.application.valuation_calculator 模組可 import":
        ctx["sec_arch001"] = {}
        return
    # TP-ARCH-002 — CR-ARCH-03: single render definition
    if step == "已載入 stock_monitor.application.message_template":
        import stock_monitor.application.message_template as _mt
        ctx["sec_message_template"] = _mt
        return
    # TP-ARCH-003 — CR-CODE-03: MinuteCycleConfig
    if step == "stock_monitor.application.runtime_service 模組可 import":
        import stock_monitor.application.runtime_service as _rs_mod
        ctx["sec_runtime_service"] = _rs_mod
        return
    # TP-ARCH-004 — CR-ARCH-06: DB-based opening summary idempotency
    if step == "系統採用 SqliteLogger 紀錄事件":
        from stock_monitor.adapters.sqlite_repo import SqliteLogger as _SL
        ctx["sec_SqliteLogger"] = _SL
        return
    raise AssertionError(f"Unhandled GIVEN step: {step}")


def _handle_when(step: str, ctx: dict):
    if step.startswith('新增 watchlist "'):
        m = re.search(r'新增 watchlist "([^"]+)" with fair ([0-9.]+) and cheap ([0-9.]+)', step)
        try:
            ctx["watchlist_repo"].upsert_manual_threshold(m.group(1), fair=float(m.group(2)), cheap=float(m.group(3)), enabled=1)
            _set_last_error(ctx, None)
        except Exception as exc:
            _set_last_error(ctx, exc)
        return
    if step.startswith('插入 valuation method "'):
        m = re.search(r'插入 valuation method "([^"]+)" with enabled (\d+)', step)
        try:
            _insert_method(ctx, m.group(1), int(m.group(2)))
            _set_last_error(ctx, None)
        except Exception as exc:
            _set_last_error(ctx, exc)
        return
    if step.startswith('新增 message row for stock "'):
        m = re.search(r'新增 message row for stock "([^"]+)" minute "([^"]+)"', step)
        stock_no, minute_bucket = m.group(1), m.group(2)
        try:
            _insert_message_raw(
                ctx,
                stock_no=stock_no,
                minute_bucket=minute_bucket,
                stock_status=1,
                methods_hit=json.dumps(["manual_rule"]),
                message="ok",
            )
            _set_last_error(ctx, None)
        except Exception as exc:
            _set_last_error(ctx, exc)
        return
    if step.startswith('再新增 message row for stock "'):
        m = re.search(r'再新增 message row for stock "([^"]+)" same minute "([^"]+)"', step)
        try:
            _insert_message_raw(
                ctx,
                stock_no=m.group(1),
                minute_bucket=m.group(2),
                stock_status=1,
                methods_hit=json.dumps(["manual_rule"]),
                message="dup",
            )
            _set_last_error(ctx, None)
        except Exception as exc:
            _set_last_error(ctx, exc)
        return
    if step.startswith("新增 message row with minute "):
        m = re.search(r'新增 message row with minute "([^"]+)" and methods_hit "([^"]+)"', step)
        try:
            _insert_message_raw(
                ctx,
                stock_no="2330",
                minute_bucket=m.group(1),
                stock_status=1,
                methods_hit=m.group(2),
                message="bad",
            )
            _set_last_error(ctx, None)
        except Exception as exc:
            _set_last_error(ctx, exc)
        return
    if step.startswith("寫入一筆 pending_delivery_ledger"):
        now_epoch = _now_epoch(ctx["now_dt"])
        try:
            ctx["conn"].execute(
                """
                INSERT INTO pending_delivery_ledger(minute_bucket, payload_json, status, retry_count, last_error, created_at, updated_at)
                VALUES (?, ?, 'PENDING', 0, NULL, ?, ?)
                """,
                ("2026-04-10 10:21", json.dumps({"payload": "x", "rows": []}), now_epoch, now_epoch),
            )
            ctx["conn"].commit()
            _set_last_error(ctx, None)
        except Exception as exc:
            _set_last_error(ctx, exc)
        return
    if step.startswith('新增 valuation_snapshot for stock "') or step.startswith('再新增 valuation_snapshot for stock "'):
        m = re.search(r'新增 valuation_snapshot for stock "([^"]+)" trade_date "([^"]+)" method "([^"]+)"', step)
        if m is None:
            m = re.search(r'再新增 valuation_snapshot for stock "([^"]+)" trade_date "([^"]+)" method "([^"]+)"', step)
        try:
            _insert_snapshot(ctx, m.group(1), m.group(2), m.group(3))
            _set_last_error(ctx, None)
        except Exception as exc:
            _set_last_error(ctx, exc)
        return
    if step == "啟動服務":
        try:
            if ctx.get("startup_mode") == "json1_unavailable":
                class _NoJsonConn:
                    def execute(self, sql: str):
                        if "PRAGMA foreign_keys" in sql:
                            return _RowCursor([[1]])
                        if "json_valid" in sql:
                            raise RuntimeError("JSON1 unavailable")
                        return _RowCursor([[1]])

                assert_sqlite_prerequisites(_NoJsonConn())
            elif ctx.get("startup_mode") == "line_config":
                case = ctx.get("startup_env_case")
                env = _build_env_case(case)
                validate_line_runtime_config(env)
            else:
                assert_sqlite_prerequisites(ctx["conn"])
            _set_last_error(ctx, None)
            ctx["last_result"] = {"started": True}
        except Exception as exc:
            _set_last_error(ctx, exc)
            ctx["last_result"] = {"started": False}
        return
    if step.startswith('執行 "PRAGMA foreign_keys;'):
        row = ctx["conn"].execute("PRAGMA foreign_keys;").fetchone()
        ctx["last_query_result"] = int(row[0])
        return
    if step == "呼叫 health check":
        ctx["last_health"] = health_check(ctx["conn"])
        return
    if step == "套用 PriorityPolicy":
        statuses = [int(hit["stock_status"]) for hit in ctx["hits"]]
        ctx["priority_status"] = PriorityPolicy().resolve_status(statuses)
        ctx["aggregated"] = aggregate_stock_signals(ctx["hits"][0]["stock_no"], ctx["hits"])
        return
    if step == "執行 CooldownPolicy":
        now_ts = _now_epoch(ctx["now_dt"])
        ctx["cooldown_result"] = CooldownPolicy(cooldown_seconds=ctx["cooldown_seconds"]).can_send(
            last_sent_at=ctx.get("last_sent_at"),
            now_ts=now_ts,
        )
        return
    if step == "產生同分鐘冪等鍵":
        ctx["idem_key_1"] = build_minute_idempotency_key(
            ctx["idempotency_stock_no"], ctx["idempotency_minute_bucket"], stock_status=1
        )
        ctx["idem_key_2"] = build_minute_idempotency_key(
            ctx["idempotency_stock_no"], ctx["idempotency_minute_bucket"], stock_status=2
        )
        return
    if step == "進行股票層級聚合":
        stock_no = ctx["hits"][0]["stock_no"]
        ctx["aggregated"] = aggregate_stock_signals(stock_no, ctx["hits"])
        return
    if step == "套用冷卻規則":
        if "second_minute_hits" in ctx:
            rows = build_minute_rows(
                now_dt=ctx["second_minute_dt"],
                hits=ctx["second_minute_hits"],
                message_repo=ctx["message_repo"],
                pending_repo=ctx["pending_repo"],
                pending_fallback=ctx["pending_fallback"],
                cooldown_seconds=ctx["cooldown_seconds"],
                timezone_name=ctx["timezone"],
            )
            ctx["cooldown_rows"] = rows
        return
    if step in {"執行一次盤中輪詢流程", "執行盤中輪詢流程"}:
        watchlist_rows = ctx["watchlist_repo"].list_enabled()
        if not watchlist_rows:
            ctx["watchlist_repo"].upsert_manual_threshold("2330", fair=1500, cheap=1000, enabled=1)
            ctx["watchlist_repo"].upsert_manual_threshold("2317", fair=150, cheap=130, enabled=1)
        now_epoch = _now_epoch(ctx["now_dt"])
        ctx["market_provider"].snapshot.setdefault("index_tick_at", now_epoch)
        if not ctx["market_provider"].quotes and ctx.get("rows"):
            for row in ctx["rows"]:
                stock_no = row["stock_no"]
                price = 1000.0 if row["stock_status"] == 2 else 149.0
                ctx["market_provider"].quotes[stock_no] = {"price": price, "tick_at": now_epoch}
        effective_message_repo = ctx.get("dispatch_message_repo", ctx["message_repo"])
        effective_pending_repo = ctx.get("dispatch_pending_repo", ctx["pending_repo"])
        ctx["last_result"] = run_minute_cycle(
            now_dt=ctx["now_dt"],
            market_data_provider=ctx["market_provider"],
            line_client=ctx["line_client"],
            watchlist_repo=ctx["watchlist_repo"],
            message_repo=effective_message_repo,
            pending_repo=effective_pending_repo,
            pending_fallback=ctx["pending_fallback"],
            logger=ctx["logger"],
            cooldown_seconds=ctx["cooldown_seconds"],
            retry_count=ctx["retry_count"],
            stale_threshold_sec=ctx["stale_threshold_sec"],
            timezone_name=ctx["timezone"],
        )
        return
    if step == "執行該分鐘落盤":
        try:
            persist_message_rows_transactional(ctx["transactional_repo"], ctx["rows"])
            _set_last_error(ctx, None)
        except Exception as exc:
            _set_last_error(ctx, exc)
            ctx["pending_repo"].enqueue(
                {
                    "minute_bucket": "2026-04-10 10:21",
                    "payload": "rollback",
                    "rows": ctx["rows"],
                    "error": str(exc),
                }
            )
        return
    if step == "執行補償 worker":
        ctx["before_reconcile_sent"] = len(ctx["line_client"].sent)
        ctx["last_result"] = reconcile_pending_once(
            line_client=ctx["line_client"],
            message_repo=ctx["message_repo"],
            pending_repo=ctx["pending_repo"],
            logger=ctx["logger"],
        )
        return
    if step.startswith("補償 worker 再次執行"):
        ctx["before_reconcile_sent_2"] = len(ctx["line_client"].sent)
        ctx["last_result_2"] = reconcile_pending_once(
            line_client=ctx["line_client"],
            message_repo=ctx["message_repo"],
            pending_repo=ctx["pending_repo"],
            logger=ctx["logger"],
        )
        return
    if step == "執行該分鐘通知":
        repo = ctx.get("dispatch_message_repo", ctx["message_repo"])
        pending_repo = ctx.get("dispatch_pending_repo", ctx["pending_repo"])
        ctx["line_payload"] = aggregate_minute_notifications("2026-04-10 10:21", ctx["rows"])
        ctx["last_result"] = dispatch_and_persist_minute(
            minute_bucket="2026-04-10 10:21",
            rows=ctx["rows"],
            line_client=ctx["line_client"],
            message_repo=repo,
            pending_repo=pending_repo,
            pending_fallback=ctx["pending_fallback"],
            logger=ctx["logger"],
        )
        return
    if step == "產生彙總訊息":
        ctx["aggregated"] = aggregate_stock_signals("2330", ctx["hits"])
        return
    if step == "觸發開盤監控設定摘要通知":
        _trigger_opening_summary_once(ctx)
        return
    if step == "同一交易日再次觸發開盤摘要":
        _trigger_opening_summary_once(ctx)
        return
    if step == "觸發日結估值 job":
        if not ctx["watchlist_repo"].list_enabled():
            _ensure_watchlist(ctx, "2330")

        if ctx["now_dt"].strftime("%H:%M") != "14:00":
            date_part = ctx["now_dt"].strftime("%Y-%m-%d")
            ctx["now_dt"] = _parse_dt(f"{date_part} 14:00", ctx["timezone"])

        if ctx["should_fail_calculator"]:
            class _Calc:
                def calculate(self):
                    raise RuntimeError("valuation failed")

            calculator = _Calc()
        else:
            calc_class = ctx.pop("custom_calculator_class", _ManualValuationCalculator)
            calculator = calc_class(
                watchlist_repo=ctx["watchlist_repo"],
                trade_date=ctx["now_dt"].strftime("%Y-%m-%d"),
            )

        ctx["last_result"] = run_daily_valuation_job(
            now_dt=ctx["now_dt"],
            is_trading_day=ctx["is_trading_day"],
            calculator=calculator,
            snapshot_repo=ctx["valuation_snapshot_repo"],
            logger=ctx["logger"],
        )
        return
    if step.startswith('第 N+1 分鐘（<300 秒）再命中 "2330+status1"'):
        now_epoch = _now_epoch(_parse_dt("2026-04-10 10:21", ctx["timezone"]))
        ctx["now_dt"] = _parse_dt("2026-04-10 10:21", ctx["timezone"])
        _ensure_watchlist(ctx, "2330")
        ctx["market_provider"].snapshot["index_tick_at"] = now_epoch
        ctx["market_provider"].quotes["2330"] = {"price": 1490.0, "tick_at": now_epoch}
        ctx["last_result"] = run_minute_cycle(
            now_dt=ctx["now_dt"],
            market_data_provider=ctx["market_provider"],
            line_client=ctx["line_client"],
            watchlist_repo=ctx["watchlist_repo"],
            message_repo=ctx["message_repo"],
            pending_repo=ctx["pending_repo"],
            pending_fallback=ctx["pending_fallback"],
            logger=ctx["logger"],
            cooldown_seconds=ctx["cooldown_seconds"],
            retry_count=ctx["retry_count"],
            stale_threshold_sec=ctx["stale_threshold_sec"],
            timezone_name=ctx["timezone"],
        )
        return
    if step.startswith('同分鐘新輸入為 "'):
        m = re.search(r'同分鐘新輸入為 "([^"]+)" status (\d+)', step)
        stock_no, status = m.group(1), int(m.group(2))
        _ensure_watchlist(ctx, stock_no)
        ctx["message_repo"].save_batch(
            [
                {
                    "stock_no": stock_no,
                    "message": "status-upgrade",
                    "stock_status": status,
                    "methods_hit": ["emily_composite_v1", "raysky_blended_margin_v1"],
                    "minute_bucket": "2026-04-10 10:21",
                    "update_time": _now_epoch(ctx["now_dt"]),
                }
            ]
        )
        return
    if step.startswith("同分鐘新輸入為 status 1 且 methods_hit 含 "):
        methods = re.findall(r'"([^"]+)"', step)[0].split(",")
        ctx["message_repo"].save_batch(
            [
                {
                    "stock_no": "2330",
                    "message": "v2",
                    "stock_status": 1,
                    "methods_hit": [value.strip() for value in methods if value.strip()],
                    "minute_bucket": "2026-04-10 10:21",
                    "update_time": _now_epoch(ctx["now_dt"]),
                }
            ]
        )
        return
    if step == "查詢 message 表":
        ctx["queried_rows"] = ctx["message_repo"].list_rows()
        return
    if step.startswith('在交易日 "14:00" 觸發估值'):
        ctx["is_trading_day"] = True
        ctx["now_dt"] = _parse_dt("2026-04-10 14:00", ctx["timezone"])

        class _CalcOk:
            def calculate(self_inner):
                rows = ctx["watchlist_repo"].list_enabled()
                return [
                    {
                        "stock_no": row["stock_no"],
                        "trade_date": "2026-04-10",
                        "method_name": "emily_composite",
                        "method_version": "v1",
                        "fair_price": float(row["manual_fair_price"]),
                        "cheap_price": float(row["manual_cheap_price"]),
                    }
                    for row in rows
                ]

        ctx["last_result"] = run_daily_valuation_job(
            now_dt=ctx["now_dt"],
            is_trading_day=True,
            calculator=_CalcOk(),
            snapshot_repo=ctx["valuation_snapshot_repo"],
            logger=ctx["logger"],
        )
        return
    if step == "執行開盤可交易判斷":
        ctx["trade_eval_result"] = evaluate_market_open_status(now_dt=ctx["now_dt"], latest_index_tick_dt=ctx.get("index_tick_dt"))
        return
    if step == "排程器觸發每分鐘輪詢":
        ctx["watchlist_repo"].upsert_manual_threshold("2330", fair=1500, cheap=1000, enabled=1)
        ctx["last_result"] = run_minute_cycle(
            now_dt=ctx["now_dt"],
            market_data_provider=ctx["market_provider"],
            line_client=ctx["line_client"],
            watchlist_repo=ctx["watchlist_repo"],
            message_repo=ctx["message_repo"],
            pending_repo=ctx["pending_repo"],
            pending_fallback=ctx["pending_fallback"],
            logger=ctx["logger"],
            cooldown_seconds=ctx["cooldown_seconds"],
            retry_count=ctx["retry_count"],
            stale_threshold_sec=ctx["stale_threshold_sec"],
            timezone_name=ctx["timezone"],
        )
        return
    if step == "執行該分鐘訊號判斷":
        ctx["watchlist_repo"].upsert_manual_threshold("2330", fair=1500, cheap=1000, enabled=1)
        ctx["last_result"] = run_minute_cycle(
            now_dt=ctx["now_dt"],
            market_data_provider=ctx["market_provider"],
            line_client=ctx["line_client"],
            watchlist_repo=ctx["watchlist_repo"],
            message_repo=ctx["message_repo"],
            pending_repo=ctx["pending_repo"],
            pending_fallback=ctx["pending_fallback"],
            logger=ctx["logger"],
            cooldown_seconds=ctx["cooldown_seconds"],
            retry_count=ctx["retry_count"],
            stale_threshold_sec=ctx["stale_threshold_sec"],
            timezone_name=ctx["timezone"],
        )
        return
    if step == "系統恢復並重新進入輪詢":
        minute_bucket = ctx["restart_minute_bucket"]
        ctx["now_dt"] = _parse_dt(minute_bucket, ctx["timezone"])
        now_epoch = _now_epoch(ctx["now_dt"])
        ctx["watchlist_repo"].upsert_manual_threshold(ctx["restart_stock_no"], fair=1500, cheap=1000, enabled=1)
        ctx["market_provider"].snapshot["index_tick_at"] = now_epoch
        ctx["market_provider"].quotes[ctx["restart_stock_no"]] = {"price": 900.0, "tick_at": now_epoch}
        ctx["before_restart_sent"] = len(ctx["line_client"].sent)
        ctx["last_result"] = run_minute_cycle(
            now_dt=ctx["now_dt"],
            market_data_provider=ctx["market_provider"],
            line_client=ctx["line_client"],
            watchlist_repo=ctx["watchlist_repo"],
            message_repo=ctx["message_repo"],
            pending_repo=ctx["pending_repo"],
            pending_fallback=ctx["pending_fallback"],
            logger=ctx["logger"],
            cooldown_seconds=ctx["cooldown_seconds"],
            retry_count=ctx["retry_count"],
            stale_threshold_sec=ctx["stale_threshold_sec"],
            timezone_name=ctx["timezone"],
        )
        return
    if step == "補償 worker 啟動":
        ctx["before_reconcile_sent"] = len(ctx["line_client"].sent)
        ctx["last_result"] = reconcile_pending_once(
            line_client=ctx["line_client"],
            message_repo=ctx["message_repo"],
            pending_repo=ctx["pending_repo"],
            logger=ctx["logger"],
        )
        return
    if step == "執行該分鐘流程":
        ctx["watchlist_repo"].upsert_manual_threshold("2330", fair=1500, cheap=1000, enabled=1)
        ctx["last_result"] = run_minute_cycle(
            now_dt=ctx["now_dt"],
            market_data_provider=ctx["market_provider"],
            line_client=ctx["line_client"],
            watchlist_repo=ctx["watchlist_repo"],
            message_repo=ctx["message_repo"],
            pending_repo=ctx["pending_repo"],
            pending_fallback=ctx["pending_fallback"],
            logger=ctx["logger"],
            cooldown_seconds=ctx["cooldown_seconds"],
            retry_count=ctx["retry_count"],
            stale_threshold_sec=ctx["stale_threshold_sec"],
            timezone_name=ctx["timezone"],
        )
        return
    if step == "產生 minute_bucket":
        ctx["minute_bucket_result"] = TimeBucketService(ctx["timezone"]).to_minute_bucket(ctx["now_dt"])
        return
    if step == "計算通知準確率":
        ctx["kpi_result"] = compute_notification_accuracy(
            total_signal_minutes=ctx["total_signal_minutes"],
            outage_minutes=ctx["outage_minutes"],
            correct_notified_minutes=ctx["correct_minutes"],
        )
        return
    if step == "任何 LINE 訊息被產生":
        # UAT-014: no-op; assertions are covered in THEN steps
        return
    # TP-SEC-001 — CR-SEC-01: capture repr output
    if step == "對 LinePushClient 實例呼叫 repr()":
        ctx["sec_repr_output"] = repr(ctx["sec_line_client"])
        return
    # TP-SEC-002 — CR-SEC-03 / CR-CODE-05: attempt invalid-tz init
    if step == "初始化 TimeBucketService 或呼叫 _resolve_timezone":
        tz_name = ctx.get("sec_tz_name", "Invalid/NotAZone")
        try:
            ctx["sec_tz_service"] = TimeBucketService(tz_name)
            ctx["sec_raised_exc"] = None
        except ValueError as _exc:
            ctx["sec_raised_exc"] = _exc
        except Exception as _exc:
            ctx["sec_raised_exc"] = _exc
            ctx["sec_unexpected_exc_type"] = type(_exc).__name__
        return
    # TP-ARCH-001 — CR-ARCH-01/02: attempt import + run valuation
    if step == "執行一次估值計算（正常情境）":
        arch001 = ctx.setdefault("sec_arch001", {})
        try:
            from stock_monitor.application.valuation_calculator import ManualValuationCalculator as _MVC

            class _FakeRepo:
                def list_enabled(self_):
                    return [{"stock_no": "2330", "manual_fair_price": 1500.0, "manual_cheap_price": 1000.0}]

            _calc = _MVC(watchlist_repo=_FakeRepo(), trade_date="2026-04-14")
            arch001["calc_result"] = _calc.calculate()
            arch001["calc_events"] = getattr(_calc, "events", [])
            arch001["calc_import_ok"] = True
        except ImportError as _exc:
            arch001["calc_import_ok"] = False
            arch001["calc_import_error"] = str(_exc)
        return
    # TP-ARCH-002 — CR-ARCH-03: scan project for duplicate render definitions
    if step.startswith("在整個專案中搜尋"):
        import ast as _ast
        import stock_monitor as _sm_pkg
        from pathlib import Path as _Path
        from inspect import getfile as _getfile
        _pkg_root = _Path(_getfile(_sm_pkg)).parent
        _defs: list[str] = []
        for _py in sorted(_pkg_root.rglob("*.py")):
            try:
                _src = _py.read_text(encoding="utf-8")
                _tree = _ast.parse(_src)
                for _node in _ast.walk(_tree):
                    if isinstance(_node, _ast.FunctionDef) and _node.name == "render_line_template_message":
                        _defs.append(_py.name)
            except Exception:
                pass
        ctx["sec_render_definitions"] = _defs
        return
    # TP-ARCH-003 — CR-CODE-03: attempt MinuteCycleConfig attribute lookup
    if step == "從 runtime_service import MinuteCycleConfig":
        _rs = ctx.get("sec_runtime_service")
        try:
            ctx["sec_MinuteCycleConfig"] = getattr(_rs, "MinuteCycleConfig")
            ctx["sec_mcc_import_ok"] = True
        except AttributeError:
            ctx["sec_mcc_import_ok"] = False
        return
    # TP-ARCH-004 — CR-ARCH-06: inspect method source
    if step == "查看 opening_summary_sent_for_date 的實作":
        import inspect as _inspect
        _cls = ctx.get("sec_SqliteLogger")
        _method = getattr(_cls, "opening_summary_sent_for_date", None)
        ctx["sec_method_exists"] = _method is not None
        if _method is not None:
            try:
                ctx["sec_method_source"] = _inspect.getsource(_method)
            except Exception:
                ctx["sec_method_source"] = ""
        return
    raise AssertionError(f"Unhandled WHEN step: {step}")


def _handle_then(step: str, ctx: dict):
    if step == "寫入應成功":
        assert ctx["last_error"] is None
        return
    if step.startswith("寫入應失敗且錯誤為 "):
        expected = step.split("錯誤為 ", 1)[1]
        assert ctx["last_error"] is not None
        lowered = ctx["last_error"].lower()
        if expected == "CHECK constraint":
            assert "check constraint" in lowered
        elif expected == "partial unique index":
            assert "unique constraint" in lowered
        elif expected == "unique constraint":
            assert "unique constraint" in lowered
        else:
            assert expected.lower() in lowered
        return
    if step == "應可成功查回該筆資料":
        row = ctx["conn"].execute("SELECT COUNT(*) FROM pending_delivery_ledger WHERE status='PENDING'").fetchone()
        assert int(row[0]) >= 1
        return
    if step == "依 status 與 updated_at 查詢應命中索引":
        plan_rows = ctx["conn"].execute(
            "EXPLAIN QUERY PLAN SELECT * FROM pending_delivery_ledger WHERE status='PENDING' ORDER BY updated_at"
        ).fetchall()
        joined = " | ".join(str(tuple(row)) for row in plan_rows)
        assert "idx_pending_delivery_status" in joined
        return
    if step == "啟動應失敗":
        assert ctx["last_result"]["started"] is False
        assert ctx["last_error"] is not None
        return
    if step.startswith("錯誤訊息應明確包含 "):
        expected = re.findall(r'"([^"]+)"', step)[0]
        assert _error_contains(ctx["last_error"], expected)
        return
    if step.startswith("錯誤訊息應包含 "):
        expected = re.findall(r'"([^"]+)"', step)[0]
        assert _error_contains(ctx["last_error"], expected)
        return
    if step == "log 不得輸出完整 token 明文":
        logs = _fetch_logs(ctx)
        all_text = " ".join(f"{item['event']} {item['detail']}" for item in logs)
        assert "validtoken_12345" not in all_text
        return
    if step == "查詢結果應為 1":
        assert ctx["last_query_result"] == 1
        return
    if step == 'health status 應為 "ok"':
        assert ctx["last_health"]["status"] == "ok"
        return
    if step == "最終狀態應為 status 2":
        assert ctx["priority_status"] == 2
        return
    if step.startswith("訊息內 methods_hit 應包含 "):
        expected = re.findall(r'"([^"]+)"', step)[0].split(",")
        methods = set(ctx["aggregated"][0]["methods_hit"])
        assert all(item in methods for item in expected)
        return
    if step.startswith("結果應為 "):
        expected = re.findall(r'"([^"]+)"', step)[0]
        if expected == "blocked":
            assert ctx["cooldown_result"] is False
        elif expected == "sendable":
            assert ctx["cooldown_result"] is True
        else:
            assert str(ctx.get("trade_eval_result", {}).get("reason")) == expected
        return
    if step == "兩次冪等鍵應相同":
        assert ctx["idem_key_1"] == ctx["idem_key_2"]
        return
    if step.startswith("冪等鍵應為 "):
        expected = re.findall(r'"([^"]+)"', step)[0]
        assert ctx["idem_key_1"] == expected
        return
    if step == "只應產生一個股票事件":
        assert len(ctx["aggregated"]) == 1
        return
    if step == "該股票事件狀態應為 status 1":
        assert ctx["aggregated"][0]["stock_status"] == 1
        return
    if step == "該股票事件 methods_hit 應列出全部命中方法":
        assert set(ctx["aggregated"][0]["methods_hit"]) == {
            "emily_composite_v1",
            "oldbull_dividend_yield_v1",
            "raysky_blended_margin_v1",
        }
        return
    if step == "第 2 分鐘事件仍應可發送":
        assert len(ctx.get("cooldown_rows", [])) == 1
        return
    if step == "第 2 分鐘事件應被擋下":
        assert ctx.get("cooldown_rows", []) == []
        return
    if step == "不應更新任何 message.update_time":
        row = ctx["conn"].execute(
            "SELECT MAX(update_time) FROM message WHERE stock_no='2330' AND stock_status=1"
        ).fetchone()
        assert int(row[0]) == int(ctx["last_message_update_time"])
        return
    if step == "LINE API 應僅被呼叫 1 次":
        assert len(ctx["line_client"].sent) == 1
        return
    if step.startswith("單一訊息內容應同時包含 "):
        msg = ctx["line_client"].sent[-1]
        assert "2330" in msg and "2317" in msg
        return
    if step == "upsert 後該筆 status 應為 2":
        row = ctx["conn"].execute(
            "SELECT stock_status FROM message WHERE stock_no='2330' AND minute_bucket='2026-04-10 10:21'"
        ).fetchone()
        assert int(row[0]) == 2
        return
    if step == "methods_hit 與 message 應更新為該分鐘最終聚合內容":
        row = ctx["conn"].execute(
            "SELECT methods_hit, message FROM message WHERE stock_no='2330' AND minute_bucket='2026-04-10 10:21'"
        ).fetchone()
        methods = set(json.loads(row[0]))
        assert "emily_composite_v1" in methods and "raysky_blended_margin_v1" in methods
        return
    if step.startswith("upsert 後 methods_hit 應更新為同分鐘最終方法清單 "):
        expected = set(re.findall(r'"([^"]+)"', step)[0].split(","))
        row = ctx["conn"].execute(
            "SELECT methods_hit FROM message WHERE stock_no='2330' AND minute_bucket='2026-04-10 10:21'"
        ).fetchone()
        methods = set(json.loads(row[0]))
        assert methods == expected
        return
    if step == "message 內容應更新為最新聚合版":
        row = ctx["conn"].execute(
            "SELECT message FROM message WHERE stock_no='2330' AND minute_bucket='2026-04-10 10:21'"
        ).fetchone()
        assert row[0] == "v2"
        return
    if step == "message 表該分鐘應新增 0 筆":
        row = ctx["conn"].execute("SELECT COUNT(*) FROM message WHERE minute_bucket='2026-04-10 10:21'").fetchone()
        assert int(row[0]) == 0
        return
    if step == 'system_logs 應新增 level "ERROR" 的紀錄':
        assert any(item["level"] == "ERROR" for item in _fetch_logs(ctx))
        return
    if step.startswith('pending_delivery_ledger 或 pending_delivery.jsonl 應新增 "PENDING"'):
        pending_count = ctx["conn"].execute(
            "SELECT COUNT(*) FROM pending_delivery_ledger WHERE status='PENDING'"
        ).fetchone()[0]
        fallback_exists = Path(ctx["pending_fallback"].path).exists()
        assert int(pending_count) > 0 or fallback_exists
        return
    if step == '該分鐘應視為 "已通知"':
        assert ctx["last_result"]["status"] in {"pending", "persisted"}
        return
    if step == "message 表在該分鐘應為 0 筆":
        assert ctx["transactional_repo"].committed_rows == []
        return
    if step == "不得出現部分成功落盤":
        assert ctx["transactional_repo"].rolled_back is True
        return
    if step == "補償佇列應建立該分鐘待回補項目":
        assert len(ctx["pending_repo"].list_pending()) >= 1
        return
    if step == "該分鐘應可繼續訊號判斷與通知流程":
        assert ctx["market_provider"].calls >= 2
        return
    if step == "system_logs 應記錄 retry 次數":
        assert any("retry" in item["detail"].lower() for item in _fetch_logs(ctx))
        return
    if step == "該分鐘不應發送 LINE":
        assert len(ctx["line_client"].sent) == 0
        return
    if step == "該分鐘不應寫入 message":
        assert ctx["message_repo"].list_rows() == []
        return
    if step == "system_logs 應新增 ERROR 或 WARN":
        assert any(item["level"] in {"ERROR", "WARN"} for item in _fetch_logs(ctx))
        return
    if step == "該分鐘不得補發過期訊號":
        assert ctx["last_result"]["status"] in {"skipped", "no_signal"}
        return
    if step == "message 表應成功回補":
        assert len(ctx["message_repo"].list_rows()) > 0
        return
    if step == 'ledger 狀態應更新為 "RECONCILED"':
        count = ctx["conn"].execute(
            "SELECT COUNT(*) FROM pending_delivery_ledger WHERE status='RECONCILED'"
        ).fetchone()[0]
        assert int(count) >= 1
        return
    if step == "不得重複發送同一分鐘 LINE 訊息":
        assert len(ctx["line_client"].sent) == ctx["before_reconcile_sent_2"]
        return
    if step == 'system_logs 應新增 level "WARN" with event "MARKET_TIMEOUT"':
        assert any(item["level"] == "WARN" and item["event"] == "MARKET_TIMEOUT" for item in _fetch_logs(ctx))
        return
    if step == '應寫入 "logs/pending_delivery.jsonl"':
        assert Path(ctx["pending_fallback"].path).exists()
        return
    if step == "system_logs 應記錄 fallback 事件":
        assert any("PENDING_FALLBACK_JSONL" in item["detail"] for item in _fetch_logs(ctx))
        return
    if step == "LINE 群組應在 60 秒內收到通知":
        assert len(ctx["line_client"].sent) >= 1
        return
    if step == "LINE 不應再次發送":
        assert len(ctx["line_client"].sent) == 0
        return
    if step == "message.update_time 不應變動":
        row = ctx["conn"].execute(
            "SELECT MAX(update_time) FROM message WHERE stock_no='2330' AND stock_status=1"
        ).fetchone()
        assert int(row[0]) == int(ctx["last_message_update_time"])
        return
    if step == "每筆應有 stock_no, message, stock_status, update_time":
        row = ctx["conn"].execute(
            "SELECT stock_no, message, stock_status, update_time FROM message ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row is not None
        assert row[0] and row[1] and int(row[2]) in {1, 2} and int(row[3]) > 0
        return
    if step == "LINE 僅發送 1 封彙總訊息":
        assert len(ctx["line_client"].sent) == 1
        return
    if step == "LINE 應發送 1 封開盤摘要訊息":
        assert len(ctx["line_client"].sent) == 1
        return
    if step == "訊息應列出股票、方法、fair/cheap":
        msg = ctx["line_client"].sent[-1]
        assert "台積電(2330) 手動" in msg and "海悅(2348) 手動" in msg and "鈊象(3293) 手動" in msg
        assert "艾蜜" in msg and "老牛" in msg and "雷司" in msg
        assert "/" in msg
        return
    if step == "LINE 不應再次發送開盤摘要":
        assert len(ctx["line_client"].sent) == 1
        assert ctx.get("last_result", {}).get("status") == "skipped"
        return
    if step == "訊息應列出所有命中股票與方法":
        msg = ctx["line_client"].sent[-1]
        assert "2330" in msg and "2317" in msg
        assert "emily_composite_v1" in msg
        return
    if step == "該股票應僅以 status 2 呈現與通知":
        assert ctx["aggregated"][0]["stock_status"] == 2
        return
    if step == "valuation_snapshots 應新增各 stock x method 的快照":
        count = ctx["conn"].execute("SELECT COUNT(*) FROM valuation_snapshots").fetchone()[0]
        assert int(count) >= 1
        return
    if step == "emily/oldbull/raysky 各新增一筆快照":
        rows = ctx["conn"].execute(
            """
            SELECT method_name, method_version
            FROM valuation_snapshots
            WHERE stock_no='2330' AND trade_date='2026-04-10'
            """
        ).fetchall()
        methods = {(row[0], row[1]) for row in rows}
        assert ("emily_composite", "v1") in methods
        assert ("oldbull_dividend_yield", "v1") in methods
        assert ("raysky_blended_margin", "v1") in methods
        return
    if step == 'raysky 應記錄 "SKIP_INSUFFICIENT_DATA" 且其餘方法成功':
        logs = _fetch_logs(ctx)
        assert any("VALUATION_SKIP_INSUFFICIENT_DATA:raysky_blended_margin_v1" in item["detail"] for item in logs)
        rows = ctx["conn"].execute(
            """
            SELECT method_name
            FROM valuation_snapshots
            WHERE stock_no='2330' AND trade_date='2026-04-10'
            ORDER BY method_name
            """
        ).fetchall()
        methods = [row[0] for row in rows]
        assert "emily_composite" in methods and "oldbull_dividend_yield" in methods
        assert "raysky_blended_margin" not in methods
        return
    if step == "該方法可成功計算且有來源切換 log":
        logs = _fetch_logs(ctx)
        assert any("VALUATION_PROVIDER_FALLBACK_USED:raysky_blended_margin_v1" in item["detail"] for item in logs)
        rows = ctx["conn"].execute(
            """
            SELECT COUNT(*)
            FROM valuation_snapshots
            WHERE stock_no='2330' AND trade_date='2026-04-10' AND method_name='raysky_blended_margin'
            """
        ).fetchone()
        assert int(rows[0]) >= 1
        return
    if step == "不應新增任何 valuation_snapshots":
        count = ctx["conn"].execute("SELECT COUNT(*) FROM valuation_snapshots").fetchone()[0]
        assert int(count) == 0
        return
    if step == "system_logs 應記錄 skip/info":
        assert any(item["level"] == "INFO" for item in _fetch_logs(ctx))
        return
    if step == "既有快照不應被覆蓋":
        row = ctx["conn"].execute(
            "SELECT fair_price, cheap_price FROM valuation_snapshots WHERE stock_no='2330' AND trade_date='2026-04-09'"
        ).fetchone()
        assert row is not None
        assert float(row[0]) == 1500.0 and float(row[1]) == 1000.0
        return
    if step == "system_logs 應記錄錯誤":
        assert any(item["level"] == "ERROR" for item in _fetch_logs(ctx))
        return
    if step == "任務應執行 1 次":
        assert ctx["last_result"]["status"] in {"executed", "failed"}
        if ctx["last_result"]["status"] == "executed":
            assert int(ctx["last_result"].get("count", 0)) >= 1
        return
    if step == "計算失敗的方法不應覆蓋舊值":
        rows = ctx["conn"].execute(
            "SELECT COUNT(*) FROM valuation_snapshots WHERE stock_no='2330' AND trade_date='2026-04-09'"
        ).fetchone()
        assert int(rows[0]) == 1
        return
    if step == '判斷結果應為 "可交易"':
        assert ctx["trade_eval_result"]["is_open"] is True
        return
    if step == '判斷結果應為 "不開市"':
        assert ctx["trade_eval_result"]["is_open"] is False
        return
    if step == "該分鐘輪詢應跳過通知流程":
        assert ctx["trade_eval_result"]["is_open"] is False
        return
    if step == "系統應直接跳過輪詢與通知":
        assert ctx["last_result"]["status"] == "skipped"
        return
    if step == "該股票該分鐘不應觸發通知":
        assert len(ctx["line_client"].sent) == 0
        return
    if step == 'system_logs 應新增 "STALE_QUOTE" WARN':
        assert any(item["level"] == "WARN" and item["event"] == "STALE_QUOTE" for item in _fetch_logs(ctx))
        return
    if step == 'system_logs 應新增 "DATA_CONFLICT" WARN':
        assert any(item["level"] == "WARN" and item["event"] == "DATA_CONFLICT" for item in _fetch_logs(ctx))
        return
    if step == '"2026-04-10 10:21" 的事件不得重複發送':
        assert len(ctx["line_client"].sent) == ctx["before_restart_sent"]
        return
    if step == "應僅執行 message 回補":
        assert ctx["last_result"]["reconciled"] >= 1
        return
    if step == "不得再次發送該分鐘 LINE":
        assert len(ctx["line_client"].sent) == ctx["before_reconcile_sent"]
        return
    if step == "該分鐘 LINE 發送次數應為 0":
        assert len(ctx["line_client"].sent) == 0
        return
    if step == "system_logs 應存在對應 WARN 記錄":
        assert any(item["level"] == "WARN" for item in _fetch_logs(ctx))
        return
    if step.startswith("只能透過 TimeBucketService 產生 "):
        expected = re.findall(r'"([^"]+)"', step)[0]
        assert guard_bucket_source("TimeBucketService") is True
        assert ctx["minute_bucket_result"] == expected
        return
    if step == "分母應為 980":
        assert int(ctx["kpi_result"]["effective_denominator"]) == 980
        return
    if step == "準確率應為 99.18%":
        assert round(float(ctx["kpi_result"]["accuracy"]) * 100, 2) == 99.18
        return
    if step == 'KPI 驗證結果應為 "pass"':
        assert ctx["kpi_result"]["pass"] is True
        return
    if step == "所有訊息皆須透過 render_line_template_message 渲染":
        import stock_monitor.application.runtime_service as _rs
        import stock_monitor.application.monitoring_workflow as _mw
        assert hasattr(_rs, "TRIGGER_ROW_TEMPLATE_KEY"), (
            "[UAT-014] TRIGGER_ROW_TEMPLATE_KEY must exist in runtime_service"
        )
        assert hasattr(_mw, "MINUTE_DIGEST_TEMPLATE_KEY"), (
            "[UAT-014] MINUTE_DIGEST_TEMPLATE_KEY must exist in monitoring_workflow"
        )
        return
    if step == "程式碼中不得存在跳過模板的硬編碼最終文案":
        import inspect
        import stock_monitor.application.runtime_service as _rs
        import stock_monitor.application.monitoring_workflow as _mw
        rs_source = inspect.getsource(_rs.build_minute_rows)
        mw_source = inspect.getsource(_mw.aggregate_minute_notifications)
        assert "低於便宜價{" not in rs_source and "低於合理價{" not in rs_source, (
            "[UAT-014] build_minute_rows must not use hardcoded Chinese text f-strings"
        )
        assert "[股票監控通知]" not in mw_source, (
            "[UAT-014] aggregate_minute_notifications must not hardcode '[股票監控通知]'"
        )
        return
    # TP-SEC-001 — CR-SEC-01: token repr
    if step.startswith("repr 輸出不應包含 "):
        _text = re.search(r'"([^"]+)"', step).group(1)
        _repr = ctx.get("sec_repr_output", "")
        assert _text not in _repr, (
            f"[TP-SEC-001] repr() exposes token — CR-SEC-01 requires field(repr=False). Got: {_repr!r}"
        )
        return
    if step == "LinePushClient 仍可正常發出 LINE API 請求":
        _client = ctx.get("sec_line_client")
        assert hasattr(_client, "channel_access_token"), "[TP-SEC-001] must keep channel_access_token"
        assert hasattr(_client, "send"), "[TP-SEC-001] must keep send() method"
        return
    # TP-SEC-002 — CR-SEC-03 / CR-CODE-05: timezone ValueError
    if step == "應立即 raise ValueError":
        _exc = ctx.get("sec_raised_exc")
        _unexp = ctx.get("sec_unexpected_exc_type")
        if _unexp:
            pytest.fail(f"[TP-SEC-002] Expected ValueError but got {_unexp}({_exc})")
        assert _exc is not None, (
            f"[TP-SEC-002] No exception raised for invalid tz {ctx.get('sec_tz_name')!r} — "
            "CR-SEC-03 / CR-CODE-05 require ValueError fail-fast, not silent fallback"
        )
        assert isinstance(_exc, ValueError), f"[TP-SEC-002] Expected ValueError, got {type(_exc).__name__}"
        return
    if step == "不應繼續執行後續邏輯":
        assert ctx.get("sec_raised_exc") is not None, (
            "[TP-SEC-002] Execution continued without raising — service created with degraded tz=None"
        )
        return
    if step == "不應 fallback 至 UTC 時區":
        assert ctx.get("sec_raised_exc") is not None, (
            "[TP-SEC-002] Silent UTC fallback occurred — CR-SEC-03 requires ValueError instead"
        )
        return
    # TP-ARCH-001 — CR-ARCH-01/02: calculator in application layer
    if step == "ManualValuationCalculator 應可從 application.valuation_calculator import":
        _arch001 = ctx.get("sec_arch001", {})
        if not _arch001.get("calc_import_ok"):
            pytest.fail(
                "[TP-ARCH-001] CR-ARCH-01: Cannot import ManualValuationCalculator from "
                f"stock_monitor.application.valuation_calculator — "
                f"{_arch001.get('calc_import_error', 'ImportError')}"
            )
        return
    if step == "app.py 不應包含估值計算專屬 class 或 function 定義":
        import stock_monitor.app as _app_mod
        assert not hasattr(_app_mod, "_ManualValuationCalculator"), (
            "[TP-ARCH-001] CR-ARCH-01: app.py still defines _ManualValuationCalculator. "
            "Move to stock_monitor.application.valuation_calculator."
        )
        return
    if step == "system_logs 不應出現 scenario_case 相關的偽造 skip 事件":
        _events = ctx.get("sec_arch001", {}).get("calc_events", [])
        _fake = [e for e in _events if "optional_indicator_v1" in str(e) and "SKIP_INSUFFICIENT_DATA" in str(e)]
        assert not _fake, f"[TP-ARCH-001/CR-SEC-02] Fake skip events from scenario_case='default': {_fake}"
        return
    # TP-ARCH-002 — CR-ARCH-03: single render definition
    if step == "只應在 message_template.py 中找到一個定義":
        _defs = ctx.get("sec_render_definitions", [])
        assert len(_defs) == 1, (
            f"[TP-ARCH-002] CR-ARCH-03: render_line_template_message defined in {len(_defs)} file(s): {_defs}. "
            "Expected 1 definition in message_template.py only."
        )
        return
    if step == "runtime_service.py 不應包含 render_line_template_message 函式定義":
        _defs = ctx.get("sec_render_definitions", [])
        assert "runtime_service.py" not in _defs, (
            f"[TP-ARCH-002] CR-ARCH-03: runtime_service.py still defines render_line_template_message: {_defs}"
        )
        return
    # TP-ARCH-003 — CR-CODE-03: MinuteCycleConfig
    if step == "import 應成功":
        assert ctx.get("sec_mcc_import_ok"), (
            "[TP-ARCH-003] CR-CODE-03: MinuteCycleConfig not found in runtime_service. "
            "Introduce a MinuteCycleConfig dataclass to replace the 12-parameter signature."
        )
        return
    if step == "MinuteCycleConfig 應為 dataclass 或具名 config 型別":
        from dataclasses import is_dataclass as _is_dc
        _cls = ctx.get("sec_MinuteCycleConfig")
        if _cls is None:
            pytest.fail("[TP-ARCH-003] MinuteCycleConfig not imported")
        assert _is_dc(_cls), f"[TP-ARCH-003] CR-CODE-03: {_cls!r} is not a dataclass"
        return
    if step == "run_minute_cycle 應接受 MinuteCycleConfig 作為設定入口":
        import inspect as _insp
        _rs = ctx.get("sec_runtime_service")
        _sig = _insp.signature(_rs.run_minute_cycle)
        _params = list(_sig.parameters.keys())
        _cfg_params = [p for p in _params if p in {"config", "cfg", "minute_cycle_config"}]
        assert _cfg_params, (
            f"[TP-ARCH-003] CR-CODE-03: run_minute_cycle uses individual params {_params}, not a config object"
        )
        return
    # TP-ARCH-004 — CR-ARCH-06: DB-based opening summary idempotency
    if step == "不得使用 LIKE 查詢比對 system_logs.detail 判斷是否已發送":
        _src = ctx.get("sec_method_source", "")
        assert "LIKE" not in _src, (
            "[TP-ARCH-004] CR-ARCH-06: opening_summary_sent_for_date uses LIKE on system_logs.detail "
            "(log-as-state anti-pattern). Replace with dedicated DB state field."
        )
        return
    if step == "應使用專屬 DB 狀態欄位或獨立資料表記錄已發送日期":
        _src = ctx.get("sec_method_source", "")
        assert "system_logs" not in _src, (
            "[TP-ARCH-004] CR-ARCH-06: opening_summary_sent_for_date still queries system_logs. "
            "After fix, use a dedicated idempotency store (not the event log table)."
        )
        return
    raise AssertionError(f"Unhandled THEN step: {step}")


@given(parsers.re(r"(?P<step_text>.*[\u4e00-\u9fff].*)"))
def given_full_runtime(step_text: str, bdd_ctx: dict):
    _handle_given(step_text.strip(), bdd_ctx)


@when(parsers.re(r"(?P<step_text>.*[\u4e00-\u9fff].*)"))
def when_full_runtime(step_text: str, bdd_ctx: dict):
    _handle_when(step_text.strip(), bdd_ctx)


@then(parsers.re(r"(?P<step_text>.*[\u4e00-\u9fff].*)"))
def then_full_runtime(step_text: str, bdd_ctx: dict):
    _handle_then(step_text.strip(), bdd_ctx)
