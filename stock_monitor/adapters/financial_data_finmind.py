"""FinMind financial data provider for Taiwan stocks.

Fetches real financial data (dividends, EPS, balance sheet, PE/PB history)
from FinMind public API per individual stock_no.

API base: https://api.finmindtrade.com/api/v4/data
Rate limit: 300 req/hour (no token), 600 req/hour (with FINMIND_API_TOKEN).
EDD §9.1 data source for the three baseline valuation methods.

Run-level in-memory cache: within a single scan run, each (dataset, stock_no)
pair is fetched at most once. The cache lives on the provider instance — create
a new instance per scan run to get fresh data.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from urllib import error, parse, request as urllib_request

_BASE_URL = "https://api.finmindtrade.com/api/v4/data"
_TIMEOUT_SEC = 20
_MAX_BYTES = 10 * 1024 * 1024  # 10 MB


def _fetch_finmind(dataset: str, stock_id: str, start_date: str, token: str = "") -> list[dict]:
    """Low-level FinMind API call. Returns data list or empty list on error."""
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


class FinMindFinancialDataProvider:
    """Real financial data from FinMind API for Taiwan stocks.

    No persistent caching — data is fetched on each method call.
    Set FINMIND_API_TOKEN env var for higher rate limits.

    Field sources (EDD §5.13):
        avg_dividend       ← TaiwanStockDividend.CashEarningsDistribution
        eps_ttm/10y_avg    ← TaiwanStockFinancialStatements (type=EPS)
        bps_latest         ← TaiwanStockPER.PBR × close_price reciprocal
        current_assets     ← TaiwanStockBalanceSheet (type=CurrentAssets)
        total_liabilities  ← TaiwanStockBalanceSheet (type=TotalLiabilities)
        shares_outstanding ← TaiwanStockDividend.ParticipateDistributionOfTotalShares
        pe/pb history      ← TaiwanStockPER.PER / PBR
        price history      ← TaiwanStockPrice.max / min / close
    """

    def __init__(self, api_token: str | None = None, timeout: int = _TIMEOUT_SEC):
        self._token = api_token or os.getenv("FINMIND_API_TOKEN", "")
        # Run-level cache: (dataset, stock_no, start_date) → list[dict]
        # Avoids duplicate API calls when multiple methods query the same data.
        self._cache: dict[tuple[str, str, str], list[dict]] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fetch(self, dataset: str, stock_no: str, start_date: str) -> list[dict]:
        key = (dataset, str(stock_no), start_date)
        if key not in self._cache:
            self._cache[key] = _fetch_finmind(dataset, stock_no, start_date, self._token)
        return self._cache[key]

    @staticmethod
    def _start(years_back: int, extra_days: int = 90) -> str:
        return (datetime.now() - timedelta(days=365 * years_back + extra_days)).strftime("%Y-%m-%d")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_avg_dividend(self, stock_no: str, years: int = 5) -> float | None:
        """Average annual cash dividend per share over the last N fiscal years.

        Sums CashEarningsDistribution + CashStatutorySurplus per fiscal year,
        then averages across years. Returns None if no dividend data found.
        """
        rows = self._fetch("TaiwanStockDividend", stock_no, self._start(years))
        if not rows:
            return None

        by_year: dict[str, float] = {}
        for row in rows:
            year = str(row.get("year") or "")[:4]
            if not year or not year.isdigit():
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
        Annual avg = average of the most recent N annual Q4 values.
        Returns None if insufficient data (fewer than 4 quarters).
        """
        rows = self._fetch("TaiwanStockFinancialStatements", stock_no, self._start(years))
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

        # Annual avg: take the latest Q4 (highest-date per year) per year
        by_year: dict[str, float] = {}
        for r in eps_rows:
            year = (r.get("date") or "")[:4]
            if year and year not in by_year:
                by_year[year] = float(r.get("value") or 0)

        annual_vals = list(by_year.values())[:years]
        eps_10y_avg = sum(annual_vals) / len(annual_vals) if annual_vals else None

        return {
            "eps_ttm": round(eps_ttm, 4),
            "eps_10y_avg": round(eps_10y_avg, 4) if eps_10y_avg is not None else None,
        }

    def get_balance_sheet_data(self, stock_no: str) -> dict | None:
        """Returns current_assets and total_liabilities (NT$ thousands) from latest period.

        Returns None if no balance sheet data found.
        """
        rows = self._fetch("TaiwanStockBalanceSheet", stock_no, self._start(2))
        if not rows:
            return None

        rows.sort(key=lambda r: r.get("date", ""), reverse=True)
        latest_date = rows[0].get("date") if rows else None
        if not latest_date:
            return None

        result: dict[str, float] = {}
        for r in rows:
            if r.get("date") != latest_date:
                break
            t = r.get("type", "")
            if t == "CurrentAssets":
                result["current_assets"] = float(r.get("value") or 0)
            elif t == "TotalLiabilities":
                result["total_liabilities"] = float(r.get("value") or 0)

        return result if len(result) == 2 else None  # require both fields

    def get_pe_pb_stats(self, stock_no: str, years: int = 10) -> dict | None:
        """Returns annual PE/PB stats and estimated latest BPS.

        Returns dict with:
            pe_low_avg   - average of annual minimum PE over N years
            pe_mid_avg   - average of annual mean PE over N years
            pb_low_avg   - average of annual minimum PB over N years
            pb_mid_avg   - average of annual mean PB over N years
            bps_latest   - estimated from latest close / latest PBR (approx)

        Returns None if fewer than 1 year of valid data.
        """
        rows = self._fetch("TaiwanStockPER", stock_no, self._start(years))
        if not rows:
            return None

        rows.sort(key=lambda r: r.get("date", ""))

        # Group by year
        by_year: dict[str, list[tuple[float, float]]] = {}
        for r in rows:
            year = (r.get("date") or "")[:4]
            per = r.get("PER")
            pbr = r.get("PBR")
            if per is not None and pbr is not None:
                try:
                    p, b = float(per), float(pbr)
                    if p > 0 and b > 0:
                        by_year.setdefault(year, []).append((p, b))
                except (TypeError, ValueError):
                    continue

        if not by_year:
            return None

        recent_years = list(by_year.values())[-years:]
        pe_lows, pe_mids, pb_lows, pb_mids = [], [], [], []

        for year_data in recent_years:
            pers = [d[0] for d in year_data]
            pbrs = [d[1] for d in year_data]
            pe_lows.append(min(pers))
            pe_mids.append(sum(pers) / len(pers))
            pb_lows.append(min(pbrs))
            pb_mids.append(sum(pbrs) / len(pbrs))

        # Estimate latest BPS: fetch recent close and use latest PBR
        price_rows = self._fetch(
            "TaiwanStockPrice", stock_no,
            (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d"),
        )
        bps_latest: float | None = None
        if price_rows and rows:
            try:
                latest_close = float(price_rows[-1].get("close") or 0)
                latest_pbr = float(rows[-1].get("PBR") or 0)
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
        """Returns year_low_10y (avg of annual lows) and year_avg_10y (avg of annual closes).

        Returns None if fewer than 1 year of price data.
        """
        rows = self._fetch("TaiwanStockPrice", stock_no, self._start(years))
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
        """Shares participating in the most recent dividend distribution (approximation).

        Uses TaiwanStockDividend.ParticipateDistributionOfTotalShares as proxy
        for shares outstanding. Returns None if unavailable.
        """
        rows = self._fetch("TaiwanStockDividend", stock_no, self._start(2))
        if not rows:
            return None
        rows.sort(key=lambda r: r.get("date", ""), reverse=True)
        for r in rows:
            v = r.get("ParticipateDistributionOfTotalShares")
            if v is not None:
                try:
                    s = float(v)
                    if s > 0:
                        return s
                except (TypeError, ValueError):
                    continue
        return None
