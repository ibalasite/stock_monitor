"""FR-19 scan-market valuation method injection helpers.

Methods use real financial data fetched live from FinMind API.
No DB snapshot lookup — every call recomputes from upstream data.
EDD §14.7.7/8: actual computation, not pre-computed results.
"""

from __future__ import annotations

from stock_monitor.adapters.financial_data_finmind import FinMindFinancialDataProvider
from stock_monitor.application.valuation_methods_real import (
    EmilyCompositeV1,
    OldbullDividendYieldV1,
    RayskyBlendedMarginV1,
)

# Registry: DB method_name/version → real method class
_METHOD_REGISTRY: dict[tuple[str, str], type] = {
    ("emily_composite", "v1"): EmilyCompositeV1,
    ("oldbull_dividend_yield", "v1"): OldbullDividendYieldV1,
    ("raysky_blended_margin", "v1"): RayskyBlendedMarginV1,
}


def load_enabled_scan_methods(
    conn,
    as_of_date: str,  # noqa: ARG001
    db_path: str | None = None,
) -> list:
    """Load enabled valuation methods from DB for scan-market.

    Reads which methods are enabled from valuation_methods table,
    then returns real-computation method instances backed by FinMind API.

    as_of_date is accepted for interface compatibility but not used —
    real methods always fetch the freshest available data.

    db_path is forwarded to FinMindFinancialDataProvider so the SWR cache
    writes to the same SQLite file as the rest of the application.

    Raises RuntimeError("MARKET_SCAN_METHODS_EMPTY") when no enabled methods
    are registered in the DB or none match the known registry.
    """
    rows = conn.execute(
        """
        SELECT method_name, method_version
        FROM valuation_methods
        WHERE enabled = 1
        ORDER BY method_name, method_version
        """
    ).fetchall()

    if not rows:
        raise RuntimeError("MARKET_SCAN_METHODS_EMPTY")

    provider = FinMindFinancialDataProvider(db_path=db_path)
    methods: list = []

    for row in rows:
        name, version = str(row[0]), str(row[1])
        cls = _METHOD_REGISTRY.get((name, version))
        if cls is not None:
            methods.append(cls(provider=provider))

    if not methods:
        raise RuntimeError("MARKET_SCAN_METHODS_EMPTY")

    return methods
