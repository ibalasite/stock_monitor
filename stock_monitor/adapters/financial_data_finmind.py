"""FinMind financial data adapter — P1 (primary) provider.

Fetch strategy (stale-while-revalidate, per-stock):
  1. In-memory cache  — same scan run: hit directly.
  2. DB cache fresh   — fetched_at within stale_days: return immediately.
  3. DB cache stale   — older than stale_days: return stale + background refresh.
  4. DB cache miss    — call FinMind API; store on success; raise
                        ProviderUnavailableError on rate-limit / network failure.

API base: https://api.finmindtrade.com/api/v4/data
Rate limit: 300 req/hour (no token), 600 req/hour (with FINMIND_API_TOKEN).
EDD §9.1 data source for the three baseline valuation methods.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from urllib import error, parse, request as urllib_request

from stock_monitor.adapters.financial_data_cache import SWRCacheBase, SWR_TTL_SECONDS
from stock_monitor.adapters.financial_data_port import ProviderUnavailableError

_BASE_URL = "https://api.finmindtrade.com/api/v4/data"
_TIMEOUT_SEC = 20
_MAX_BYTES = 10 * 1024 * 1024  # 10 MB

# Canonical start dates — fixed per dataset so cache key is (stock_no, dataset).
_DATASET_START: dict[str, str] = {
    "TaiwanStockDividend":            "2010-01-01",
    "TaiwanStockFinancialStatements": "2010-01-01",
    "TaiwanStockBalanceSheet":        "2010-01-01",
    "TaiwanStockPER":                 "2010-01-01",
    "TaiwanStockPrice":               "2010-01-01",
}


# ---------------------------------------------------------------------------
# Low-level HTTP call (no caching)
# ---------------------------------------------------------------------------

def _fetch_finmind(dataset: str, stock_id: str, start_date: str, token: str = "") -> list[dict] | None:
    """Raw FinMind API call.

    Returns:
        list[dict]  — API status 200; may be [] if stock genuinely has no data.
        None        — network error, timeout, parse failure, or non-200 status
                      (e.g. rate limit 402). Callers must NOT cache None.
    """
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
        return None  # transient — do not cache

    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None  # transient — do not cache

    if payload.get("status") != 200:
        return None  # rate limit (402) or other server error — do not cache
    return payload.get("data", []) or []


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class FinMindFinancialDataProvider(SWRCacheBase):
    """FinMind-backed financial data adapter (P1 — primary).

    Parameters
    ----------
    api_token:
        FinMind API token. Defaults to FINMIND_API_TOKEN env var.
    db_path:
        SQLite DB for SWR cache. Defaults to FINMIND_CACHE_DB_PATH or
        'data/stock_monitor.db'.
    stale_days:
        Days before a cache entry is considered stale (default 15).
    """

    provider_name = "finmind"

    def __init__(
        self,
        api_token: str | None = None,
        db_path: str | None = None,
        stale_days: int = 15,
    ) -> None:
        self._token = api_token or os.getenv("FINMIND_API_TOKEN", "")
        super().__init__(db_path=db_path, stale_days=stale_days)

    def _fetch_raw(self, dataset: str, stock_no: str) -> list[dict] | None:
        """Call FinMind API for one (dataset, stock_no). Returns None on failure."""
        start = _DATASET_START.get(dataset, "2010-01-01")
        return _fetch_finmind(dataset, stock_no, start, self._token)

    # ------------------------------------------------------------------
    # Public data methods (implement FinancialDataPort protocol)
    # ------------------------------------------------------------------

    def get_avg_dividend(self, stock_no: str, years: int = 5) -> float | None:
        """Average annual cash dividend per share over N years.

        Raises ProviderUnavailableError on transient failure.
        Returns None if no dividend data found.
        """
        rows = self._fetch("TaiwanStockDividend", stock_no)
        if not rows:
            return None

        cutoff_year = datetime.now().year - years
        by_year: dict[str, float] = {}
        for row in rows:
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
        """EPS TTM and N-year annual average.

        Raises ProviderUnavailableError on transient failure.
        Returns None if fewer than 4 quarters available.
        """
        rows = self._fetch("TaiwanStockFinancialStatements", stock_no)
        if not rows:
            return None

        eps_rows = [r for r in rows if r.get("type") == "EPS"]
        if not eps_rows:
            return None

        eps_rows.sort(key=lambda r: r.get("date", ""), reverse=True)

        recent4 = eps_rows[:4]
        if len(recent4) < 4:
            return None
        eps_ttm = sum(float(r.get("value") or 0) for r in recent4)

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
        """Latest current_assets and total_liabilities (NT$ thousands).

        Raises ProviderUnavailableError on transient failure.
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
                result["current_assets"] = float(r.get("value") or 0) / 1_000
            elif t == "Liabilities":
                result["total_liabilities"] = float(r.get("value") or 0) / 1_000

        return result if len(result) == 2 else None

    def get_pe_pb_stats(self, stock_no: str, years: int = 10) -> dict | None:
        """Annual PE/PB stats over N years plus estimated latest BPS.

        Raises ProviderUnavailableError on transient failure.
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
        """year_low_10y and year_avg_10y from N years of daily prices.

        Raises ProviderUnavailableError on transient failure.
        """
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
        """Shares in most recent dividend distribution.

        Raises ProviderUnavailableError on transient failure.
        """
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
