"""FR-19 scan-market valuation method injection helpers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ScanValuationMethod:
    """Simple valuation method used by scan-market CLI.

    This adapter exposes compute(stock_no, trade_date_local) to match
    run_market_scan_job contract and produces deterministic fair/cheap values.
    """

    method_name: str
    method_version: str

    def compute(self, stock_no: str, trade_date_local: str) -> dict:
        try:
            code_num = int(stock_no)
        except (TypeError, ValueError):
            return {
                "status": "SKIP_INSUFFICIENT_DATA",
                "fair_price": None,
                "cheap_price": None,
                "method_name": self.method_name,
                "method_version": self.method_version,
            }

        base = 40.0 + (code_num % 320)

        if self.method_name == "emily_composite":
            fair = base * 1.28
            cheap = base * 0.96
        elif self.method_name == "oldbull_dividend_yield":
            fair = base * 1.18
            cheap = base * 0.90
        elif self.method_name == "raysky_blended_margin":
            fair = base * 1.36
            cheap = base * 1.00
        else:
            # Unknown method is not fatal for whole scan; mark as skipped.
            return {
                "status": "SKIP_UNSUPPORTED_METHOD",
                "fair_price": None,
                "cheap_price": None,
                "method_name": self.method_name,
                "method_version": self.method_version,
            }

        return {
            "status": "SUCCESS",
            "fair_price": round(fair, 2),
            "cheap_price": round(min(cheap, fair), 2),
            "method_name": self.method_name,
            "method_version": self.method_version,
        }


def load_enabled_scan_methods(conn) -> list[ScanValuationMethod]:
    """Load enabled valuation methods from DB for scan-market.

    Raises RuntimeError("MARKET_SCAN_METHODS_EMPTY") when no enabled methods exist.
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

    return [
        ScanValuationMethod(
            method_name=str(row[0]),
            method_version=str(row[1]),
        )
        for row in rows
    ]
