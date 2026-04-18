"""Goodinfo financial data adapter — P3 (tertiary / last-resort) provider.

Per-stock web scraping with mandatory 15-second throttle between requests.
Goodinfo has comprehensive Taiwan stock data but aggressive anti-bot protection.

Cache strategy: per-stock SWR (same as FinMind). First call scrapes + stores.
Subsequent calls served from cache until stale (default 15 days).

Rate: ~4 stocks/minute per dataset. 1,078 stocks × ~6 scrapes = ~1,600 calls
= ~400 minutes for full cold start. Best used as fallback only.

All HTML parsing is best-effort; if structure changes, methods return None
(the caller treats that as transient failure and the next provider is tried).
"""

from __future__ import annotations

import re
import threading
import time
from datetime import datetime
from urllib import error, request as urllib_request

from stock_monitor.adapters.financial_data_cache import SWRCacheBase
from stock_monitor.adapters.financial_data_port import ProviderUnavailableError

_TIMEOUT = 30
_MAX_BYTES = 5 * 1024 * 1024
_MIN_INTERVAL_SEC = 15.0  # Goodinfo blocks faster requests

_UA = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9",
    "Referer": "https://goodinfo.tw/tw/index.asp",
}

# Goodinfo URL templates
_GOODINFO_DIV_URL   = "https://goodinfo.tw/tw/StockDividendPolicy.asp?STOCK_ID={}"
_GOODINFO_PEPB_URL  = "https://goodinfo.tw/tw/StockBW.asp?STOCK_ID={}&RPT_TIME=YEAR"
_GOODINFO_PRICE_URL = "https://goodinfo.tw/tw/ShowK_ChartFlow.asp?STOCK_ID={}&CHT_CAT=YEAR"
_GOODINFO_BS_URL    = "https://goodinfo.tw/tw/StockFinDetail.asp?STOCK_ID={}&RPT_CAT=M_BSREPORT&PERIOD=Q"


# ---------------------------------------------------------------------------
# Throttle
# ---------------------------------------------------------------------------

_last_request_time: float = 0.0
_throttle_lock = threading.Lock()


def _throttled_get(url: str) -> bytes | None:
    """HTTP GET with mandatory inter-request throttle."""
    global _last_request_time
    with _throttle_lock:
        now = time.time()
        wait = _MIN_INTERVAL_SEC - (now - _last_request_time)
        if wait > 0:
            time.sleep(wait)
        _last_request_time = time.time()

    req = urllib_request.Request(url, headers=_UA)
    try:
        with urllib_request.urlopen(req, timeout=_TIMEOUT) as r:
            return r.read(_MAX_BYTES)
    except (error.URLError, OSError, TimeoutError):
        return None


# ---------------------------------------------------------------------------
# HTML parsers (best-effort; return None if structure unexpected)
# ---------------------------------------------------------------------------

def _parse_table_rows(html: str) -> list[list[str]]:
    """Extract all table rows as list-of-cells (lightweight, no BeautifulSoup)."""
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S | re.I):
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S | re.I)
        cleaned = [re.sub(r"<[^>]+>", "", c).replace("\xa0", " ").strip() for c in cells]
        if cleaned:
            rows.append(cleaned)
    return rows


def _parse_goodinfo_dividend(html: bytes) -> list[dict] | None:
    """Parse StockDividendPolicy page.

    Returns list of {date, CashEarningsDistribution, ParticipateDistributionOfTotalShares}.
    """
    text = html.decode("utf-8", errors="replace")
    if "找不到" in text or "無資料" in text:
        return []  # Genuine no-data page

    rows = _parse_table_rows(text)
    if not rows:
        return None  # unexpected structure — treat as transient

    # Find header row with 年度 and 現金股利
    header_idx = year_col = cash_col = -1
    for i, row in enumerate(rows):
        joined = "".join(row)
        if "年度" in joined and "現金股利" in joined:
            header_idx = i
            for j, cell in enumerate(row):
                if "年度" in cell and year_col < 0:
                    year_col = j
                if "現金股利" in cell and cash_col < 0:
                    cash_col = j
            break

    if header_idx < 0 or year_col < 0 or cash_col < 0:
        return None  # structure mismatch — transient

    result: list[dict] = []
    for row in rows[header_idx + 1:]:
        if len(row) <= max(year_col, cash_col):
            continue
        raw_year = re.sub(r"[^\d]", "", row[year_col])
        if not raw_year:
            continue
        yr = int(raw_year)
        # Goodinfo shows western years directly
        if yr < 1990 or yr > datetime.now().year + 1:
            continue
        cash_str = row[cash_col].replace(",", "").replace("--", "0").strip()
        try:
            cash = float(cash_str) if cash_str else 0.0
        except ValueError:
            cash = 0.0
        result.append({
            "date": f"{yr}-01-01",
            "CashEarningsDistribution": cash,
            "CashStatutorySurplus": 0.0,
            "ParticipateDistributionOfTotalShares": 0.0,
        })

    return result if result else []


def _parse_goodinfo_pepb(html: bytes) -> list[dict] | None:
    """Parse StockBW page for annual PE/PB stats.

    Returns list of {date (YYYY-12-31), PER_low, PER_avg, PBR_low, PBR_avg}.
    """
    text = html.decode("utf-8", errors="replace")
    if "找不到" in text or "無資料" in text:
        return []

    rows = _parse_table_rows(text)
    if not rows:
        return None

    # Find header with 年度 and 本益比 / 股淨比
    header_idx = year_col = per_low_col = per_avg_col = pbr_low_col = pbr_avg_col = -1
    for i, row in enumerate(rows):
        joined = "".join(row)
        if "年度" in joined and ("本益比" in joined or "PER" in joined):
            header_idx = i
            for j, cell in enumerate(row):
                if "年度" in cell and year_col < 0:
                    year_col = j
                # PE columns (本益比 section: 最低, 平均 or similar)
                if "最低" in cell and per_low_col < 0:
                    per_low_col = j
                if "平均" in cell and per_avg_col < 0:
                    per_avg_col = j
                # PB columns (股淨比/PBR section)
                if "最低" in cell and per_low_col >= 0 and pbr_low_col < 0 and j > per_low_col:
                    pbr_low_col = j
                if "平均" in cell and per_avg_col >= 0 and pbr_avg_col < 0 and j > per_avg_col:
                    pbr_avg_col = j
            break

    if header_idx < 0 or year_col < 0:
        return None

    result: list[dict] = []
    for row in rows[header_idx + 1:]:
        raw_year = re.sub(r"[^\d]", "", row[year_col] if len(row) > year_col else "")
        if not raw_year:
            continue
        yr = int(raw_year)
        if yr < 1990 or yr > datetime.now().year + 1:
            continue

        def _f(col: int) -> float:
            if col < 0 or col >= len(row):
                return 0.0
            s = row[col].replace(",", "").replace("--", "0").strip()
            try:
                return float(s)
            except ValueError:
                return 0.0

        per_low = _f(per_low_col)
        per_avg = _f(per_avg_col)
        pbr_low = _f(pbr_low_col)
        pbr_avg = _f(pbr_avg_col)

        if per_low > 0 or per_avg > 0:
            result.append({
                "date": f"{yr}-12-31",
                "PER_low": per_low,
                "PER_avg": per_avg,
                "PBR_low": pbr_low,
                "PBR_avg": pbr_avg,
            })

    return result if result else []


def _parse_goodinfo_price(html: bytes) -> list[dict] | None:
    """Parse annual candlestick page for year low and close prices."""
    text = html.decode("utf-8", errors="replace")
    if "找不到" in text or "無資料" in text:
        return []

    rows = _parse_table_rows(text)
    if not rows:
        return None

    header_idx = year_col = low_col = close_col = -1
    for i, row in enumerate(rows):
        joined = "".join(row)
        if "年度" in joined and ("最低" in joined or "收盤" in joined):
            header_idx = i
            for j, cell in enumerate(row):
                if "年度" in cell and year_col < 0:
                    year_col = j
                if "最低" in cell and low_col < 0:
                    low_col = j
                if "收盤" in cell and close_col < 0:
                    close_col = j
            break

    if header_idx < 0 or year_col < 0:
        return None

    result: list[dict] = []
    for row in rows[header_idx + 1:]:
        raw_year = re.sub(r"[^\d]", "", row[year_col] if len(row) > year_col else "")
        if not raw_year:
            continue
        yr = int(raw_year)
        if yr < 1990 or yr > datetime.now().year + 1:
            continue

        def _f(col: int) -> float:
            if col < 0 or col >= len(row):
                return 0.0
            s = row[col].replace(",", "").replace("--", "0").strip()
            try:
                return float(s)
            except ValueError:
                return 0.0

        low   = _f(low_col)
        close = _f(close_col)
        if low > 0 or close > 0:
            result.append({"date": f"{yr}-12-31", "min": low, "close": close})

    return result if result else []


def _parse_goodinfo_balance_sheet(html: bytes) -> list[dict] | None:
    """Parse quarterly balance sheet for latest current_assets and total_liabilities.

    Values are in NT$1,000 on Goodinfo pages.
    """
    text = html.decode("utf-8", errors="replace")
    if "找不到" in text or "無資料" in text:
        return []

    rows = _parse_table_rows(text)
    if not rows:
        return None

    # Find header row with 流動資產 and 負債
    header_idx = period_col = ca_col = tl_col = -1
    for i, row in enumerate(rows):
        joined = "".join(row)
        if "流動資產" in joined and "負債" in joined:
            header_idx = i
            for j, cell in enumerate(row):
                if ("年度" in cell or "期間" in cell) and period_col < 0:
                    period_col = j
                if "流動資產" in cell and ca_col < 0:
                    ca_col = j
                if ("負債合計" in cell or "負債總額" in cell) and tl_col < 0:
                    tl_col = j
            break

    if header_idx < 0 or ca_col < 0 or tl_col < 0:
        return None

    # Take only the first data row (most recent quarter)
    for row in rows[header_idx + 1:]:
        if len(row) <= max(ca_col, tl_col):
            continue

        def _f(col: int) -> float:
            s = row[col].replace(",", "").replace("--", "0").strip()
            try:
                return float(s) * 1_000  # Goodinfo in NT$1,000 units
            except ValueError:
                return 0.0

        ca = _f(ca_col)
        tl = _f(tl_col)
        if ca > 0 or tl > 0:
            return [{"current_assets": ca, "total_liabilities": tl}]

    return []


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class GoodinfoAdapter(SWRCacheBase):
    """Goodinfo-backed financial data adapter (P3 — last-resort fallback).

    Per-stock scraping with 15-second throttle. SWR cache reduces repeated
    scraping: once a stock is cached, no scraping until stale (15 days).
    """

    provider_name = "goodinfo"

    def _fetch_raw(self, dataset: str, stock_no: str) -> list[dict] | None:
        """Scrape Goodinfo for one stock+dataset. Returns None on network failure."""
        if dataset == "dividend":
            raw = _throttled_get(_GOODINFO_DIV_URL.format(stock_no))
            return None if raw is None else _parse_goodinfo_dividend(raw)
        if dataset == "eps":
            # Dividend page also contains annual EPS — reuse the same scrape
            # by fetching dividend page and extracting EPS column.
            raw = _throttled_get(_GOODINFO_DIV_URL.format(stock_no))
            return None if raw is None else _parse_goodinfo_eps_from_div(raw)
        if dataset == "pepb":
            raw = _throttled_get(_GOODINFO_PEPB_URL.format(stock_no))
            return None if raw is None else _parse_goodinfo_pepb(raw)
        if dataset == "price":
            raw = _throttled_get(_GOODINFO_PRICE_URL.format(stock_no))
            return None if raw is None else _parse_goodinfo_price(raw)
        if dataset == "balance_sheet":
            raw = _throttled_get(_GOODINFO_BS_URL.format(stock_no))
            return None if raw is None else _parse_goodinfo_balance_sheet(raw)
        return []

    # ------------------------------------------------------------------
    # Public data methods
    # ------------------------------------------------------------------

    def get_avg_dividend(self, stock_no: str, years: int = 5) -> float | None:
        rows = self._fetch("dividend", stock_no)
        if not rows:
            return None

        cutoff_year = datetime.now().year - years
        by_year: dict[int, float] = {}
        for row in rows:
            yr = int(str(row.get("date") or "")[:4] or 0)
            if yr < cutoff_year:
                continue
            cash = float(row.get("CashEarningsDistribution") or 0.0)
            if cash > 0:
                by_year[yr] = by_year.get(yr, 0.0) + cash

        return round(sum(by_year.values()) / len(by_year), 4) if by_year else None

    def get_eps_data(self, stock_no: str, years: int = 10) -> dict | None:
        rows = self._fetch("eps", stock_no)
        if not rows:
            return None

        rows_sorted = sorted(rows, key=lambda r: r.get("date", ""), reverse=True)
        # Goodinfo EPS rows are annual; approximate TTM from 4 most recent quarters
        # if only annual data available, use the most recent year as TTM proxy.
        recent = rows_sorted[:4]
        if not recent:  # pragma: no cover
            return None

        # If annual data: eps field is full-year EPS; TTM = most recent year
        eps_ttm = float(recent[0].get("eps") or 0)
        annual_vals = [float(r.get("eps") or 0) for r in rows_sorted[:years]]
        eps_10y_avg = sum(annual_vals) / len(annual_vals) if annual_vals else None

        return {
            "eps_ttm": round(eps_ttm, 4),
            "eps_10y_avg": round(eps_10y_avg, 4) if eps_10y_avg is not None else None,
        }

    def get_balance_sheet_data(self, stock_no: str) -> dict | None:
        rows = self._fetch("balance_sheet", stock_no)
        if not rows:
            return None
        latest = rows[0]
        try:
            ca = float(latest.get("current_assets") or 0) / 1_000
            tl = float(latest.get("total_liabilities") or 0) / 1_000
        except (TypeError, ValueError):
            return None
        return {"current_assets": ca, "total_liabilities": tl} if (ca or tl) else None

    def get_pe_pb_stats(self, stock_no: str, years: int = 10) -> dict | None:
        rows = self._fetch("pepb", stock_no)
        if not rows:
            return None

        rows_sorted = sorted(rows, key=lambda r: r.get("date", ""))
        recent = rows_sorted[-years:]

        pe_lows, pe_mids, pb_lows, pb_mids = [], [], [], []
        for r in recent:
            pl = float(r.get("PER_low") or 0)
            pm = float(r.get("PER_avg") or 0)
            bl = float(r.get("PBR_low") or 0)
            bm = float(r.get("PBR_avg") or 0)
            if pl > 0:
                pe_lows.append(pl)
            if pm > 0:
                pe_mids.append(pm)
            if bl > 0:
                pb_lows.append(bl)
            if bm > 0:
                pb_mids.append(bm)

        if not pe_lows:
            return None

        def _avg(lst: list[float]) -> float | None:
            return round(sum(lst) / len(lst), 4) if lst else None

        # Goodinfo doesn't give BPS directly; skip bps_latest
        return {
            "pe_low_avg": _avg(pe_lows),
            "pe_mid_avg": _avg(pe_mids),
            "pb_low_avg": _avg(pb_lows),
            "pb_mid_avg": _avg(pb_mids),
            "bps_latest": None,
        }

    def get_price_annual_stats(self, stock_no: str, years: int = 10) -> dict | None:
        rows = self._fetch("price", stock_no)
        if not rows:
            return None

        rows_sorted = sorted(rows, key=lambda r: r.get("date", ""))[-years:]
        lows   = [float(r.get("min") or 0) for r in rows_sorted if float(r.get("min") or 0) > 0]
        closes = [float(r.get("close") or 0) for r in rows_sorted if float(r.get("close") or 0) > 0]

        if not lows:
            return None
        return {
            "year_low_10y": round(sum(lows) / len(lows), 2),
            "year_avg_10y": round(sum(closes) / len(closes), 2) if closes else None,
        }

    def get_shares_outstanding(self, stock_no: str) -> float | None:
        # Goodinfo dividend page doesn't easily expose shares outstanding;
        # return None and let other providers handle it.
        return None


# ---------------------------------------------------------------------------
# Helper: extract annual EPS from Goodinfo dividend policy page
# ---------------------------------------------------------------------------

def _parse_goodinfo_eps_from_div(html: bytes) -> list[dict] | None:
    """Extract annual EPS column from StockDividendPolicy page.

    The dividend page has an EPS column alongside dividend data.
    Returns list of {date, eps} or None on parse failure.
    """
    text = html.decode("utf-8", errors="replace")
    if "找不到" in text or "無資料" in text:
        return []

    rows = _parse_table_rows(text)
    if not rows:
        return None

    header_idx = year_col = eps_col = -1
    for i, row in enumerate(rows):
        joined = "".join(row)
        if "年度" in joined and "EPS" in joined:
            header_idx = i
            for j, cell in enumerate(row):
                if "年度" in cell and year_col < 0:
                    year_col = j
                if "EPS" in cell and eps_col < 0:
                    eps_col = j
            break

    if header_idx < 0 or year_col < 0 or eps_col < 0:
        return None

    result: list[dict] = []
    for row in rows[header_idx + 1:]:
        raw_year = re.sub(r"[^\d]", "", row[year_col] if len(row) > year_col else "")
        if not raw_year:
            continue
        yr = int(raw_year)
        if yr < 1990 or yr > datetime.now().year + 1:
            continue
        eps_str = row[eps_col].replace(",", "").replace("--", "0").strip() if len(row) > eps_col else "0"
        try:
            eps = float(eps_str)
        except ValueError:
            eps = 0.0
        result.append({"date": f"{yr}-12-31", "eps": eps})

    return result if result else []
