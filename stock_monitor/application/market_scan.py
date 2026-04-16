"""FR-19 全市場估值掃描 Use Case.

Orchestrates:
1. Fetch all listed stocks via AllListedStocksPort.
2. For each stock, run all enabled valuation methods.
3. Aggregate SUCCESS results → agg_fair / agg_cheap (arithmetic mean).
4. Classify into three buckets:
   - below_cheap   → upsert watchlist (enabled=1 for NEW stocks only)
   - near_fair     → write scan_results_above_cheap.csv
   - uncalculable  → write scan_results_uncalculable.csv
5. Return MarketScanResult summary.

EDD §14.3 / PDD FR-19.
"""

from __future__ import annotations

import csv
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from stock_monitor.adapters.sqlite_repo import apply_schema, connect_sqlite

_CSV_FIELDNAMES = [
    "stock_no",
    "stock_name",
    "agg_fair_price",
    "agg_cheap_price",
    "yesterday_close",
    "methods_success",
    "methods_skipped",
]


@dataclass
class MarketScanResult:
    """Summary returned by run_market_scan_job."""

    scan_date: str          # YYYY-MM-DD
    total_stocks: int
    watchlist_upserted: int
    near_fair_count: int
    uncalculable_count: int
    output_dir: str


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _upsert_watchlist(conn, stock_no: str, stock_name: str, fair: float, cheap: float) -> None:
    """Insert or update watchlist entry.

    For NEW stocks: enabled defaults to 1.
    For EXISTING stocks: only update stock_name/fair/cheap; do NOT touch enabled.
    EDD §14.3 Watchlist Upsert SQL contract.
    """
    now_epoch = int(time.time())
    conn.execute(
        """
        INSERT INTO watchlist
            (stock_no, stock_name, manual_fair_price, manual_cheap_price, enabled, created_at, updated_at)
        VALUES (?, ?, ?, ?, 1, ?, ?)
        ON CONFLICT(stock_no) DO UPDATE SET
            stock_name = excluded.stock_name,
            manual_fair_price = excluded.manual_fair_price,
            manual_cheap_price = excluded.manual_cheap_price,
            updated_at = excluded.updated_at
        """,
        (stock_no, stock_name, fair, cheap, now_epoch, now_epoch),
    )


def _write_system_log(conn, level: str, event: str, detail: str) -> None:
    now_epoch = int(time.time())
    conn.execute(
        "INSERT INTO system_logs (level, event, detail, created_at) VALUES (?, ?, ?, ?)",
        (level, event, detail, now_epoch),
    )


def run_market_scan_job(
    db_path: str,
    output_dir: str,
    stocks_provider,
    valuation_methods: list,
) -> MarketScanResult:
    """Run the full market-wide valuation scan.

    Parameters
    ----------
    db_path:
        Path to the SQLite database file.
    output_dir:
        Directory for CSV output files. Created if it does not exist.
    stocks_provider:
        An object implementing AllListedStocksPort.get_all_listed_stocks().
    valuation_methods:
        List of method objects implementing compute(stock_no, trade_date_local) -> dict.
        Each result dict must contain: status, fair_price, cheap_price, method_name.

    Returns
    -------
    MarketScanResult
    """
    scan_date = date.today().isoformat()
    conn = connect_sqlite(db_path)
    apply_schema(conn)

    stocks = stocks_provider.get_all_listed_stocks()

    near_fair_rows: list[dict] = []
    uncalculable_rows: list[dict] = []
    watchlist_upserted = 0

    for stock in stocks:
        stock_no: str = stock["stock_no"]
        stock_name: str = stock["stock_name"]
        close: float | None = stock.get("yesterday_close")

        success_fairs: list[float] = []
        success_cheaps: list[float] = []
        methods_success: list[str] = []
        methods_skipped: list[str] = []

        for method in valuation_methods:
            try:
                result = method.compute(stock_no, scan_date)
            except Exception as exc:
                _write_system_log(
                    conn,
                    "ERROR",
                    "MARKET_SCAN_STOCK_ERROR",
                    f"stock_no={stock_no} method={getattr(method, 'method_name', '?')} error={exc}",
                )
                conn.commit()
                continue

            status = result.get("status", "")
            method_label = f"{result.get('method_name', getattr(method, 'method_name', '?'))}_{result.get('method_version', getattr(method, 'method_version', 'v1'))}"

            if status == "SUCCESS":
                fair = result.get("fair_price")
                cheap = result.get("cheap_price")
                if fair is not None and cheap is not None:
                    success_fairs.append(float(fair))
                    success_cheaps.append(float(cheap))
                    methods_success.append(method_label)
            else:
                methods_skipped.append(f"{method_label}:{status}")

        # Classification
        if close is None:
            # No price data → uncalculable
            uncalculable_rows.append({
                "stock_no": stock_no,
                "stock_name": stock_name,
                "agg_fair_price": "",
                "agg_cheap_price": "",
                "yesterday_close": "",
                "methods_success": "|".join(methods_success),
                "methods_skipped": "|".join(methods_skipped) or "NO_PRICE",
            })
            continue

        if not success_fairs:
            # All methods skipped → uncalculable
            uncalculable_rows.append({
                "stock_no": stock_no,
                "stock_name": stock_name,
                "agg_fair_price": "",
                "agg_cheap_price": "",
                "yesterday_close": str(close),
                "methods_success": "",
                "methods_skipped": "|".join(methods_skipped),
            })
            continue

        agg_fair = _mean(success_fairs)
        agg_cheap = _mean(success_cheaps)
        row_base = {
            "stock_no": stock_no,
            "stock_name": stock_name,
            "agg_fair_price": f"{agg_fair:.2f}",
            "agg_cheap_price": f"{agg_cheap:.2f}",
            "yesterday_close": f"{close:.2f}",
            "methods_success": "|".join(methods_success),
            "methods_skipped": "|".join(methods_skipped),
        }

        if close <= agg_cheap:
            _upsert_watchlist(conn, stock_no, stock_name, agg_fair, agg_cheap)
            conn.commit()
            watchlist_upserted += 1
        elif close <= agg_fair:
            near_fair_rows.append(row_base)
        # else: above fair → not reported (EDD §14.3)

    conn.close()

    # Write CSVs
    out = Path(output_dir)
    if near_fair_rows:
        _write_csv(out / "scan_results_above_cheap.csv", near_fair_rows)
    else:
        # Always write the file (empty header) so consumers can check existence
        _write_csv(out / "scan_results_above_cheap.csv", [])

    if uncalculable_rows:
        _write_csv(out / "scan_results_uncalculable.csv", uncalculable_rows)

    return MarketScanResult(
        scan_date=scan_date,
        total_stocks=len(stocks),
        watchlist_upserted=watchlist_upserted,
        near_fair_count=len(near_fair_rows),
        uncalculable_count=len(uncalculable_rows),
        output_dir=output_dir,
    )
