"""Trading session rules."""

from __future__ import annotations

from datetime import datetime, time


TRADING_START = time(9, 0)
TRADING_END = time(13, 30)
OPEN_CHECK_START = time(8, 45)


def is_in_trading_session(now_dt: datetime) -> bool:
    if now_dt.weekday() >= 5:
        return False
    current = now_dt.time()
    return TRADING_START <= current <= TRADING_END


def evaluate_market_open_status(now_dt: datetime, latest_index_tick_dt: datetime | None) -> dict:
    if now_dt.weekday() >= 5:
        return {"is_open": False, "reason": "weekend"}

    now_time = now_dt.time()
    if now_time < OPEN_CHECK_START:
        return {"is_open": False, "reason": "before_open_check"}
    if now_time > TRADING_END:
        return {"is_open": False, "reason": "after_close"}

    has_same_day_tick = (
        latest_index_tick_dt is not None
        and latest_index_tick_dt.date() == now_dt.date()
    )

    if now_time >= TRADING_START and not has_same_day_tick:
        return {"is_open": False, "reason": "no_index_update_after_open"}

    return {"is_open": has_same_day_tick, "reason": "open" if has_same_day_tick else "waiting_index"}

