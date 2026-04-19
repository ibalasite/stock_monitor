"""FR-19 scan-market valuation method injection helpers.

Methods use real financial data fetched via individual three-source providers
(FinMind P1, MOPS+TWSE P2, Goodinfo P3) fired independently.  For each
enabled method the valuation is computed three times — once per provider —
and the minimum fair/cheap price is returned (ConservativeMultiSourceMethod).
This produces the most conservative signal threshold, reducing false positives
during intraday monitoring.

EDD §14.7.7/8: actual computation, not pre-computed results.
"""

from __future__ import annotations

from stock_monitor.adapters.financial_data_finmind import FinMindFinancialDataProvider
from stock_monitor.adapters.financial_data_goodinfo import GoodinfoAdapter
from stock_monitor.adapters.financial_data_mops import MopsTwseAdapter
from stock_monitor.application.valuation_methods_real import (
    ConservativeMultiSourceMethod,
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

    Reads which methods are enabled from valuation_methods table, then returns
    ConservativeMultiSourceMethod instances that run each method against P1, P2,
    and P3 independently and return the minimum fair/cheap price.

    as_of_date is accepted for interface compatibility but not used —
    real methods always fetch the freshest available data.

    db_path is forwarded to the three individual providers so the SWR cache
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

    p1 = FinMindFinancialDataProvider(db_path=db_path)
    p2 = MopsTwseAdapter(db_path=db_path)
    p3 = GoodinfoAdapter(db_path=db_path)
    providers = [p1, p2, p3]

    methods: list = []
    for row in rows:
        name, version = str(row[0]), str(row[1])
        cls = _METHOD_REGISTRY.get((name, version))
        if cls is not None:
            methods.append(ConservativeMultiSourceMethod(cls, providers))

    if not methods:
        raise RuntimeError("MARKET_SCAN_METHODS_EMPTY")

    return methods
