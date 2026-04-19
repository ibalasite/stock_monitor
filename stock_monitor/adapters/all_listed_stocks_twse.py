"""TWSE/TPEx adapter: fetch all listed/OTC ordinary stocks.

FR-19: 全市場估值掃描 — 股票清單來源。
Data sources:
  - TSE:  https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY_ALL?response=json
  - TPEx: https://www.tpex.org.tw/openapi/v1/tpex_stk_closingprice
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from urllib import error, request

try:
    import truststore
    truststore.inject_into_ssl()  # pragma: no cover
except ImportError:  # pragma: no cover
    pass  # truststore not installed; fall back to default SSL

_MAX_RESPONSE_BYTES = 1_048_576  # 1 MB  (CR-SEC-04 parity)
_TIMEOUT_SEC = 30
_MAX_RETRIES = 3

# Regex: exactly 4 ASCII digits (ordinary stock codes in Taiwan)
_VALID_CODE_RE = re.compile(r"^\d{4}$")

# Keywords that disqualify a stock name (ETFs, warrants, DRs, etc.)
_EXCLUDE_KEYWORDS = ("ETF", "存託憑證", "DR", "認購", "認售", "牛", "熊")


def _is_ordinary_stock(stock_no: str, stock_name: str) -> bool:
    """Return True if the stock should be included (ordinary stock, not ETF/DR/warrant)."""
    if not _VALID_CODE_RE.match(stock_no.strip()):
        return False
    name_upper = stock_name.upper()
    for kw in _EXCLUDE_KEYWORDS:
        if kw.upper() in name_upper:
            return False
    return True


def _to_float_price(raw: str | None) -> float | None:
    """Parse a price string (may contain commas) to float. Return None if not parsable."""
    if raw is None:
        return None
    text = str(raw).strip().replace(",", "")
    if not text or text == "-":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _http_get(url: str) -> bytes:
    """HTTP GET with retry (up to _MAX_RETRIES). Raises on exhaustion."""
    req = request.Request(
        url=url,
        method="GET",
        headers={"User-Agent": "stock-monitor/1.0 (market-scan)"},
    )
    last_exc: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            with request.urlopen(req, timeout=_TIMEOUT_SEC) as resp:
                return resp.read(_MAX_RESPONSE_BYTES)
        except (error.URLError, OSError) as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES:
                time.sleep(0.5 * attempt)
    raise RuntimeError(
        f"HTTP GET failed after {_MAX_RETRIES} attempts: {url} — {last_exc}"
    ) from last_exc


def _fetch_twse_stocks() -> list[dict]:
    """Fetch TSE ordinary stocks using the STOCK_DAY_ALL JSON endpoint."""
    url = "https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY_ALL?response=json"
    raw = _http_get(url)
    payload = json.loads(raw.decode("utf-8"))
    # Expected shape: {"stat": "OK", "fields": [...], "data": [[...], ...]}
    if not isinstance(payload, dict):
        raise RuntimeError("TWSE STOCK_DAY_ALL: unexpected response format")
    data = payload.get("data") or []
    fields = payload.get("fields") or []

    # Locate column indices
    try:
        idx_code = fields.index("證券代號")
        idx_name = fields.index("證券名稱")
        idx_close = fields.index("收盤價")
    except ValueError:
        # Fallback: assume columns 0,1,8 (common STOCK_DAY_ALL layout)
        idx_code, idx_name, idx_close = 0, 1, 8

    result: list[dict] = []
    for row in data:
        try:
            code = str(row[idx_code]).strip()
            name = str(row[idx_name]).strip()
            close_raw = str(row[idx_close]).strip() if len(row) > idx_close else None
        except (IndexError, TypeError):
            continue
        if not _is_ordinary_stock(code, name):
            continue
        result.append({
            "stock_no": code,
            "stock_name": name,
            "yesterday_close": _to_float_price(close_raw),
            "market": "TWSE",
        })
    return result


def _fetch_tpex_stocks() -> list[dict]:
    """Fetch TPEx ordinary stocks using the openapi closingprice endpoint."""
    url = "https://www.tpex.org.tw/openapi/v1/tpex_stk_closingprice"
    raw = _http_get(url)
    payload = json.loads(raw.decode("utf-8"))

    if not isinstance(payload, list):
        raise RuntimeError("TPEx closingprice: unexpected response format")

    result: list[dict] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        code = str(item.get("SecuritiesCompanyCode") or item.get("Code") or "").strip()
        name = str(item.get("CompanyName") or item.get("Name") or "").strip()
        close_raw = item.get("Close") or item.get("ClosingPrice")
        if not _is_ordinary_stock(code, name):
            continue
        result.append({
            "stock_no": code,
            "stock_name": name,
            "yesterday_close": _to_float_price(str(close_raw) if close_raw is not None else None),
            "market": "TPEx",
        })
    return result


@dataclass
class TwseAllListedStocksProvider:
    """Fetch all listed (TSE + OTC) ordinary stocks for FR-19 market-wide scan.

    Implements AllListedStocksPort (EDD §14.1).
    Raises RuntimeError if all HTTP retries fail for both sources.
    """

    _stocks_cache: list[dict] = field(default_factory=list, repr=False)

    def get_all_listed_stocks(self) -> list[dict]:
        """Return combined TSE + TPEx ordinary stock list.

        Each dict contains:
            stock_no: str           — 4-digit code
            stock_name: str         — Chinese name
            yesterday_close: float | None — previous close (None if unavailable)
            market: str             — 'TWSE' | 'TPEx'

        Raises:
            RuntimeError: if TWSE source fails (primary; all retries exhausted).
                          TPEx failure is tolerated (partial result returned).
        """
        twse_stocks = _fetch_twse_stocks()  # raises on failure
        if not twse_stocks:
            raise RuntimeError(
                "TwseAllListedStocksProvider: TWSE returned empty stock list."
            )

        try:
            tpex_stocks = _fetch_tpex_stocks()
        except Exception:
            tpex_stocks = []

        combined = twse_stocks + tpex_stocks
        self._stocks_cache = combined
        return combined
