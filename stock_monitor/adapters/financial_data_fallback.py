"""Fallback financial data provider — chains P1 → P2 → P3.

Per-method fallback logic:
  1. Call provider P1.
     - Returns value (float/dict) → use it, done.
     - Returns None              → stock genuinely has no data; stop, return None.
     - Raises ProviderUnavailableError → P1 is rate-limited or down; try P2.
  2. Call provider P2 (same logic).
  3. Call provider P3 (same logic).
  4. All three raised ProviderUnavailableError → return None (all providers down).

Each provider maintains its own independent SWR cache. When P1 is rate-limited:
  - P1 has no cache for this stock → ProviderUnavailableError → P2 serves.
  - P1 has stale cache → returns stale data (no error), P1 is still useful.
  - P2 builds its own cache from MOPS/TWSE.
  - When P1 recovers, it populates its own cache; future calls hit P1 cache again.

EDD §9.1 / ADR-016: this class is the single injection point for valuation methods.
"""

from __future__ import annotations

import logging

from stock_monitor.adapters.financial_data_port import (
    FinancialDataPort,
    ProviderUnavailableError,
)

logger = logging.getLogger(__name__)


class FallbackFinancialDataProvider:
    """Chains multiple FinancialDataPort providers with per-method fallback.

    Usage:
        from stock_monitor.adapters.financial_data_finmind import FinMindFinancialDataProvider
        from stock_monitor.adapters.financial_data_mops import MopsTwseAdapter
        from stock_monitor.adapters.financial_data_goodinfo import GoodinfoAdapter
        from stock_monitor.adapters.financial_data_fallback import FallbackFinancialDataProvider

        provider = FallbackFinancialDataProvider.default(db_path="data/stock_monitor.db")
    """

    def __init__(self, providers: list[FinancialDataPort]) -> None:
        if not providers:
            raise ValueError("FallbackFinancialDataProvider requires at least one provider")
        self._providers = providers

    @classmethod
    def default(
        cls,
        db_path: str | None = None,
        stale_days: int = 15,
    ) -> "FallbackFinancialDataProvider":
        """Construct the standard P1→P2→P3 chain with default settings."""
        from stock_monitor.adapters.financial_data_finmind import FinMindFinancialDataProvider
        from stock_monitor.adapters.financial_data_mops import MopsTwseAdapter
        from stock_monitor.adapters.financial_data_goodinfo import GoodinfoAdapter

        return cls([
            FinMindFinancialDataProvider(db_path=db_path, stale_days=stale_days),
            MopsTwseAdapter(db_path=db_path, stale_days=stale_days),
            GoodinfoAdapter(db_path=db_path, stale_days=stale_days),
        ])

    # ------------------------------------------------------------------
    # Internal dispatch helper
    # ------------------------------------------------------------------

    def _call(self, method: str, stock_no: str, **kwargs):  # type: ignore[return]
        """Try each provider in order, falling back on ProviderUnavailableError."""
        last_exc: ProviderUnavailableError | None = None
        for provider in self._providers:
            name = getattr(provider, "provider_name", type(provider).__name__)
            try:
                result = getattr(provider, method)(stock_no, **kwargs)
                # Successful call (result may be None = genuine no data).
                if last_exc is not None:
                    # Log that we fell back
                    logger.debug(
                        "financial_data fallback: %s/%s served by %s after earlier failure",
                        method,
                        stock_no,
                        name,
                    )
                return result
            except ProviderUnavailableError as exc:
                last_exc = exc
                logger.debug(
                    "financial_data fallback: %s unavailable for %s/%s (%s), trying next",
                    name,
                    method,
                    stock_no,
                    exc,
                )
                continue

        # All providers raised ProviderUnavailableError
        logger.warning(
            "financial_data fallback: all providers failed for %s/%s — returning None",
            method,
            stock_no,
        )
        return None

    # ------------------------------------------------------------------
    # Public data methods (mirrors FinancialDataPort)
    # ------------------------------------------------------------------

    def get_avg_dividend(self, stock_no: str, years: int = 5) -> float | None:
        return self._call("get_avg_dividend", stock_no, years=years)

    def get_eps_data(self, stock_no: str, years: int = 10) -> dict | None:
        return self._call("get_eps_data", stock_no, years=years)

    def get_balance_sheet_data(self, stock_no: str) -> dict | None:
        return self._call("get_balance_sheet_data", stock_no)

    def get_pe_pb_stats(self, stock_no: str, years: int = 10) -> dict | None:
        return self._call("get_pe_pb_stats", stock_no, years=years)

    def get_price_annual_stats(self, stock_no: str, years: int = 10) -> dict | None:
        return self._call("get_price_annual_stats", stock_no, years=years)

    def get_shares_outstanding(self, stock_no: str) -> float | None:
        return self._call("get_shares_outstanding", stock_no)
