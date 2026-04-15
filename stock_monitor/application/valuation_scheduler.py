"""Daily valuation scheduler helper."""

from __future__ import annotations


def _iter_calculation_events(calculator) -> list[tuple[str, str]]:
    raw_events = getattr(calculator, "events", None)
    if raw_events is None and hasattr(calculator, "get_events"):
        raw_events = calculator.get_events()
    if not raw_events:
        return []

    normalized: list[tuple[str, str]] = []
    for item in raw_events:
        if isinstance(item, tuple) and len(item) >= 2:
            level, message = item[0], item[1]
        else:
            level, message = "INFO", item
        normalized.append((str(level).upper(), str(message)))
    return normalized


def run_daily_valuation_job(
    now_dt,
    is_trading_day: bool,
    calculator,
    snapshot_repo,
    logger,
    watchlist_repo=None,
    market_data_provider=None,
) -> dict:
    if not is_trading_day:
        logger.log("INFO", "VALUATION_SKIPPED_NON_TRADING_DAY")
        return {"status": "skipped"}

    if now_dt.strftime("%H:%M") != "14:00":
        logger.log("INFO", "VALUATION_SKIPPED_NOT_SCHEDULED_TIME")
        return {"status": "skipped"}

    try:
        snapshots = calculator.calculate()
    except Exception as exc:
        logger.log("ERROR", f"VALUATION_FAILED: {exc}")
        return {"status": "failed"}

    for level, message in _iter_calculation_events(calculator):
        logger.log(level, message)

    try:
        snapshot_repo.save_snapshots(snapshots)
    except Exception as exc:
        logger.log("ERROR", f"VALUATION_PERSIST_FAILED: {exc}")
        return {"status": "failed"}

    logger.log("INFO", f"VALUATION_EXECUTED: {len(snapshots)}")

    # FR-18: save stock Chinese names from market data into watchlist
    if watchlist_repo is not None and market_data_provider is not None:
        try:
            wl_rows = watchlist_repo.list_enabled()
            stock_nos = [str(r["stock_no"]) for r in wl_rows]
            # Prefer dedicated get_stock_names() (adapters cache names during get_realtime_quotes).
            # Fall back to reading 'name' from quotes (backward compat with test fakes).
            if hasattr(market_data_provider, "get_stock_names"):
                names = market_data_provider.get_stock_names(stock_nos)
            else:
                quotes = market_data_provider.get_realtime_quotes(stock_nos)
                names = {
                    stock_no: str((q or {}).get("name") or "").strip()
                    for stock_no, q in quotes.items()
                    if str((q or {}).get("name") or "").strip()
                }
            if names:
                watchlist_repo.update_stock_names(names)
        except Exception as exc:
            logger.log("WARN", f"STOCK_NAME_SAVE_FAILED: {exc}")

    return {"status": "executed", "count": len(snapshots)}

