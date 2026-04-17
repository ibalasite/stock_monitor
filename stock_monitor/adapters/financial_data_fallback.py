"""Financial data provider — parallel three-source (P1/P2/P3) or sequential fallback.

ParallelFinancialDataProvider (FR-21 / CR-FIN-02):
  All three providers are fired simultaneously via ThreadPoolExecutor.
  After all complete (or TIMEOUT_SEC), compare financial_data_cache.fetched_at
  and return the result from whichever provider has the most recent cache entry.
  If every provider raises ProviderUnavailableError → re-raise.

FallbackFinancialDataProvider (legacy):
  Sequential P1→P2→P3 chain kept for reference / migration path.
  Do NOT use for new valuation code; use ParallelFinancialDataProvider instead.

EDD §9.1 / ADR-016 / ADR-018.
"""

from __future__ import annotations

import logging
import sqlite3
from concurrent.futures import ThreadPoolExecutor, wait as _futures_wait

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


class ParallelFinancialDataProvider:
    """Fires all providers simultaneously and returns the freshest cached result.

    Per FR-21 / CR-FIN-02: three sources (FinMind P1, MOPS P2, Goodinfo P3)
    must execute concurrently — not as a sequential fallback chain.

    After all futures complete (or TIMEOUT_SEC elapses), each successful
    provider's financial_data_cache.fetched_at is queried; the result from
    the provider with the highest fetched_at wins.  If all providers raise
    ProviderUnavailableError, re-raise it to the caller.
    """

    TIMEOUT_SEC: int = 60

    def __init__(self, providers: list) -> None:
        if not providers:
            raise ValueError("ParallelFinancialDataProvider requires at least one provider")
        self._providers = list(providers)

    @classmethod
    def default(
        cls,
        db_path: str | None = None,
        stale_days: int = 15,
    ) -> "ParallelFinancialDataProvider":
        """Construct the standard P1+P2+P3 parallel provider."""
        from stock_monitor.adapters.financial_data_finmind import FinMindFinancialDataProvider
        from stock_monitor.adapters.financial_data_mops import MopsTwseAdapter
        from stock_monitor.adapters.financial_data_goodinfo import GoodinfoAdapter

        return cls([
            FinMindFinancialDataProvider(db_path=db_path, stale_days=stale_days),
            MopsTwseAdapter(db_path=db_path, stale_days=stale_days),
            GoodinfoAdapter(db_path=db_path, stale_days=stale_days),
        ])

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _provider_fetched_at(self, provider: object, stock_no: str) -> int:
        """Return the most recent fetched_at timestamp for this provider/stock."""
        db_path = getattr(provider, "_db_path", None)
        provider_name = getattr(provider, "provider_name", None)
        if not db_path or not provider_name:
            return 0
        try:
            with sqlite3.connect(db_path, timeout=5) as conn:
                row = conn.execute(
                    "SELECT MAX(fetched_at) FROM financial_data_cache"
                    " WHERE provider=? AND stock_no=?",
                    (provider_name, str(stock_no)),
                ).fetchone()
            return int(row[0]) if row and row[0] is not None else 0
        except Exception:
            return 0

    def _call_parallel(self, method: str, stock_no: str, **kwargs):  # type: ignore[return]
        """Fire all providers simultaneously; return freshest result by fetched_at."""
        executor = ThreadPoolExecutor(max_workers=len(self._providers))
        futures_map: dict = {}
        try:
            for p in self._providers:
                f = executor.submit(getattr(p, method), stock_no, **kwargs)
                futures_map[f] = p
            done, _ = _futures_wait(list(futures_map.keys()), timeout=self.TIMEOUT_SEC)
        finally:
            executor.shutdown(wait=False)

        # Separate results into three buckets:
        #   valid_hits  — provider returned a non-None value (has data)
        #   any_no_data — at least one provider explicitly said "no data" (None)
        #   unavailable — provider raised ProviderUnavailableError (temporarily down)
        #
        # Semantics (EDD §9.4.6):
        #   • Only non-None results participate in fetched_at comparison.
        #   • None = stock genuinely has no data at this provider — skip, don't override
        #     a valid result from another provider that happens to have an older cache.
        #   • All valid results absent + any None seen → return None (no data anywhere).
        #   • All providers raised → raise ProviderUnavailableError.
        valid_hits: list[tuple[int, object]] = []
        any_no_data = False
        for f in done:
            p = futures_map[f]
            name = getattr(p, "provider_name", type(p).__name__)
            try:
                result = f.result()
                if result is None:
                    any_no_data = True
                    logger.debug(
                        "parallel_financial: provider %s has no data for %s/%s",
                        name, method, stock_no,
                    )
                else:
                    ts = self._provider_fetched_at(p, stock_no)
                    valid_hits.append((ts, result))
            except ProviderUnavailableError:
                logger.debug(
                    "parallel_financial: provider %s unavailable for %s/%s",
                    name, method, stock_no,
                )
            except Exception as exc:
                logger.warning(
                    "parallel_financial: provider %s raised unexpected error for %s/%s: %s",
                    name, method, stock_no, exc,
                )

        if valid_hits:
            # Return the result from whichever provider has the freshest cache.
            valid_hits.sort(key=lambda x: x[0], reverse=True)
            return valid_hits[0][1]

        if any_no_data:
            # Every available provider confirmed no data for this stock.
            return None

        raise ProviderUnavailableError(
            f"all providers unavailable for {method}/{stock_no}"
        )

    # ------------------------------------------------------------------
    # Public data methods (mirrors FinancialDataPort)
    # ------------------------------------------------------------------

    def get_avg_dividend(self, stock_no: str, years: int = 5) -> float | None:
        return self._call_parallel("get_avg_dividend", stock_no, years=years)

    def get_eps_data(self, stock_no: str, years: int = 10) -> dict | None:
        return self._call_parallel("get_eps_data", stock_no, years=years)

    def get_balance_sheet_data(self, stock_no: str) -> dict | None:
        return self._call_parallel("get_balance_sheet_data", stock_no)

    def get_pe_pb_stats(self, stock_no: str, years: int = 10) -> dict | None:
        return self._call_parallel("get_pe_pb_stats", stock_no, years=years)

    def get_price_annual_stats(self, stock_no: str, years: int = 10) -> dict | None:
        return self._call_parallel("get_price_annual_stats", stock_no, years=years)

    def get_shares_outstanding(self, stock_no: str) -> float | None:
        return self._call_parallel("get_shares_outstanding", stock_no)
