"""Runtime orchestration for one-minute monitoring cycle."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from stock_monitor.application.monitoring_workflow import (
    dispatch_and_persist_minute,
    fetch_market_with_retry,
    reconcile_pending_once,
)
from stock_monitor.application.trading_session import evaluate_market_open_status, is_in_trading_session
from stock_monitor.domain.policies import CooldownPolicy, aggregate_stock_signals
from stock_monitor.domain.time_bucket import TimeBucketService


def _to_epoch_seconds(dt: datetime) -> int:
    if dt.tzinfo is None:
        return int(dt.timestamp())
    return int(dt.astimezone(timezone.utc).timestamp())


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
            continue
        hits.append(
            {
                "stock_no": stock_no,
                "stock_status": status,
                "method": "manual_rule",
                "price": price,
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
        rows.append(
            {
                "stock_no": stock_no,
                "stock_status": status,
                "methods_hit": event.get("methods_hit", []),
                "minute_bucket": minute_bucket,
                "update_time": now_epoch,
                "message": f"{stock_no} status={status} price={prices[0]:.2f}",
            }
        )
    return rows


def run_minute_cycle(
    *,
    now_dt: datetime,
    market_data_provider,
    line_client,
    watchlist_repo,
    message_repo,
    pending_repo,
    pending_fallback,
    logger,
    cooldown_seconds: int = 300,
    retry_count: int = 3,
    stale_threshold_sec: int = 90,
    timezone_name: str = "Asia/Taipei",
) -> dict:
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
    rows = build_minute_rows(
        now_dt=now_dt,
        hits=hits,
        message_repo=message_repo,
        pending_repo=pending_repo,
        pending_fallback=pending_fallback,
        cooldown_seconds=cooldown_seconds,
        timezone_name=timezone_name,
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


def run_reconcile_cycle(*, line_client, message_repo, pending_repo, logger) -> dict:
    return reconcile_pending_once(
        line_client=line_client,
        message_repo=message_repo,
        pending_repo=pending_repo,
        logger=logger,
    )
