"""FinMind financial data provider for Taiwan stocks — SWR cache edition.

Fetch strategy (stale-while-revalidate):
  1. In-memory cache  — same scan run, same (dataset, stock_no): hit directly.
  2. DB cache fresh   — fetched_at within stale_days: return immediately.
  3. DB cache stale   — older than stale_days: return stale data NOW,
                        spawn daemon thread to refresh in background.
  4. DB cache miss    — fetch from FinMind API, store in DB, return.

Typical cadence: full scan runs every ~15 days.
  - First run: each stock fetches from FinMind (~28 h at 300 req/h free tier).
  - Subsequent runs: all hits from DB cache, essentially instant.
  - Refresh: background threads silently keep cache fresh on stale hits.

API base: https://api.finmindtrade.com/api/v4/data
Rate limit: 300 req/hour (no token), 600 req/hour (with FINMIND_API_TOKEN).
EDD §9.1 data source for the three baseline valuation methods.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from urllib import error, parse, request as urllib_request

_BASE_URL = "https://api.finmindtrade.com/api/v4/data"
_TIMEOUT_SEC = 20
_MAX_BYTES = 10 * 1024 * 1024  # 10 MB

# Canonical start dates per dataset — always fetch enough history.
# Cache key is (stock_no, dataset) so start_date is fixed, not dynamic.
_DATASET_START: dict[str, str] = {
    "TaiwanStockDividend":            "2010-01-01",
    "TaiwanStockFinancialStatements": "2010-01-01",
    "TaiwanStockBalanceSheet":        "2010-01-01",
    "TaiwanStockPER":                 "2010-01-01",
    "TaiwanStockPrice":               "2010-01-01",
}

_DEFAULT_STALE_DAYS = 15
_CACHE_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS financial_data_cache (
    stock_no   TEXT NOT NULL,
    dataset    TEXT NOT NULL,
    data_json  TEXT NOT NULL,
    fetched_at INTEGER NOT NULL,
    PRIMARY KEY (stock_no, dataset)
)
"""


# ---------------------------------------------------------------------------
# Low-level FinMind HTTP call (no caching)
# ---------------------------------------------------------------------------

def _fetch_finmind(dataset: str, stock_id: str, start_date: str, token: str = "") -> list[dict]:
    """Raw FinMind API call. Returns data list or [] on any error."""
    params: dict = {
        "dataset": dataset,
        "data_id": str(stock_id),
        "start_date": start_date,
    }
    if token:
        params["token"] = token

    url = _BASE_URL + "?" + parse.urlencode(params)
    req = urllib_request.Request(url, headers={"User-Agent": "stock-monitor/1.0"})

    try:
        with urllib_request.urlopen(req, timeout=_TIMEOUT_SEC) as resp:
            raw = resp.read(_MAX_BYTES)
    except (error.URLError, OSError, TimeoutError):
        return []

    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return []

    if payload.get("status") != 200:
        return []
    return payload.get("data", []) or []


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

class FinMindFinancialDataProvider:
    """Real financial data from FinMind API with SQLite-backed SWR cache.

    Parameters
    ----------
    api_token:
        FinMind API token. Defaults to FINMIND_API_TOKEN env var.
        Works without token (300 req/h); with token (600 req/h).
    db_path:
        Path to SQLite DB for persistent cache.
        Defaults to FINMIND_CACHE_DB_PATH env var or "data/stock_monitor.db".
    stale_days:
        Days before a cached entry is considered stale (default 15).
        Stale entries are returned immediately while background refresh runs.
    """

    def __init__(
        self,
        api_token: str | None = None,
        db_path: str | None = None,
        stale_days: int = _DEFAULT_STALE_DAYS,
    ):
        self._token = api_token or os.getenv("FINMIND_API_TOKEN", "")
        self._db_path = db_path or os.getenv("FINMIND_CACHE_DB_PATH", "data/stock_monitor.db")
        self._stale_sec = stale_days * 86_400
        # Run-level in-memory cache: avoids duplicate calls within one scan run.
        self._mem: dict[tuple[str, str], list[dict]] = {}
        # Tracks in-flight background refreshes to avoid duplicate threads.
        self._refreshing: set[tuple[str, str]] = set()
        self._lock = threading.Lock()
        # Ensure cache table exists.
        self._ensure_cache_table()

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    def _ensure_cache_table(self) -> None:
        try:
            with sqlite3.connect(self._db_path, timeout=5) as conn:
                conn.execute(_CACHE_CREATE_SQL)
                conn.commit()
        except sqlite3.Error:
            pass  # DB unavailable — degrade gracefully to API-only mode.

    def _db_get(self, stock_no: str, dataset: str) -> tuple[list[dict], int] | None:
        """Return (rows, fetched_at) from DB cache, or None if not found."""
        try:
            with sqlite3.connect(self._db_path, timeout=5) as conn:
                row = conn.execute(
                    "SELECT data_json, fetched_at FROM financial_data_cache WHERE stock_no=? AND dataset=?",
                    (str(stock_no), dataset),
                ).fetchone()
            if row is None:
                return None
            return json.loads(row[0]), int(row[1])
        except (sqlite3.Error, json.JSONDecodeError, ValueError):
            return None

    def _db_put(self, stock_no: str, dataset: str, rows: list[dict]) -> None:
        """Upsert rows into DB cache with current timestamp."""
        try:
            with sqlite3.connect(self._db_path, timeout=5) as conn:
                conn.execute(
                    """
                    INSERT INTO financial_data_cache (stock_no, dataset, data_json, fetched_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(stock_no, dataset) DO UPDATE SET
                        data_json  = excluded.data_json,
                        fetched_at = excluded.fetched_at
                    """,
                    (str(stock_no), dataset, json.dumps(rows, ensure_ascii=False), int(time.time())),
                )
                conn.commit()
        except sqlite3.Error:
            pass  # Cache write failure is non-fatal.

    # ------------------------------------------------------------------
    # Background refresh
    # ------------------------------------------------------------------

    def _spawn_refresh(self, stock_no: str, dataset: str) -> None:
        """Spawn a daemon thread to refresh a stale cache entry."""
        key = (str(stock_no), dataset)
        with self._lock:
            if key in self._refreshing:
                return  # Already refreshing — skip duplicate.
            self._refreshing.add(key)

        def _do() -> None:
            try:
                start = _DATASET_START.get(dataset, "2010-01-01")
                rows = _fetch_finmind(dataset, stock_no, start, self._token)
                if rows:
                    self._db_put(stock_no, dataset, rows)
                    # Update in-memory cache so next stock in this scan sees fresh data.
                    with self._lock:
                        self._mem[key] = rows
            finally:
                with self._lock:
                    self._refreshing.discard(key)

        t = threading.Thread(target=_do, daemon=True, name=f"finmind-refresh-{dataset}-{stock_no}")
        t.start()

    # ------------------------------------------------------------------
    # Core fetch — SWR cache logic
    # ------------------------------------------------------------------

    def _fetch(self, dataset: str, stock_no: str) -> list[dict]:
        """Return data for (dataset, stock_no) using SWR strategy."""
        key = (str(stock_no), dataset)

        # 1. In-memory cache (run-level dedup)
        with self._lock:
            if key in self._mem:
                return self._mem[key]

        # 2 & 3. DB cache
        cached = self._db_get(stock_no, dataset)
        if cached is not None:
            rows, fetched_at = cached
            age = int(time.time()) - fetched_at
            with self._lock:
                self._mem[key] = rows  # promote to mem cache
            if age >= self._stale_sec:
                self._spawn_refresh(stock_no, dataset)  # stale → background refresh
            return rows

        # 4. Cache miss — fetch fresh, store, return
        start = _DATASET_START.get(dataset, "2010-01-01")
        rows = _fetch_finmind(dataset, stock_no, start, self._token)
        self._db_put(stock_no, dataset, rows)
        with self._lock:
            self._mem[key] = rows
        return rows

    # ------------------------------------------------------------------
    # Public data methods (EDD §9.1 inputs)
    # ------------------------------------------------------------------

    def get_avg_dividend(self, stock_no: str, years: int = 5) -> float | None:
        """Average annual cash dividend per share over the last N fiscal years.

        Sums CashEarningsDistribution + CashStatutorySurplus per ex-dividend date
        year (ISO date field), then averages across years.
        NOTE: The FinMind 'year' field is in ROC calendar format ('98年'); use
        the 'date' field (ISO YYYY-MM-DD) instead to extract the Western year.
        Returns None if no dividend data found.
        """
        rows = self._fetch("TaiwanStockDividend", stock_no)
        if not rows:
            return None

        cutoff_year = datetime.now().year - years
        by_year: dict[str, float] = {}
        for row in rows:
            # Use 'date' (ISO format) — 'year' is ROC calendar (e.g. '98年').
            year = str(row.get("date") or "")[:4]
            if not year or not year.isdigit() or int(year) < cutoff_year:
                continue
            cash = float(row.get("CashEarningsDistribution") or 0.0)
            cash += float(row.get("CashStatutorySurplus") or 0.0)
            if cash > 0:
                by_year[year] = by_year.get(year, 0.0) + cash

        if not by_year:
            return None
        return round(sum(by_year.values()) / len(by_year), 4)

    def get_eps_data(self, stock_no: str, years: int = 10) -> dict | None:
        """Returns eps_ttm (trailing 12-month) and eps_10y_avg (N-year annual avg).

        EPS rows from TaiwanStockFinancialStatements (type='EPS') are quarterly.
        TTM = sum of the 4 most recent quarters.
        Annual avg = average of the most recent N calendar-year values.
        Returns None if fewer than 4 quarters available.
        """
        rows = self._fetch("TaiwanStockFinancialStatements", stock_no)
        if not rows:
            return None

        eps_rows = [r for r in rows if r.get("type") == "EPS"]
        if not eps_rows:
            return None

        eps_rows.sort(key=lambda r: r.get("date", ""), reverse=True)

        # TTM: sum last 4 quarters
        recent4 = eps_rows[:4]
        if len(recent4) < 4:
            return None
        eps_ttm = sum(float(r.get("value") or 0) for r in recent4)

        # Annual avg: sum all quarterly EPS entries per calendar year,
        # then average across the N most recent years.
        by_year: dict[str, float] = {}
        for r in eps_rows:
            year = (r.get("date") or "")[:4]
            if year:
                by_year[year] = by_year.get(year, 0.0) + float(r.get("value") or 0)

        annual_vals = list(by_year.values())[:years]
        eps_10y_avg = sum(annual_vals) / len(annual_vals) if annual_vals else None

        return {
            "eps_ttm": round(eps_ttm, 4),
            "eps_10y_avg": round(eps_10y_avg, 4) if eps_10y_avg is not None else None,
        }

    def get_balance_sheet_data(self, stock_no: str) -> dict | None:
        """Returns current_assets and total_liabilities (NT$ thousands) from latest period.

        FinMind TaiwanStockBalanceSheet reports values in actual NT$ (not thousands).
        We divide by 1000 before returning so the values match the NT$-thousands unit
        expected by the NCAV formula:  (current_assets - total_liabilities) * 1000 / shares.

        FinMind type names: 'CurrentAssets' and 'Liabilities' (total liabilities).
        'TotalLiabilities' does not exist; 'Liabilities' is the correct aggregate key.
        """
        rows = self._fetch("TaiwanStockBalanceSheet", stock_no)
        if not rows:
            return None

        rows_sorted = sorted(rows, key=lambda r: r.get("date", ""), reverse=True)
        latest_date = rows_sorted[0].get("date") if rows_sorted else None
        if not latest_date:
            return None

        result: dict[str, float] = {}
        for r in rows_sorted:
            if r.get("date") != latest_date:
                break
            t = r.get("type", "")
            if t == "CurrentAssets":
                # Divide by 1000: FinMind returns NT$ actual; formula expects NT$ thousands.
                result["current_assets"] = float(r.get("value") or 0) / 1_000
            elif t == "Liabilities":
                result["total_liabilities"] = float(r.get("value") or 0) / 1_000

        return result if len(result) == 2 else None

    def get_pe_pb_stats(self, stock_no: str, years: int = 10) -> dict | None:
        """Returns annual PE/PB stats and estimated latest BPS.

        Returns dict:
            pe_low_avg, pe_mid_avg — average of annual min/mean PE over N years
            pb_low_avg, pb_mid_avg — average of annual min/mean PB over N years
            bps_latest             — estimated from latest close / latest PBR
        Returns None if fewer than 1 year of valid data.
        """
        rows = self._fetch("TaiwanStockPER", stock_no)
        if not rows:
            return None

        rows_sorted = sorted(rows, key=lambda r: r.get("date", ""))

        by_year: dict[str, list[tuple[float, float]]] = {}
        for r in rows_sorted:
            year = (r.get("date") or "")[:4]
            try:
                p, b = float(r.get("PER") or 0), float(r.get("PBR") or 0)
                if p > 0 and b > 0:
                    by_year.setdefault(year, []).append((p, b))
            except (TypeError, ValueError):
                continue

        if not by_year:
            return None

        recent = list(by_year.values())[-years:]
        pe_lows, pe_mids, pb_lows, pb_mids = [], [], [], []
        for year_data in recent:
            pers = [d[0] for d in year_data]
            pbrs = [d[1] for d in year_data]
            pe_lows.append(min(pers))
            pe_mids.append(sum(pers) / len(pers))
            pb_lows.append(min(pbrs))
            pb_mids.append(sum(pbrs) / len(pbrs))

        # Approximate BPS: latest close ÷ latest PBR
        price_rows = self._fetch("TaiwanStockPrice", stock_no)
        bps_latest: float | None = None
        if price_rows and rows_sorted:
            try:
                price_rows_sorted = sorted(price_rows, key=lambda r: r.get("date", ""))
                latest_close = float(price_rows_sorted[-1].get("close") or 0)
                latest_pbr = float(rows_sorted[-1].get("PBR") or 0)
                if latest_close > 0 and latest_pbr > 0:
                    bps_latest = round(latest_close / latest_pbr, 2)
            except (TypeError, ValueError, IndexError):
                pass

        def _avg(lst: list[float]) -> float | None:
            return round(sum(lst) / len(lst), 4) if lst else None

        return {
            "pe_low_avg": _avg(pe_lows),
            "pe_mid_avg": _avg(pe_mids),
            "pb_low_avg": _avg(pb_lows),
            "pb_mid_avg": _avg(pb_mids),
            "bps_latest": bps_latest,
        }

    def get_price_annual_stats(self, stock_no: str, years: int = 10) -> dict | None:
        """Returns year_low_10y (avg of annual lows) and year_avg_10y (avg of annual closes)."""
        rows = self._fetch("TaiwanStockPrice", stock_no)
        if not rows:
            return None

        by_year: dict[str, dict[str, list[float]]] = {}
        for r in rows:
            year = (r.get("date") or "")[:4]
            if not year:
                continue
            try:
                low = float(r.get("min") or 0)
                close = float(r.get("close") or 0)
            except (TypeError, ValueError):
                continue
            if low > 0 and close > 0:
                y = by_year.setdefault(year, {"lows": [], "closes": []})
                y["lows"].append(low)
                y["closes"].append(close)

        if not by_year:
            return None

        annual_lows = [min(v["lows"]) for v in by_year.values() if v["lows"]]
        annual_avgs = [sum(v["closes"]) / len(v["closes"]) for v in by_year.values() if v["closes"]]

        if not annual_lows:
            return None
        return {
            "year_low_10y": round(sum(annual_lows) / len(annual_lows), 2),
            "year_avg_10y": round(sum(annual_avgs) / len(annual_avgs), 2),
        }

    def get_shares_outstanding(self, stock_no: str) -> float | None:
        """Shares participating in the most recent dividend distribution (proxy for outstanding shares)."""
        rows = self._fetch("TaiwanStockDividend", stock_no)
        if not rows:
            return None
        rows_sorted = sorted(rows, key=lambda r: r.get("date", ""), reverse=True)
        for r in rows_sorted:
            v = r.get("ParticipateDistributionOfTotalShares")
            if v is not None:
                try:
                    s = float(v)
                    if s > 0:
                        return s
                except (TypeError, ValueError):
                    continue
        return None
