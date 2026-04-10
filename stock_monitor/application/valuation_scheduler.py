"""Daily valuation scheduler helper."""

from __future__ import annotations


def run_daily_valuation_job(
    now_dt,
    is_trading_day: bool,
    calculator,
    snapshot_repo,
    logger,
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

    snapshot_repo.save_snapshots(snapshots)
    logger.log("INFO", f"VALUATION_EXECUTED: {len(snapshots)}")
    return {"status": "executed", "count": len(snapshots)}

