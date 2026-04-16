"""FR-19 全市場估值掃描 Use Case.

Orchestrates:
1. Fetch all listed stocks via AllListedStocksPort.
2. For each stock, run all enabled valuation methods.
3. Aggregate SUCCESS results → agg_fair / agg_cheap (max across methods).
4. Classify into four buckets:
   - below_cheap   → upsert watchlist; SELECT-before-upsert to count new vs updated
   - near_fair     → write scan_YYYYMMDD_near_fair.csv
   - above_fair    → counted only (not written to CSV)
   - uncalculable  → write scan_YYYYMMDD_uncalculable.csv
5. Return MarketScanResult summary.

EDD §14.3 / PDD FR-19 / ADR-016.
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
    """Summary returned by run_market_scan_job.

    Invariant: watchlist_new + watchlist_updated == watchlist_upserted
    (EDD §14.3 / ADR-016)
    """

    scan_date: str          # YYYYMMDD format for CSV filenames
    total_stocks: int
    watchlist_upserted: int
    watchlist_new: int       # stocks inserted (not previously in watchlist)
    watchlist_updated: int   # stocks updated (already existed in watchlist)
    near_fair_count: int
    uncalculable_count: int
    above_fair_count: int    # stocks above agg_fair; not written to CSV
    output_dir: str


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _upsert_watchlist(conn, stock_no: str, stock_name: str, fair: float, cheap: float) -> str:
    """Insert or update watchlist entry. Returns 'new' or 'updated'.

    SELECT-before-upsert to distinguish new vs pre-existing entries (ADR-016).
    For NEW stocks: enabled defaults to 1.
    For EXISTING stocks: only update stock_name/fair/cheap; do NOT touch enabled.
    EDD §14.3 Watchlist Upsert SQL contract.
    """
    now_epoch = int(time.time())
    existing = conn.execute(
        "SELECT 1 FROM watchlist WHERE stock_no = ?", (stock_no,)
    ).fetchone()
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
    return "updated" if existing else "new"


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
    scan_date = date.today().strftime("%Y%m%d")
    conn = connect_sqlite(db_path)
    apply_schema(conn)

    stocks = stocks_provider.get_all_listed_stocks()

    near_fair_rows: list[dict] = []
    uncalculable_rows: list[dict] = []
    watchlist_upserted = 0
    watchlist_new = 0
    watchlist_updated = 0
    above_fair_count = 0

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

        agg_fair = max(success_fairs)
        agg_cheap = max(success_cheaps)
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
            kind = _upsert_watchlist(conn, stock_no, stock_name, agg_fair, agg_cheap)
            conn.commit()
            watchlist_upserted += 1
            if kind == "new":
                watchlist_new += 1
            else:
                watchlist_updated += 1
        elif close <= agg_fair:
            near_fair_rows.append(row_base)
        else:
            above_fair_count += 1

    conn.close()

    # Write CSVs (scan_YYYYMMDD_*.csv — EDD §14.4 / gap-4 fix)
    out = Path(output_dir)
    if near_fair_rows:
        _write_csv(out / f"scan_{scan_date}_near_fair.csv", near_fair_rows)

    if uncalculable_rows:
        _write_csv(out / f"scan_{scan_date}_uncalculable.csv", uncalculable_rows)

    return MarketScanResult(
        scan_date=scan_date,
        total_stocks=len(stocks),
        watchlist_upserted=watchlist_upserted,
        watchlist_new=watchlist_new,
        watchlist_updated=watchlist_updated,
        near_fair_count=len(near_fair_rows),
        uncalculable_count=len(uncalculable_rows),
        above_fair_count=above_fair_count,
        output_dir=output_dir,
    )
