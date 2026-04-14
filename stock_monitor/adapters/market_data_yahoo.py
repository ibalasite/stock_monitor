"""Yahoo Finance TW quote page scraper for near-realtime prices.

Fetches the HTML page from:
  https://tw.stock.yahoo.com/quote/{stock_no}

The page server-renders the current market price embedded as JSON inside the
HTML, which is near-realtime (within seconds of the last tick) — unlike the
v8 chart API which has a forced ~20-minute delay for unauthenticated calls.

URL format: stock_no only — no .TW/.TWO suffix needed.
exchange_map parameter is accepted for interface compatibility but not used
for URL construction.

CR-ADP-01: HTTP failures must be caught and logged; never raise to caller.
"""

from __future__ import annotations

import logging
import re
import socket
from dataclasses import dataclass
from urllib import error, request

_LOG = logging.getLogger(__name__)

MAX_RESPONSE_BYTES = 2_097_152  # 2 MB cap for HTML pages
_BASE_URL = "https://tw.stock.yahoo.com/quote"
_TIMEOUT_SEC = 15

# Regex patterns for price data embedded in the server-rendered HTML
_RE_PRICE = re.compile(r'"regularMarketPrice"\s*:(\d+\.?\d*)')
_RE_TIME = re.compile(r'"regularMarketTime"\s*:(\d+)')
_RE_NAME = re.compile(r'"longName"\s*:\s*"([^"]+)"')
# Best ask (委賣一) from the rendered order book table — preferred over regularMarketPrice.
# Falls back to regularMarketPrice when order book is absent (after-hours, suspended).
_RE_ASK = re.compile(
    r'委賣價</span><span>量</span>.*?<span[^>]*>([\d,]+(?:\.\d+)?)</span>',
    re.DOTALL,
)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


@dataclass
class YahooFinanceMarketDataProvider:
    """Fetch Taiwan stock near-realtime prices by scraping tw.stock.yahoo.com."""

    base_url: str = _BASE_URL
    timeout_sec: int = _TIMEOUT_SEC

    def get_realtime_quotes(
        self,
        stock_nos: list[str],
        exchange_map: dict[str, str] | None = None,  # kept for interface compat
    ) -> dict[str, dict]:
        if not stock_nos:
            return {}

        quotes: dict[str, dict] = {}
        for stock_no in stock_nos:
            url = f"{self.base_url}/{stock_no}"
            req = request.Request(url=url, method="GET", headers=_HEADERS)
            try:
                with request.urlopen(req, timeout=self.timeout_sec) as resp:
                    raw = resp.read(MAX_RESPONSE_BYTES)
            except (error.HTTPError, error.URLError, socket.timeout, OSError) as exc:
                _LOG.warning("Yahoo TW page request failed for %s: %s", stock_no, exc)
                continue

            html = raw.decode("utf-8", errors="replace")
            m_ask = _RE_ASK.search(html)
            m_price = _RE_PRICE.search(html)
            m_time = _RE_TIME.search(html)
            if (not m_ask and not m_price) or not m_time:
                _LOG.warning("Yahoo TW page parse failed for %s: ask/price/time not found", stock_no)
                continue

            try:
                price = (
                    float(m_ask.group(1).replace(",", ""))
                    if m_ask
                    else float(m_price.group(1))
                )
                tick_at = int(m_time.group(1))
            except (TypeError, ValueError) as exc:
                _LOG.warning("Yahoo TW page value error for %s: %s", stock_no, exc)
                continue

            m_name = _RE_NAME.search(html)
            name = m_name.group(1) if m_name else ""

            quotes[stock_no] = {
                "stock_no": stock_no,
                "price": price,
                "tick_at": tick_at,
                "name": name,
            }

        return quotes

