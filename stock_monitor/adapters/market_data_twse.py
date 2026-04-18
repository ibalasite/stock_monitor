"""TWSE MIS public endpoint adapter for realtime quotes."""

from __future__ import annotations

import json
import socket
from dataclasses import dataclass
from urllib import error, parse, request

try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:  # pragma: no cover
    pass  # truststore not installed; fall back to default SSL

MAX_RESPONSE_BYTES = 1_048_576  # 1 MB cap to prevent memory exhaustion (CR-SEC-04)


def _to_float(value: str | None) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == "-":
        return None
    try:
        return float(text)
    except ValueError:
        return None


@dataclass
class TwseRealtimeMarketDataProvider:
    """Read Taiwan stock realtime data from TWSE MIS endpoint."""

    base_url: str = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
    index_channel: str = "tse_t00.tw"
    timeout_sec: int = 10

    def __post_init__(self) -> None:
        # Cache the last known best ask price (委賣一, a field) per stock across polling
        # cycles. When a='-' or absent (snapshot between ticks or no order book), we
        # return the cached ask price instead of an approximate fallback.
        self._price_cache: dict[str, float] = {}
        # Cache the exchange type (tse/otc) per stock from the ex field.
        self._exchange_cache: dict[str, str] = {}
        # Cache the last tick_at timestamp per stock.
        self._tick_cache: dict[str, int] = {}
        # FR-18: cache stock Chinese names from API response; names are NOT included
        # in get_realtime_quotes() return dict — use get_stock_names() to retrieve.
        self._name_cache: dict[str, str] = {}

    def _build_stock_channels(self, stock_nos: list[str]) -> tuple[list[str], list[str]]:
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in stock_nos:
            stock_no = str(raw).strip()
            if not stock_no or stock_no in seen:
                continue
            seen.add(stock_no)
            normalized.append(stock_no)

        channels: list[str] = []
        for stock_no in normalized:
            channels.append(f"tse_{stock_no}.tw")
            channels.append(f"otc_{stock_no}.tw")
        return normalized, channels

    def _build_url(self, channels: list[str]) -> str:
        query = parse.urlencode({"ex_ch": "|".join(channels), "json": "1", "delay": "0"})
        return f"{self.base_url}?{query}"

    def _http_get_json(self, url: str) -> dict:
        req = request.Request(url=url, method="GET", headers={"User-Agent": "stock-monitor/1.0"})
        try:
            with request.urlopen(req, timeout=self.timeout_sec) as resp:
                payload = resp.read(MAX_RESPONSE_BYTES).decode("utf-8")
        except socket.timeout as exc:
            raise TimeoutError("market data timeout") from exc
        except error.URLError as exc:
            if isinstance(exc.reason, socket.timeout):
                raise TimeoutError("market data timeout") from exc
            raise RuntimeError(f"market data request failed: {exc}") from exc
        return json.loads(payload)

    def _fetch_channels(self, channels: list[str]) -> list[dict]:
        data = self._http_get_json(self._build_url(channels))
        msg_array = data.get("msgArray")
        if not isinstance(msg_array, list):
            raise RuntimeError("invalid market response")
        return msg_array

    def get_market_snapshot(self, now_epoch: int) -> dict:
        rows = self._fetch_channels([self.index_channel])
        if not rows:
            raise RuntimeError("index not found")
        row = rows[0]
        tlong = row.get("tlong")
        try:
            tick_epoch = int(str(tlong)) // 1000
        except (TypeError, ValueError):
            raise RuntimeError("index tick timestamp unavailable")
        price = _to_float(row.get("z")) or _to_float(row.get("y")) or 0.0
        return {
            "source": "twse_mis",
            "index_channel": self.index_channel,
            "index_price": price,
            "index_tick_at": tick_epoch,
            "fetched_at": now_epoch,
        }

    def get_realtime_quotes(self, stock_nos: list[str]) -> dict[str, dict]:
        if not stock_nos:
            return {}
        normalized_stock_nos, channels = self._build_stock_channels(stock_nos)
        requested = set(normalized_stock_nos)
        rows = self._fetch_channels(channels)
        quotes: dict[str, dict] = {}
        for row in rows:
            stock_no = str(row.get("c") or "").strip()
            if not stock_no:
                continue
            if stock_no not in requested:
                continue
            ask_raw = str(row.get("a") or "").split("_")[0].strip()
            price = _to_float(ask_raw)
            if price is not None:
                # Fresh ask — update cache with the best ask price (委賣一).
                self._price_cache[stock_no] = price
            else:
                # a='-' or absent: no order book at this snapshot instant.
                # Use the last known ask price from cache if available.
                # If cache is cold (first poll), seed with yesterday's close (y)
                # so Composite never needs to fallback to delayed external sources.
                if stock_no not in self._price_cache:
                    yesterday_close = _to_float(row.get("y"))
                    if yesterday_close is not None:
                        self._price_cache[stock_no] = yesterday_close
                price = self._price_cache.get(stock_no)
            if price is None:
                continue
            try:
                tick_epoch = int(str(row.get("tlong"))) // 1000
            except (TypeError, ValueError):
                tick_epoch = 0

            exchange = str(row.get("ex") or "").strip()
            if exchange:
                self._exchange_cache[stock_no] = exchange
            else:
                exchange = self._exchange_cache.get(stock_no, "")

            self._tick_cache[stock_no] = tick_epoch

            # FR-18: cache name from API; do NOT include in quote dict (names from DB only)
            raw_name = str(row.get("n") or "").strip()
            if raw_name:
                self._name_cache[stock_no] = raw_name

            existing = quotes.get(stock_no)
            existing_tick = int(existing["tick_at"]) if existing else -1
            if existing is not None and tick_epoch < existing_tick:
                continue

            quotes[stock_no] = {
                "stock_no": stock_no,
                "price": price,
                "tick_at": tick_epoch,
                "exchange": exchange,
            }
        return quotes

    def get_stock_names(self, stock_nos: list[str]) -> dict[str, str]:
        """FR-18: Return cached stock Chinese names (populated during get_realtime_quotes).
        Names are NOT included in get_realtime_quotes() return dict.
        """
        return {sno: self._name_cache[sno] for sno in stock_nos if sno in self._name_cache}
