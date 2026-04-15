"""Composite market data provider: Freshness-First merge of TWSE + Yahoo Finance.

ADR-014: Compare tick_at from both sources; take the newer one.
Tie-break: TWSE wins. If TWSE has no data, use Yahoo. If both empty, omit stock.

CR-ADP-02: Must perform Freshness-First comparison; never return a source dict directly.
"""

from __future__ import annotations

from dataclasses import dataclass

from stock_monitor.adapters.market_data_twse import TwseRealtimeMarketDataProvider
from stock_monitor.adapters.market_data_yahoo import YahooFinanceMarketDataProvider


@dataclass
class CompositeMarketDataProvider:
    """Freshness-First composite of TWSE (primary) and Yahoo Finance (secondary)."""

    primary: TwseRealtimeMarketDataProvider
    secondary: YahooFinanceMarketDataProvider

    def get_realtime_quotes(self, stock_nos: list[str]) -> dict[str, dict]:
        if not stock_nos:
            return {}

        # Build exchange_map for Yahoo from primary's _exchange_cache
        exchange_map: dict[str, str] = dict(getattr(self.primary, "_exchange_cache", {}))

        twse_quotes = self.primary.get_realtime_quotes(stock_nos)
        # Refresh exchange_map after TWSE call (cache may have been updated)
        exchange_map.update(getattr(self.primary, "_exchange_cache", {}))

        yahoo_quotes = self.secondary.get_realtime_quotes(
            stock_nos, exchange_map=exchange_map
        )

        result: dict[str, dict] = {}
        for stock_no in stock_nos:
            twse = twse_quotes.get(stock_no)
            yahoo = yahoo_quotes.get(stock_no)

            if twse is None and yahoo is None:
                continue
            elif twse is None:
                result[stock_no] = yahoo  # type: ignore[assignment]
            elif yahoo is None:
                result[stock_no] = twse
            else:
                # Freshness-First: take newer tick_at; tie → TWSE wins
                twse_tick = int(twse.get("tick_at") or 0)
                yahoo_tick = int(yahoo.get("tick_at") or 0)
                result[stock_no] = twse if twse_tick >= yahoo_tick else yahoo

        return result

    def get_market_snapshot(self, now_epoch: int) -> dict:
        """Delegate market snapshot entirely to primary (TWSE)."""
        return self.primary.get_market_snapshot(now_epoch)

    def get_stock_names(self, stock_nos: list[str]) -> dict[str, str]:
        """FR-18: Return cached stock names, preferring primary (TWSE) over secondary (Yahoo)."""
        names: dict[str, str] = {}
        if hasattr(self.secondary, "get_stock_names"):
            names.update(self.secondary.get_stock_names(stock_nos))
        if hasattr(self.primary, "get_stock_names"):
            names.update(self.primary.get_stock_names(stock_nos))
        return names
