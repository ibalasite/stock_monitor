"""Runtime orchestration for one-minute monitoring cycle."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from stock_monitor.application.monitoring_workflow import (
    dispatch_and_persist_minute,
    fetch_market_with_retry,
    reconcile_pending_once,
)
from stock_monitor.application.message_template import LineTemplateRenderer, render_line_template_message
from stock_monitor.application.trading_session import evaluate_market_open_status, is_in_trading_session
from stock_monitor.domain.policies import CooldownPolicy, aggregate_stock_signals
from stock_monitor.domain.time_bucket import TimeBucketService


@dataclass
class MinuteCycleConfig:
    """Configuration bundle for one-minute monitoring cycle (CR-CODE-03)."""

    now_dt: datetime
    market_data_provider: Any
    line_client: Any
    watchlist_repo: Any
    message_repo: Any
    pending_repo: Any
    pending_fallback: Any
    logger: Any
    valuation_snapshot_repo: Any = None
    cooldown_seconds: int = 300
    retry_count: int = 3
    stale_threshold_sec: int = 90
    timezone_name: str = "Asia/Taipei"

_BASELINE_OPENING_METHODS: tuple[tuple[str, str], ...] = (
    ("emily_composite", "v1"),
    ("oldbull_dividend_yield", "v1"),
    ("raysky_blended_margin", "v1"),
)
_OPENING_SUMMARY_ROW_TEMPLATE = "line_opening_summary_row_compact_v1"
TRIGGER_ROW_TEMPLATE_KEY = "line_trigger_row_v1"
TEST_PUSH_TEMPLATE_KEY = "line_test_push_v1"


def _format_price(value: float) -> str:
    text = f"{float(value):.2f}"
    if text.endswith(".00"):
        return text[:-3]
    return text.rstrip("0").rstrip(".")


def _to_epoch_seconds(dt: datetime) -> int:
    if dt.tzinfo is None:
        return int(dt.timestamp())
    return int(dt.astimezone(timezone.utc).timestamp())


def _method_key(method_name: str, method_version: str) -> str:
    return f"{str(method_name).strip()}_{str(method_version).strip()}"


def _method_label(method_key: str) -> str:
    normalized = str(method_key).strip()
    mapping = {
        "manual_rule": "手動",
        "emily_composite_v1": "艾蜜",
        "oldbull_dividend_yield_v1": "老牛",
        "raysky_blended_margin_v1": "雷司",
    }
    return mapping.get(normalized, normalized)


def _stock_display(stock_no: str, stock_name_map: dict[str, str]) -> str:
    name = str(stock_name_map.get(str(stock_no), "")).strip()
    if name:
        return f"{name}({stock_no})"
    return str(stock_no)


def _format_compact_price(value: str | float | int) -> str:
    try:
        return str(int(float(value)))
    except (TypeError, ValueError):
        return str(value)


def _build_opening_method_pairs(snapshot_rows: list[dict]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = list(_BASELINE_OPENING_METHODS)
    known = {_method_key(name, version) for name, version in pairs}
    for row in snapshot_rows:
        name = str(row.get("method_name") or "").strip()
        version = str(row.get("method_version") or "").strip()
        if not name or not version:
            continue
        key = _method_key(name, version)
        if key in known:
            continue
        pairs.append((name, version))
        known.add(key)
    return pairs


def _build_opening_summary_message(
    *,
    trade_date: str,
    watchlist_rows: list[dict],
    method_pairs: list[tuple[str, str]],
    snapshot_rows: list[dict],
    stock_name_map: dict[str, str] | None = None,
) -> str:
    stock_name_map = dict(stock_name_map or {})
    watchlist_sorted = sorted(watchlist_rows, key=lambda item: str(item["stock_no"]))
    rows: list[str] = []

    snapshot_map = {
        (str(item["stock_no"]), _method_key(str(item["method_name"]), str(item["method_version"]))): item
        for item in snapshot_rows
    }

    for row in watchlist_sorted:
        stock_no = str(row["stock_no"])
        display_stock = _stock_display(stock_no, stock_name_map)
        manual_fair = _format_compact_price(row["manual_fair_price"])
        manual_cheap = _format_compact_price(row["manual_cheap_price"])
        rows.append(
            render_line_template_message(
                _OPENING_SUMMARY_ROW_TEMPLATE,
                {
                    "stock_display": display_stock,
                    "method_label": _method_label("manual_rule"),
                    "fair_price": manual_fair,
                    "cheap_price": manual_cheap,
                },
            )
        )

        for method_name, method_version in method_pairs:
            key = _method_key(method_name, method_version)
            method_label = _method_label(key)
            snapshot = snapshot_map.get((stock_no, key))
            if snapshot is None:
                rows.append(
                    render_line_template_message(
                        _OPENING_SUMMARY_ROW_TEMPLATE,
                        {
                            "stock_display": display_stock,
                            "method_label": method_label,
                            "fair_price": "N/A",
                            "cheap_price": "N/A",
                        },
                    )
                )
                continue
            rows.append(
                render_line_template_message(
                    _OPENING_SUMMARY_ROW_TEMPLATE,
                    {
                        "stock_display": display_stock,
                        "method_label": method_label,
                        "fair_price": _format_compact_price(snapshot["fair_price"]),
                        "cheap_price": _format_compact_price(snapshot["cheap_price"]),
                    },
                )
            )

    if not rows:
        rows.append("無監控資料")
    return "\n".join(rows)


def _already_sent_opening_summary(logger, trade_date: str) -> bool:
    if hasattr(logger, "opening_summary_sent_for_date"):
        try:
            return bool(logger.opening_summary_sent_for_date(trade_date))
        except Exception:
            return False
    return False


def _send_opening_summary_if_needed(
    *,
    now_dt: datetime,
    watchlist_rows: list[dict],
    valuation_snapshot_repo,
    line_client,
    logger,
    stock_name_map: dict[str, str] | None = None,
) -> None:
    trade_date = now_dt.strftime("%Y-%m-%d")
    if _already_sent_opening_summary(logger, trade_date):
        return

    snapshot_rows: list[dict] = []
    if valuation_snapshot_repo is not None and hasattr(valuation_snapshot_repo, "list_latest_snapshots"):
        try:
            snapshot_rows = valuation_snapshot_repo.list_latest_snapshots(
                stock_nos=[str(item["stock_no"]) for item in watchlist_rows],
                as_of_date=trade_date,
            )
        except Exception as exc:
            logger.log("WARN", f"OPENING_SUMMARY_SNAPSHOT_FETCH_FAILED: {exc}")

    payload = _build_opening_summary_message(
        trade_date=trade_date,
        watchlist_rows=watchlist_rows,
        method_pairs=_build_opening_method_pairs(snapshot_rows),
        snapshot_rows=snapshot_rows,
        stock_name_map=stock_name_map,
    )
    try:
        line_client.send(payload)
        logger.log("INFO", f"OPENING_SUMMARY_SENT:date={trade_date}")
        if hasattr(logger, "mark_opening_summary_sent"):
            try:
                logger.mark_opening_summary_sent(trade_date)
            except Exception:
                pass
    except Exception as exc:
        logger.log("ERROR", f"OPENING_SUMMARY_SEND_FAILED: {exc}")


def evaluate_manual_threshold_hits(watchlist_rows: list[dict], quotes: dict[str, dict]) -> list[dict]:
    hits: list[dict] = []
    for row in watchlist_rows:
        stock_no = str(row["stock_no"])
        quote = quotes.get(stock_no)
        if not quote:
            continue
        price = float(quote["price"])
        fair_price = float(row["manual_fair_price"])
        cheap_price = float(row["manual_cheap_price"])
        if price <= cheap_price:
            status = 2
        elif price <= fair_price:
            status = 1
        else:
            continue  # pragma: no cover
        hits.append(
            {
                "stock_no": stock_no,
                "stock_status": status,
                "method": "manual_rule",
                "price": price,
                "stock_name": str(row.get("stock_name") or "").strip(),
                "fair_price": fair_price,
                "cheap_price": cheap_price,
            }
        )
    return hits


def evaluate_valuation_snapshot_hits(
    snapshot_rows: list[dict],
    quotes: dict[str, dict],
    stock_name_map: dict[str, str] | None = None,
) -> list[dict]:
    hits: list[dict] = []
    for row in snapshot_rows:
        stock_no = str(row["stock_no"])
        quote = quotes.get(stock_no)
        if not quote:
            continue

        price = float(quote["price"])
        fair_price = float(row["fair_price"])
        cheap_price = float(row["cheap_price"])
        if price <= cheap_price:
            status = 2
        elif price <= fair_price:
            status = 1
        else:
            continue  # pragma: no cover

        method_name = str(row["method_name"]).strip()
        method_version = str(row["method_version"]).strip()
        method = f"{method_name}_{method_version}" if method_version else method_name
        hits.append(
            {
                "stock_no": stock_no,
                "stock_status": status,
                "method": method,
                "price": price,
                "stock_name": str((stock_name_map or {}).get(stock_no) or "").strip(),
                "fair_price": fair_price,
                "cheap_price": cheap_price,
            }
        )
    return hits


def build_minute_rows(
    now_dt: datetime,
    hits: list[dict],
    message_repo,
    pending_repo,
    pending_fallback,
    cooldown_seconds: int,
    timezone_name: str = "Asia/Taipei",
    stock_name_map: dict[str, str] | None = None,
) -> list[dict]:
    if not hits:
        return []
    grouped: dict[str, list[dict]] = defaultdict(list)
    for hit in hits:
        grouped[hit["stock_no"]].append(hit)

    now_epoch = _to_epoch_seconds(now_dt)
    minute_bucket = TimeBucketService(timezone_name).to_minute_bucket(now_dt)
    cooldown = CooldownPolicy(cooldown_seconds=cooldown_seconds)

    rows: list[dict] = []
    for stock_no, stock_hits in grouped.items():
        aggregated = aggregate_stock_signals(stock_no, stock_hits)
        if not aggregated:
            continue
        event = aggregated[0]
        status = int(event["stock_status"])
        sent_at_candidates = [
            message_repo.get_last_sent_at(stock_no, status),
        ]
        if hasattr(pending_repo, "get_last_pending_sent_at"):
            sent_at_candidates.append(pending_repo.get_last_pending_sent_at(stock_no, status))
        if hasattr(pending_fallback, "get_last_pending_sent_at"):
            sent_at_candidates.append(pending_fallback.get_last_pending_sent_at(stock_no, status))

        known_sent_times = [int(ts) for ts in sent_at_candidates if ts is not None]
        effective_last_sent_at = max(known_sent_times) if known_sent_times else None

        if not cooldown.can_send(last_sent_at=effective_last_sent_at, now_ts=now_epoch):
            continue

        prices = sorted({float(hit["price"]) for hit in stock_hits})
        # FR-18: stock name from DB stock_name_map takes priority; fall back to hit stock_name
        stock_name = str((stock_name_map or {}).get(stock_no) or "").strip()
        if not stock_name:
            for hit in stock_hits:
                candidate = str(hit.get("stock_name") or "").strip()
                if candidate:
                    stock_name = candidate
                    break
        fair_price = next((float(hit["fair_price"]) for hit in stock_hits if hit.get("fair_price") is not None), None)
        cheap_price = next((float(hit["cheap_price"]) for hit in stock_hits if hit.get("cheap_price") is not None), None)
        display_label = f"{stock_name}({stock_no})" if stock_name else stock_no
        current_price = prices[0]

        render_context = {
                    "display_label": display_label,
                    "current_price": _format_price(current_price),
                    "stock_status": status,
                    "fair_price": _format_price(fair_price) if fair_price is not None else None,
                    "cheap_price": _format_price(cheap_price) if (status == 2 and cheap_price is not None) else None,
                }
        message = render_line_template_message(TRIGGER_ROW_TEMPLATE_KEY, render_context)

        rows.append(
            {
                "stock_no": stock_no,
                "stock_status": status,
                "methods_hit": event.get("methods_hit", []),
                "minute_bucket": minute_bucket,
                "update_time": now_epoch,
                "message": message,
                "stock_name": stock_name,
                "current_price": current_price,
                "fair_price": fair_price,
                "cheap_price": cheap_price,
            }
        )
    return rows


def run_minute_cycle(
    *,
    config: MinuteCycleConfig | None = None,
    now_dt: datetime | None = None,
    market_data_provider=None,
    line_client=None,
    watchlist_repo=None,
    message_repo=None,
    pending_repo=None,
    valuation_snapshot_repo=None,
    pending_fallback=None,
    logger=None,
    cooldown_seconds: int = 300,
    retry_count: int = 3,
    stale_threshold_sec: int = 90,
    timezone_name: str = "Asia/Taipei",
) -> dict:
    # Unpack config object if provided (CR-CODE-03)
    if config is not None:
        now_dt = config.now_dt
        market_data_provider = config.market_data_provider
        line_client = config.line_client
        watchlist_repo = config.watchlist_repo
        message_repo = config.message_repo
        pending_repo = config.pending_repo
        valuation_snapshot_repo = config.valuation_snapshot_repo
        pending_fallback = config.pending_fallback
        logger = config.logger
        cooldown_seconds = config.cooldown_seconds
        retry_count = config.retry_count
        stale_threshold_sec = config.stale_threshold_sec
        timezone_name = config.timezone_name
    if not is_in_trading_session(now_dt):
        logger.log("INFO", "SKIP_NON_TRADING_SESSION")
        return {"status": "skipped", "reason": "non_trading_session"}

    now_epoch = _to_epoch_seconds(now_dt)
    fetched = fetch_market_with_retry(
        now_epoch=now_epoch,
        market_data_provider=market_data_provider,
        max_retries=retry_count,
        logger=logger,
    )
    if not fetched.get("ok"):
        return {"status": "skipped", "reason": "market_fetch_failed"}

    snapshot = fetched["snapshot"]
    index_tick_at = int(snapshot.get("index_tick_at", now_epoch))
    latest_index_tick_dt = datetime.fromtimestamp(index_tick_at, tz=now_dt.tzinfo)
    market_status = evaluate_market_open_status(now_dt=now_dt, latest_index_tick_dt=latest_index_tick_dt)
    if not market_status.get("is_open"):
        logger.log("INFO", f"SKIP_MARKET_CLOSED:{market_status.get('reason')}")
        return {"status": "skipped", "reason": market_status.get("reason")}

    watchlist_rows = watchlist_repo.list_enabled()
    if not watchlist_rows:
        logger.log("INFO", "SKIP_EMPTY_WATCHLIST")
        return {"status": "skipped", "reason": "empty_watchlist"}

    stock_nos = [str(row["stock_no"]) for row in watchlist_rows]
    quotes = market_data_provider.get_realtime_quotes(stock_nos)
    # FR-18: stock names come from DB (watchlist.stock_name), not from real-time quotes
    stock_name_map = {
        str(row["stock_no"]): str(row.get("stock_name") or "").strip()
        for row in watchlist_rows
    }

    _send_opening_summary_if_needed(
        now_dt=now_dt,
        watchlist_rows=watchlist_rows,
        valuation_snapshot_repo=valuation_snapshot_repo,
        line_client=line_client,
        logger=logger,
        stock_name_map=stock_name_map,
    )
    filtered_quotes: dict[str, dict] = {}
    for stock_no, quote in quotes.items():
        if bool(quote.get("conflict")):
            logger.log("WARN", f"DATA_CONFLICT:{stock_no}")
            continue

        try:
            tick_at = int(quote.get("tick_at"))
        except (TypeError, ValueError):
            tick_at = 0

        if tick_at <= 0 or (now_epoch - tick_at) > stale_threshold_sec:
            logger.log("WARN", f"STALE_QUOTE:{stock_no}")
            continue

        filtered_quotes[stock_no] = quote

    hits = evaluate_manual_threshold_hits(watchlist_rows=watchlist_rows, quotes=filtered_quotes)
    if valuation_snapshot_repo is not None and hasattr(valuation_snapshot_repo, "list_latest_snapshots"):
        try:
            snapshot_rows = valuation_snapshot_repo.list_latest_snapshots(
                stock_nos=stock_nos,
                as_of_date=now_dt.strftime("%Y-%m-%d"),
            )
            hits.extend(evaluate_valuation_snapshot_hits(
                snapshot_rows=snapshot_rows,
                quotes=filtered_quotes,
                stock_name_map=stock_name_map,
            ))
        except Exception as exc:
            logger.log("WARN", f"VALUATION_SNAPSHOT_FETCH_FAILED: {exc}")

    rows = build_minute_rows(
        now_dt=now_dt,
        hits=hits,
        message_repo=message_repo,
        pending_repo=pending_repo,
        pending_fallback=pending_fallback,
        cooldown_seconds=cooldown_seconds,
        timezone_name=timezone_name,
        stock_name_map=stock_name_map,
    )
    if not rows:
        return {"status": "no_signal", "count": 0}

    minute_bucket = rows[0]["minute_bucket"]
    dispatch_result = dispatch_and_persist_minute(
        minute_bucket=minute_bucket,
        rows=rows,
        line_client=line_client,
        message_repo=message_repo,
        pending_repo=pending_repo,
        pending_fallback=pending_fallback,
        logger=logger,
    )
    return {"status": dispatch_result.get("status"), "count": len(rows)}


def run_reconcile_cycle(*, message_repo, pending_repo, logger) -> dict:
    return reconcile_pending_once(
        message_repo=message_repo,
        pending_repo=pending_repo,
        logger=logger,
    )
