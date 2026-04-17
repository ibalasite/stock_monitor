"""Financial data provider protocol and shared errors.

All financial data adapters (FinMind, MOPS+TWSE, Goodinfo) implement this protocol.

Return conventions (enforced across all adapters):
    float / dict — success (may come from SWR cache).
    None         — stock genuinely has no data for this method (e.g. ETF has no EPS).
                   The caller should accept this as the definitive answer and NOT
                   try the next provider — the data simply does not exist.
    raises ProviderUnavailableError — transient failure: rate limit, network error,
                   parse failure, or any other condition that means "try again later".
                   The FallbackFinancialDataProvider catches this and tries the next adapter.

SWR cache contract per adapter:
    - Fresh hit  → return from cache, no upstream call.
    - Stale hit  → return stale data immediately, spawn background refresh.
    - Cache miss → call upstream; if succeeds store in cache and return;
                   if upstream fails raise ProviderUnavailableError (do NOT cache).
"""

from __future__ import annotations

from typing import Protocol


class ProviderUnavailableError(Exception):
    """Transient upstream failure: rate limit, network error, parse error.

    The FallbackFinancialDataProvider catches this and tries the next adapter.
    Do NOT raise this when a stock genuinely has no data — return None instead.
    """


class FinancialDataPort(Protocol):
    """Interface that every financial data adapter must satisfy."""

    def get_avg_dividend(self, stock_no: str, years: int = 5) -> float | None:
        """Average annual cash dividend per share over the last N fiscal years.
        Returns None if no dividend history found (e.g. never paid dividends).
        Raises ProviderUnavailableError on transient failure.
        """

    def get_eps_data(self, stock_no: str, years: int = 10) -> dict | None:
        """EPS metrics: eps_ttm (trailing 12-month) and eps_10y_avg.
        Returns None if fewer than 4 quarters available.
        Raises ProviderUnavailableError on transient failure.
        """

    def get_balance_sheet_data(self, stock_no: str) -> dict | None:
        """Latest balance sheet: current_assets and total_liabilities (NT$ thousands).
        Returns None if data unavailable.
        Raises ProviderUnavailableError on transient failure.
        """

    def get_pe_pb_stats(self, stock_no: str, years: int = 10) -> dict | None:
        """Annual PE/PB stats: pe_low_avg, pe_mid_avg, pb_low_avg, pb_mid_avg, bps_latest.
        Returns None if fewer than 1 year of valid data.
        Raises ProviderUnavailableError on transient failure.
        """

    def get_price_annual_stats(self, stock_no: str, years: int = 10) -> dict | None:
        """year_low_10y (avg of annual lows) and year_avg_10y (avg of annual closes).
        Returns None if no price data found.
        Raises ProviderUnavailableError on transient failure.
        """

    def get_shares_outstanding(self, stock_no: str) -> float | None:
        """Shares participating in most recent dividend (proxy for outstanding shares).
        Returns None if no dividend data found.
        Raises ProviderUnavailableError on transient failure.
        """
