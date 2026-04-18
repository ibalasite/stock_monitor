"""MOPS + TWSE financial data adapter — P2 (secondary / fallback) provider.

Bulk-fetch strategy (different from FinMind's per-stock approach):
  - EPS & Balance Sheet: one MOPS POST call returns ALL companies for a quarter.
    First miss for any stock triggers a bulk fetch that populates the cache for
    every stock at once. The second stock always hits the cache.
  - Dividend: per-stock from MOPS (no bulk endpoint available).
  - PE/PB: TWSE BWIBBU_d per trading date; one call returns all stocks.
    Historical PE/PB built by fetching 10 years of daily files (~2,500 calls,
    run as a one-time overnight pre-cache).
  - Price stats: TWSE STOCK_DAY_ALL per month; one call returns all stocks.

Rate limit: MOPS/TWSE are government servers — no token needed, ~1 req/s safe.

SWR cache: each (provider='mops', stock_no, dataset) entry follows the same
  15-day stale threshold as FinMind, but populated via bulk writes.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from datetime import date, datetime, timedelta
from urllib import error, parse, request as urllib_request

from stock_monitor.adapters.financial_data_cache import SWRCacheBase
from stock_monitor.adapters.financial_data_port import ProviderUnavailableError

_TIMEOUT = 20
_MAX_BYTES = 20 * 1024 * 1024  # 20 MB (MOPS quarterly pages can be large)
_UA = {"User-Agent": "stock-monitor/1.0 (research)"}

# MOPS base (POST requests return HTML tables)
_MOPS_EPS_URL   = "https://mops.twse.com.tw/mops/web/ajax_t163sb04"
_MOPS_BS_URL    = "https://mops.twse.com.tw/mops/web/ajax_t164sb03"
_MOPS_DIV_URL   = "https://mops.twse.com.tw/mops/web/ajax_t05st09"

# TWSE (GET requests return JSON directly)
_TWSE_PEPB_URL  = "https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU_d"
_TWSE_PRICE_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY_ALL"

# Current ROC year helper
_ROC_BASE = 1911


def _roc_year(western: int) -> str:
    return str(western - _ROC_BASE)


def _get(url: str, params: dict | None = None) -> bytes | None:
    """HTTP GET with params. Returns raw bytes or None on failure."""
    full = url + ("?" + parse.urlencode(params) if params else "")
    req = urllib_request.Request(full, headers=_UA)
    try:
        with urllib_request.urlopen(req, timeout=_TIMEOUT) as r:
            return r.read(_MAX_BYTES)
    except (error.URLError, OSError, TimeoutError):
        return None


def _post(url: str, data: dict) -> bytes | None:
    """HTTP POST with form data. Returns raw bytes or None on failure."""
    encoded = parse.urlencode(data).encode()
    req = urllib_request.Request(url, data=encoded, headers={
        **_UA,
        "Content-Type": "application/x-www-form-urlencoded",
    })
    try:
        with urllib_request.urlopen(req, timeout=_TIMEOUT) as r:
            return r.read(_MAX_BYTES)
    except (error.URLError, OSError, TimeoutError):
        return None


def _parse_mops_html_table(html: bytes) -> list[list[str]]:
    """Very lightweight HTML table parser — avoids BeautifulSoup dependency.

    Returns list of rows, each row a list of cell text values.
    Strips whitespace and handles both <td> and <th>.
    """
    text = html.decode("utf-8", errors="replace")
    # Find all <tr>...</tr> blocks
    rows: list[list[str]] = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", text, re.I | re.S):
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.I | re.S)
        cleaned = [re.sub(r"<[^>]+>", "", c).strip() for c in cells]
        if cleaned:
            rows.append(cleaned)
    return rows


# ---------------------------------------------------------------------------
# MOPS bulk EPS / Balance Sheet
# ---------------------------------------------------------------------------

def _fetch_mops_eps_quarter(typek: str, year: int, season: int) -> dict[str, list[dict]] | None:
    """Fetch quarterly EPS for all companies of TYPEK (sii=TWSE, otc=TPEx).

    Returns {stock_no: [{"date": "YYYY-QN", "eps": float}]} or None on failure.
    """
    roc_year = _roc_year(year)
    raw = _post(_MOPS_EPS_URL, {
        "encodeURIComponent": 1,
        "step": 1,
        "firstin": 1,
        "off": 1,
        "TYPEK": typek,
        "year": roc_year,
        "season": f"{season:02d}",
    })
    if raw is None:
        return None

    rows = _parse_mops_html_table(raw)
    result: dict[str, list[dict]] = {}
    # Expected columns: 公司代號, 公司名稱, 基本EPS, …
    # Column 0 = stock_no, col 2 (or nearby) = EPS value
    # We try to find the EPS column by header matching.
    if len(rows) < 2:
        return result  # empty but not a failure

    # Detect header row (find the row that contains "公司代號" or "股票代號")
    header_idx = 0
    eps_col = 2  # fallback
    for i, row in enumerate(rows):
        joined = "".join(row)
        if "公司代號" in joined or "股票代號" in joined:
            header_idx = i
            # Find EPS column
            for j, cell in enumerate(row):
                if "基本每股" in cell or "基本EPS" in cell or "每股盈餘" in cell:
                    eps_col = j
                    break
            break

    period_str = f"{year}-Q{season}"
    for row in rows[header_idx + 1:]:
        if len(row) <= eps_col:
            continue
        stock_no = row[0].strip()
        if not re.match(r"^\d{4,6}$", stock_no):
            continue
        eps_str = row[eps_col].replace(",", "").strip()
        try:
            eps_val = float(eps_str)
        except ValueError:
            continue
        result.setdefault(stock_no, []).append({"date": period_str, "eps": eps_val})

    return result


def _fetch_mops_bs_quarter(typek: str, year: int, season: int) -> dict[str, list[dict]] | None:
    """Fetch quarterly balance sheet for all companies.

    Returns {stock_no: [{"date": "YYYY-QN", "current_assets": float, "total_liabilities": float}]}
    Values in NT$ (will be divided by 1000 when consumed).
    """
    roc_year = _roc_year(year)
    raw = _post(_MOPS_BS_URL, {
        "encodeURIComponent": 1,
        "step": 1,
        "firstin": 1,
        "off": 1,
        "TYPEK": typek,
        "year": roc_year,
        "season": f"{season:02d}",
    })
    if raw is None:
        return None

    rows = _parse_mops_html_table(raw)
    result: dict[str, list[dict]] = {}
    if len(rows) < 2:
        return result

    header_idx = 0
    ca_col = tl_col = -1
    for i, row in enumerate(rows):
        joined = "".join(row)
        if "公司代號" in joined or "股票代號" in joined:
            header_idx = i
            for j, cell in enumerate(row):
                if "流動資產" in cell:
                    ca_col = j
                if "負債總額" in cell or "負債合計" in cell:
                    tl_col = j
            break

    if ca_col < 0 or tl_col < 0:
        return result  # couldn't find columns — empty but not a failure

    period_str = f"{year}-Q{season}"
    for row in rows[header_idx + 1:]:
        stock_no = row[0].strip() if row else ""
        if not re.match(r"^\d{4,6}$", stock_no):
            continue
        try:
            ca = float(row[ca_col].replace(",", "")) if len(row) > ca_col else 0.0
            tl = float(row[tl_col].replace(",", "")) if len(row) > tl_col else 0.0
        except ValueError:
            continue
        result.setdefault(stock_no, []).append({
            "date": period_str,
            "current_assets": ca,
            "total_liabilities": tl,
        })

    return result


# ---------------------------------------------------------------------------
# TWSE PE/PB + Price (bulk per date / per month)
# ---------------------------------------------------------------------------

def _fetch_twse_pepb_date(trade_date: str) -> dict[str, dict] | None:
    """Fetch all stocks' PE/PB for one trading date (YYYYMMDD).

    Returns {stock_no: {"date": str, "PER": float, "PBR": float}} or None.
    """
    raw = _get(_TWSE_PEPB_URL, {"date": trade_date, "type": "ALL", "response": "json"})
    if raw is None:
        return None
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None

    if payload.get("stat") != "OK":
        return None

    fields = payload.get("fields", [])
    try:
        code_idx = fields.index("證券代號")
        per_idx  = fields.index("本益比")
        pbr_idx  = fields.index("股價淨值比")
    except ValueError:
        return None

    result: dict[str, dict] = {}
    for row in payload.get("data", []):
        stock_no = str(row[code_idx]).strip()
        if not re.match(r"^\d{4,6}$", stock_no):
            continue
        try:
            per = float(str(row[per_idx]).replace(",", "") or 0)
            pbr = float(str(row[pbr_idx]).replace(",", "") or 0)
        except (ValueError, TypeError):
            continue
        if per > 0 and pbr > 0:
            result[stock_no] = {"date": trade_date, "PER": per, "PBR": pbr}

    return result


def _fetch_twse_price_month(yyyymm: str) -> dict[str, list[dict]] | None:
    """Fetch all stocks' monthly OHLCV for one month (YYYYMM01 format).

    Returns {stock_no: [{"date": str, "close": float, "min": float}]} or None.
    """
    raw = _get(_TWSE_PRICE_URL, {"date": f"{yyyymm}01", "response": "json"})
    if raw is None:
        return None
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None

    if payload.get("stat") != "OK":
        return None

    fields = payload.get("fields", [])
    try:
        code_idx  = fields.index("證券代號")
        close_idx = fields.index("收盤價")
        low_idx   = fields.index("最低價")
    except ValueError:
        return None

    result: dict[str, list[dict]] = {}
    for row in payload.get("data", []):
        stock_no = str(row[code_idx]).strip()
        if not re.match(r"^\d{4,6}$", stock_no):
            continue
        try:
            close = float(str(row[close_idx]).replace(",", "") or 0)
            low   = float(str(row[low_idx]).replace(",", "") or 0)
        except (ValueError, TypeError):
            continue
        if close > 0 and low > 0:
            result.setdefault(stock_no, []).append({
                "date": yyyymm,
                "close": close,
                "min": low,
            })

    return result


# ---------------------------------------------------------------------------
# Per-stock MOPS dividend
# ---------------------------------------------------------------------------

def _fetch_mops_dividend(stock_no: str) -> list[dict] | None:
    """Fetch dividend history for one stock from MOPS.

    Returns list of {date, cash_dividend} dicts or None on failure.
    """
    raw = _post(_MOPS_DIV_URL, {
        "encodeURIComponent": 1,
        "step": 1,
        "firstin": 1,
        "off": 1,
        "co_id": stock_no,
        "TYPEK": "all",
    })
    if raw is None:
        return None

    rows = _parse_mops_html_table(raw)
    result: list[dict] = []
    if len(rows) < 2:
        return result  # empty = no dividend data (not a failure)

    # Find header to locate year and cash dividend columns
    header_idx = 0
    year_col = cash_col = shares_col = -1
    for i, row in enumerate(rows):
        joined = "".join(row)
        if "年度" in joined or "所屬年度" in joined:
            header_idx = i
            for j, cell in enumerate(row):
                if "年度" in cell and year_col < 0:
                    year_col = j
                if "現金股利" in cell and cash_col < 0:
                    cash_col = j
                if "參與分配股數" in cell or "股數" in cell and shares_col < 0:
                    shares_col = j
            break

    for row in rows[header_idx + 1:]:
        if year_col < 0 or len(row) <= max(year_col, cash_col if cash_col >= 0 else 0):
            continue
        try:
            raw_year = row[year_col].strip()
            # Year may be ROC (113) or western (2024); normalize to western.
            yr = int(re.sub(r"[^\d]", "", raw_year) or 0)
            if 0 < yr < 200:
                yr += _ROC_BASE  # ROC to western
            if yr < 2000:
                continue
            cash = float(row[cash_col].replace(",", "")) if cash_col >= 0 and len(row) > cash_col else 0.0
            shares = float(row[shares_col].replace(",", "")) if shares_col >= 0 and len(row) > shares_col else 0.0
        except (ValueError, IndexError):
            continue
        result.append({
            "date": f"{yr}-01-01",
            "CashEarningsDistribution": cash,
            "CashStatutorySurplus": 0.0,
            "ParticipateDistributionOfTotalShares": shares,
        })

    return result


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class MopsTwseAdapter(SWRCacheBase):
    """MOPS + TWSE financial data adapter (P2 — secondary fallback).

    Uses bulk fetch strategy:
      - EPS + Balance Sheet: one MOPS quarterly POST populates all stocks at once.
      - PE/PB: TWSE daily BWIBBU_d files; historical build runs in background.
      - Price stats: TWSE monthly STOCK_DAY_ALL files.
      - Dividend: per-stock MOPS (no bulk endpoint).

    After the first call for any stock, subsequent stocks find a warm cache.
    """

    provider_name = "mops"

    # Track which bulk datasets have been prefetched this run (in-memory flag).
    # DB cache persists across runs; this flag avoids redundant in-run re-fetches.
    _bulk_done: set[str]
    _bulk_lock: threading.Lock

    def __init__(self, db_path: str | None = None, stale_days: int = 15) -> None:
        super().__init__(db_path=db_path, stale_days=stale_days)
        self._bulk_done = set()
        self._bulk_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Bulk prefetch helpers
    # ------------------------------------------------------------------

    def _has_fresh_bulk(self, dataset_prefix: str, min_stocks: int = 50) -> bool:
        """Return True if we already have enough fresh data for this dataset."""
        try:
            with sqlite3.connect(self._db_path, timeout=5) as conn:
                count = conn.execute(
                    "SELECT COUNT(DISTINCT stock_no) FROM financial_data_cache "
                    "WHERE provider=? AND dataset LIKE ? AND fetched_at > ?",
                    (self.provider_name, f"{dataset_prefix}%", int(time.time()) - self._stale_sec),
                ).fetchone()[0]
            return count >= min_stocks
        except sqlite3.Error:
            return False

    def _bulk_fetch_eps(self, years: int = 10) -> None:
        """Fetch EPS for all TWSE+TPEx companies for the last N years of quarters."""
        today = date.today()
        entries: list[tuple[str, str, list[dict]]] = []

        for delta_year in range(years):
            yr = today.year - delta_year
            max_season = 4
            if yr == today.year:
                # Don't request quarters not yet published
                max_season = min(4, (today.month - 1) // 3 + 1)
            for season in range(1, max_season + 1):
                period = f"{yr}-Q{season}"
                dataset = f"eps_{period}"
                for typek in ("sii", "otc"):
                    data = _fetch_mops_eps_quarter(typek, yr, season)
                    if data is None:
                        continue  # server error for this quarter — skip
                    for sno, rows in data.items():
                        # Merge rows keyed by period
                        entries.append((sno, dataset, rows))
                time.sleep(0.5)  # be polite to MOPS

        # Also store aggregated per-stock EPS list for SWR cache lookup
        # Group by stock_no across all periods
        by_stock: dict[str, list[dict]] = {}
        for sno, dataset, rows in entries:
            period = dataset[4:]  # strip "eps_"
            for r in rows:
                by_stock.setdefault(sno, []).append({"date": period, "eps": r["eps"]})

        bulk: list[tuple[str, str, list[dict]]] = [
            (sno, "eps", rows) for sno, rows in by_stock.items()
        ]
        self._db_put_many(bulk)
        # Update mem cache
        with self._lock:
            for sno, rows in by_stock.items():
                self._mem[(sno, "eps")] = rows

    def _bulk_fetch_balance_sheet(self) -> None:
        """Fetch latest balance sheet for all TWSE+TPEx companies (last 2 years)."""
        today = date.today()
        by_stock: dict[str, list[dict]] = {}

        for delta_year in range(2):
            yr = today.year - delta_year
            for season in range(4, 0, -1):  # start from Q4 → Q1
                for typek in ("sii", "otc"):
                    data = _fetch_mops_bs_quarter(typek, yr, season)
                    if data is None:
                        continue
                    for sno, rows in data.items():
                        if sno not in by_stock:  # keep only latest
                            by_stock[sno] = rows
                time.sleep(0.5)

        bulk = [(sno, "balance_sheet", rows) for sno, rows in by_stock.items()]
        self._db_put_many(bulk)
        with self._lock:
            for sno, rows in by_stock.items():
                self._mem[(sno, "balance_sheet")] = rows

    def _bulk_fetch_pepb(self, years: int = 10) -> None:
        """Fetch PE/PB for all TWSE stocks for the last N years of trading days.

        This is a large one-time operation (~2,500 calls). Runs in background after
        the first call that needs PE/PB data.
        """
        today = date.today()
        by_stock: dict[str, list[dict]] = {}

        # Fetch end-of-month snapshots rather than every trading day
        # (one per month × 10 years = 120 calls, gives enough annual min/avg)
        cur = date(today.year - years, 1, 1)
        while cur <= today:
            # Use last day of each month
            next_month = date(cur.year + (cur.month // 12), (cur.month % 12) + 1, 1)
            eom = next_month - timedelta(days=1)
            trade_date = eom.strftime("%Y%m%d")

            data = _fetch_twse_pepb_date(trade_date)
            if data:
                for sno, row in data.items():
                    by_stock.setdefault(sno, []).append(row)

            cur = next_month
            time.sleep(0.3)

        bulk = [(sno, "pepb", rows) for sno, rows in by_stock.items()]
        self._db_put_many(bulk)
        with self._lock:
            for sno, rows in by_stock.items():
                self._mem[(sno, "pepb")] = rows

    def _bulk_fetch_price(self, years: int = 10) -> None:
        """Fetch monthly price data for all TWSE stocks for the last N years."""
        today = date.today()
        by_stock: dict[str, list[dict]] = {}

        cur = date(today.year - years, 1, 1)
        while cur <= today:
            yyyymm = cur.strftime("%Y%m")
            data = _fetch_twse_price_month(yyyymm)
            if data:
                for sno, rows in data.items():
                    by_stock.setdefault(sno, []).extend(rows)

            # Advance to next month
            nxt = date(cur.year + (cur.month // 12), (cur.month % 12) + 1, 1)
            cur = nxt
            time.sleep(0.3)

        bulk = [(sno, "price", rows) for sno, rows in by_stock.items()]
        self._db_put_many(bulk)
        with self._lock:
            for sno, rows in by_stock.items():
                self._mem[(sno, "price")] = rows

    # ------------------------------------------------------------------
    # Override _fetch for bulk datasets
    # ------------------------------------------------------------------

    def _fetch(self, dataset: str, stock_no: str) -> list[dict]:
        """SWR fetch with bulk-prefetch for MOPS/TWSE bulk datasets.

        For eps, balance_sheet, pepb, price: on first miss, bulk-fetch all stocks
        and populate the cache for everyone at once. Subsequent stocks are cache hits.

        For dividend: falls through to per-stock _fetch_raw (no bulk available).
        """
        # Fast path: check in-memory cache first (same for all datasets)
        key = (str(stock_no), dataset)
        with self._lock:
            if key in self._mem:
                return self._mem[key]

        # Check DB cache
        cached = self._db_get(stock_no, dataset)
        if cached is not None:
            rows, fetched_at = cached
            age = int(time.time()) - fetched_at
            with self._lock:
                self._mem[key] = rows
            if age >= self._stale_sec:
                self._spawn_refresh(stock_no, dataset)
            return rows

        # Cache miss — choose strategy based on dataset
        if dataset == "eps":
            # EPS bulk is expensive; kick off in background, raise immediately.
            # Caller (ParallelFinancialDataProvider) will retry once cache warms.
            self._ensure_bulk_background("eps", self._bulk_fetch_eps)
            raise ProviderUnavailableError(
                "mops: EPS bulk fetch in progress — try again after pre-cache completes"
            )
        elif dataset == "balance_sheet":
            self._ensure_bulk("balance_sheet", self._bulk_fetch_balance_sheet)
        elif dataset == "pepb":
            # PE/PB bulk is expensive; kick off in background, return empty now.
            self._ensure_bulk_background("pepb", self._bulk_fetch_pepb)
            raise ProviderUnavailableError(
                "mops: PE/PB bulk fetch in progress — try again after pre-cache completes"
            )
        elif dataset == "price":
            self._ensure_bulk("price", self._bulk_fetch_price)
        else:
            # dividend — per-stock path (falls to _fetch_raw below)
            rows = self._fetch_raw(dataset, stock_no)
            if rows is None:
                raise ProviderUnavailableError(
                    f"mops: transient failure for {dataset}/{stock_no}"
                )
            self._db_put(stock_no, dataset, rows)
            with self._lock:
                self._mem[key] = rows
            return rows

        # After bulk fetch, check cache again
        with self._lock:
            if key in self._mem:
                return self._mem[key]

        db_again = self._db_get(stock_no, dataset)
        if db_again is not None:  # pragma: no cover
            rows2, _ = db_again
            with self._lock:
                self._mem[key] = rows2
            return rows2

        # Stock not found in bulk data — genuinely no data in MOPS
        empty: list[dict] = []
        self._db_put(stock_no, dataset, empty)
        with self._lock:
            self._mem[key] = empty
        return empty

    def _ensure_bulk(self, key: str, fetch_fn) -> None:  # type: ignore[type-arg]
        """Run bulk fetch synchronously if not done this run and not fresh in DB.

        The first caller executes the fetch *without* holding ``_bulk_lock``
        (which would block every concurrent MOPS thread for the full fetch
        duration — up to 2 minutes for balance_sheet).  A ``__fetching``
        sentinel in ``_bulk_done`` signals that the fetch is in progress;
        concurrent callers raise ``ProviderUnavailableError`` immediately so
        FinMind / Goodinfo can serve their requests instead.  After the fetch
        completes, all future callers return instantly via ``_bulk_done``.
        """
        _sentinel = f"{key}__fetching"
        with self._bulk_lock:
            if key in self._bulk_done:
                return
            if _sentinel in self._bulk_done:
                # Another thread owns the fetch — bail so other providers serve.
                raise ProviderUnavailableError(
                    f"mops: {key} bulk fetch in progress — try again after pre-cache completes"
                )
            if self._has_fresh_bulk(key):
                self._bulk_done.add(key)
                return
            # This thread wins the fetch race; mark in progress before releasing lock.
            self._bulk_done.add(_sentinel)
        # Execute fetch WITHOUT holding _bulk_lock.
        _success = False
        try:
            fetch_fn()
            _success = True
        finally:
            with self._bulk_lock:
                self._bulk_done.discard(_sentinel)
                if _success:
                    self._bulk_done.add(key)

    def _ensure_bulk_background(self, key: str, fetch_fn) -> None:  # type: ignore[type-arg]
        """Start bulk fetch in a background thread if not already running."""
        with self._bulk_lock:
            if key in self._bulk_done:
                return
            if f"{key}_pending" in self._bulk_done:
                return  # background thread already running — do not start another
            if self._has_fresh_bulk(key):
                self._bulk_done.add(key)
                return
            # Mark as "in progress" to avoid duplicate threads
            self._bulk_done.add(f"{key}_pending")

        def _run() -> None:
            fetch_fn()
            with self._bulk_lock:
                self._bulk_done.discard(f"{key}_pending")
                self._bulk_done.add(key)

        t = threading.Thread(target=_run, daemon=True, name=f"mops-bulk-{key}")
        t.start()

    def _fetch_raw(self, dataset: str, stock_no: str) -> list[dict] | None:
        """Per-stock fetch for datasets without bulk endpoints (dividend only)."""
        if dataset == "dividend":
            return _fetch_mops_dividend(stock_no)
        return []  # unknown dataset — treat as no data

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
            yr_str = str(row.get("date") or "")[:4]
            if not yr_str.isdigit():
                continue
            yr = int(yr_str)
            if yr < cutoff_year:
                continue
            cash = float(row.get("CashEarningsDistribution") or 0.0)
            cash += float(row.get("CashStatutorySurplus") or 0.0)
            if cash > 0:
                by_year[yr] = by_year.get(yr, 0.0) + cash

        if not by_year:
            return None
        return round(sum(by_year.values()) / len(by_year), 4)

    def get_eps_data(self, stock_no: str, years: int = 10) -> dict | None:
        rows = self._fetch("eps", stock_no)
        if not rows:
            return None

        # rows: [{"date": "2023-Q1", "eps": 1.23}, ...]
        rows_sorted = sorted(rows, key=lambda r: r.get("date", ""), reverse=True)

        # TTM = sum of last 4 quarters
        recent4 = rows_sorted[:4]
        if len(recent4) < 4:
            return None
        eps_ttm = sum(float(r.get("eps") or 0) for r in recent4)

        # Annual avg: sum quarterly EPS per calendar year, then average
        by_year: dict[str, float] = {}
        for r in rows_sorted:
            yr = str(r.get("date") or "")[:4]
            if yr:
                by_year[yr] = by_year.get(yr, 0.0) + float(r.get("eps") or 0)
        annual_vals = list(by_year.values())[:years]
        eps_10y_avg = sum(annual_vals) / len(annual_vals) if annual_vals else None

        return {
            "eps_ttm": round(eps_ttm, 4),
            "eps_10y_avg": round(eps_10y_avg, 4) if eps_10y_avg is not None else None,
        }

    def get_balance_sheet_data(self, stock_no: str) -> dict | None:
        rows = self._fetch("balance_sheet", stock_no)
        if not rows:
            return None
        latest = rows[-1]  # stored as single-entry list from latest quarter
        try:
            ca = float(latest.get("current_assets") or 0) / 1_000
            tl = float(latest.get("total_liabilities") or 0) / 1_000
        except (TypeError, ValueError):
            return None
        return {"current_assets": ca, "total_liabilities": tl} if ca or tl else None

    def get_pe_pb_stats(self, stock_no: str, years: int = 10) -> dict | None:
        rows = self._fetch("pepb", stock_no)
        if not rows:
            return None

        rows_sorted = sorted(rows, key=lambda r: r.get("date", ""))
        by_year: dict[str, list[tuple[float, float]]] = {}
        for r in rows_sorted:
            yr = str(r.get("date") or "")[:4]
            per = float(r.get("PER") or 0)
            pbr = float(r.get("PBR") or 0)
            if per > 0 and pbr > 0:
                by_year.setdefault(yr, []).append((per, pbr))

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

        # BPS: latest close ÷ latest PBR
        price_rows = self._fetch("price", stock_no)
        bps_latest: float | None = None
        if price_rows and rows_sorted:
            try:
                latest_close = float(sorted(price_rows, key=lambda r: r.get("date", ""))[-1].get("close") or 0)
                latest_pbr = float(rows_sorted[-1].get("PBR") or 0)
                if latest_close > 0 and latest_pbr > 0:
                    bps_latest = round(latest_close / latest_pbr, 2)
            except (TypeError, ValueError, IndexError):
                pass

        def _avg(lst: list[float]) -> float | None:
            return round(sum(lst) / len(lst), 4) if lst else None  # pragma: no cover

        return {
            "pe_low_avg": _avg(pe_lows),
            "pe_mid_avg": _avg(pe_mids),
            "pb_low_avg": _avg(pb_lows),
            "pb_mid_avg": _avg(pb_mids),
            "bps_latest": bps_latest,
        }

    def get_price_annual_stats(self, stock_no: str, years: int = 10) -> dict | None:
        rows = self._fetch("price", stock_no)
        if not rows:
            return None

        by_year: dict[str, dict[str, list[float]]] = {}
        for r in rows:
            yr = str(r.get("date") or "")[:4]
            if not yr:
                continue
            try:
                low   = float(r.get("min") or 0)
                close = float(r.get("close") or 0)
            except (TypeError, ValueError):
                continue
            if low > 0 and close > 0:
                y = by_year.setdefault(yr, {"lows": [], "closes": []})
                y["lows"].append(low)
                y["closes"].append(close)

        if not by_year:
            return None
        annual_lows = [min(v["lows"]) for v in by_year.values() if v["lows"]]
        annual_avgs = [sum(v["closes"]) / len(v["closes"]) for v in by_year.values() if v["closes"]]
        if not annual_lows:  # pragma: no cover
            return None
        return {
            "year_low_10y": round(sum(annual_lows) / len(annual_lows), 2),
            "year_avg_10y": round(sum(annual_avgs) / len(annual_avgs), 2),
        }

    def get_shares_outstanding(self, stock_no: str) -> float | None:
        rows = self._fetch("dividend", stock_no)
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
